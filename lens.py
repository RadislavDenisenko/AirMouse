"""Zoom lens — a rounded window that follows the cursor and magnifies it.

From the couch the screen is legible but its detail is not: you can steer
the cursor onto a button you cannot actually read. The lens fixes the
reading half of that. While the hand is driving the cursor, slowing down
makes a magnified window bloom around the pointer; speeding up fades it
away, because fast motion means travel, not aiming. Squeezing the
precision brake — an explicit "I'm aiming" — brings it on regardless of
speed.

Two layers, deliberately separated:

  * `LensModel` is pure arithmetic: cursor samples in, an (alpha, size,
    window rect, source rect) frame out. Every behaviour — the speed
    mapping, the asymmetric ease, the dwell gate, the edge clamps — lives
    here, where the test suite can drive it with synthetic cursors.
  * `LensWindow` is the Win32 shell: a click-through layered popup hosting
    a Windows Magnification API control on its own 60Hz thread, so the
    30fps camera loop never waits on it. The compositor does the actual
    magnifying; Python only moves rectangles.

The feel: one "aim confidence" value u drives everything. Opacity leads
(smoothstep over u 0.20..0.70) and size trails (86%..100% across all of
u), so the entrance reads as macOS's scale-up-and-fade-in without any
keyframes, and a shrinking lens is always already fading. Getting out of
the way is fast (~60ms time constant) and appearing is deliberate
(~220ms, plus a short calm-dwell before it may bloom at all) — the same
asymmetry as v3.8's "every accidental trigger deliberate" pass, applied
to pixels. Zoom itself NEVER ramps with speed: re-zooming content under a
moving cursor makes the world breathe. Size and opacity carry all the
motion.
"""

import ctypes
import math
import threading
import time
from ctypes import wintypes

# --------------------------------------------------------------- the model ---

def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def smoothstep(lo, hi, x):
    """0..1 with soft shoulders; degrades to a step if the band is empty."""
    if hi <= lo:
        return 0.0 if x < lo else 1.0
    t = _clamp((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def lens_rects(cx, cy, w, h, zoom, mon):
    """(window_rect, source_rect) for a w x h lens over the cursor.

    THE invariant of this function: the magnified pixel directly under
    the real cursor is ALWAYS the point the cursor is truly on. The
    window clamps to the monitor like any window; the source is then
    DERIVED from it through the cursor — src = c - (c - win)/zoom per
    axis — instead of being centred independently. Centring both
    separately only agrees at the exact screen centre; everywhere else
    (worst at edges, where tab close buttons live, and worse the bigger
    the lens) the picture under the cursor came from a nearby-but-wrong
    point, so clicks landed below where the eye aimed.

    Because the window fits the monitor and the map contracts toward the
    cursor, the derived source always fits the monitor too (up to
    rounding). `mon` is (l, t, r, b) in the cursor's pixel space."""
    ml, mt, mr, mb = mon
    # A lens larger than the monitor cannot both fit and stay truthful;
    # shrink it to the monitor (the model never asks for more — this is
    # a guard for direct callers).
    w = max(2, min(int(w), mr - ml))
    h = max(2, min(int(h), mb - mt))
    zoom = max(1.0, zoom)
    src_w = max(2, int(round(w / zoom)))
    src_h = max(2, int(round(h / zoom)))
    wl = int(_clamp(cx - w // 2, ml, max(ml, mr - w)))
    wt = int(_clamp(cy - h // 2, mt, max(mt, mb - h)))
    sl = int(round(cx - (cx - wl) / zoom))
    st = int(round(cy - (cy - wt) / zoom))
    sl = int(_clamp(sl, ml, max(ml, mr - src_w)))    # rounding guard only
    st = int(_clamp(st, mt, max(mt, mb - src_h)))
    return (wl, wt, wl + w, wt + h), (sl, st, sl + src_w, st + src_h)


class LensModel:
    """Pure speed→presence arithmetic. Feed it cursor samples, read frames.

    Tunables mirror the config block; everything else is a named constant
    below because exposing it would be bloat, not power."""

    SPEED_TAU = 0.08      # EMA on the raw speed estimate (s)
    SHOW_TAU = 0.22       # ease toward visible: deliberate
    HIDE_TAU = 0.06       # ease toward hidden: get out of the way
    DWELL_S = 0.12        # calm required before blooming from hidden
    ALPHA_BAND = (0.20, 0.70)   # u range over which opacity rises
    SIZE_FLOOR = 0.86     # size at u=0, as a share of full size
    HIDE_ALPHA = 12       # below this the window is simply hidden
    REF_H = 1440.0        # speeds are quoted at this short-side, in px
    ASPECT = 4.0 / 3.0    # labels are horizontal; a reading lens is wide

    def __init__(self, enabled=True, zoom=2.5, size_frac=0.22,
                 aim_speed=180.0, travel_speed=950.0):
        self.enabled = bool(enabled)
        self.zoom = float(zoom)
        self.size_frac = float(size_frac)
        self.aim_speed = float(aim_speed)
        self.travel_speed = float(travel_speed)
        self.u = 0.0
        self._v_ema = 0.0
        self._calm_s = 0.0
        self._last = None          # (x, y, t)
        self.apply_params({})      # clamp whatever the caller passed

    # -- live tuning ------------------------------------------------------
    def apply_params(self, params):
        """Apply a (possibly junk) config mapping; ignore what isn't ours.
        Fed raw config.json periodically, so it must never raise."""
        if not isinstance(params, dict):
            params = {}
        def num(key, cur, lo, hi):
            v = params.get(key, cur)
            try:
                return _clamp(float(v), lo, hi)
            except (TypeError, ValueError):
                return cur
        self.enabled = bool(params.get("enabled", self.enabled))
        self.zoom = num("zoom", self.zoom, 1.0, 8.0)
        self.size_frac = num("size_frac", self.size_frac, 0.08, 1.0)
        self.aim_speed = num("aim_speed", self.aim_speed, 10.0, 2000.0)
        self.travel_speed = num("travel_speed", self.travel_speed,
                                20.0, 6000.0)
        if self.travel_speed <= self.aim_speed:
            self.travel_speed = self.aim_speed + 1.0

    def reset(self):
        """Forget all motion state: the next appearance is a fresh,
        dwell-gated bloom rather than a resumed one."""
        self.u = 0.0
        self._v_ema = 0.0
        self._calm_s = 0.0
        self._last = None

    # -- per-tick ---------------------------------------------------------
    def update(self, x, y, now, mon, active=True, brake=0.0):
        """One cursor sample. Returns None (hidden) or
        (alpha 0..255, (w, h) px, window rect, source rect)."""
        if self._last is None:
            self._last, self._v_ema = (x, y, now), 0.0
            return None
        lx, ly, lt = self._last
        dt = _clamp(now - lt, 1e-3, 0.1)
        self._last = (x, y, now)

        # The reference dimension is the monitor's SHORT side: "height"
        # would balloon the lens 1.8x on the portrait monitor, which is
        # the same panel rotated and must feel identical.
        mon_w = max(1, mon[2] - mon[0])
        mon_h = max(1, mon[3] - mon[1])
        short = min(mon_w, mon_h)

        jump = math.hypot(x - lx, y - ly)
        if jump > 0.10 * short:
            # A teleport (the shaka recenter's SetCursorPos), not motion:
            # riding it would smear a visible lens across the screen.
            # Vanish this frame; the new spot earns its own bloom. The
            # threshold is deliberately low — 0.10 * short per sample is
            # ~9000 px/s, far past any speed at which the lens is still
            # visible, so real motion can never trip it, but a recenter
            # from close by still counts as the teleport it is.
            self.reset()
            self._last = (x, y, now)
            return None
        v = jump / dt
        self._v_ema += (v - self._v_ema) * (1.0 - math.exp(-dt / self.SPEED_TAU))

        s = short / self.REF_H
        lo, hi = self.aim_speed * s, self.travel_speed * s
        brake = _clamp(brake, 0.0, 1.0)
        u_target = max(1.0 - smoothstep(lo, hi, self._v_ema), brake)
        if not (self.enabled and active):
            u_target = 0.0
            self._calm_s = 0.0    # a fresh engage must earn its dwell
        elif self.u < 0.02:
            # From fully hidden the bloom needs a beat of genuine calm —
            # a flick reversal passes through zero speed for a frame and
            # must not flash the lens. A squeezed brake is explicit
            # intent and skips the wait.
            self._calm_s = self._calm_s + dt if self._v_ema < lo else 0.0
            if self._calm_s < self.DWELL_S and brake < 0.5:
                u_target = 0.0
        tau = self.SHOW_TAU if u_target > self.u else self.HIDE_TAU
        self.u += (u_target - self.u) * (1.0 - math.exp(-dt / tau))

        alpha = int(round(255 * smoothstep(*self.ALPHA_BAND, self.u)))
        if alpha < self.HIDE_ALPHA:
            return None
        grown = self.SIZE_FLOOR + (1.0 - self.SIZE_FLOOR) * self.u
        h = int(short * self.size_frac * grown)
        h -= h % 4
        # Width follows the 4:3 shape until the monitor runs out — at the
        # top of the size range the lens is simply as wide as the screen.
        w = int(min(h * self.ASPECT, mon_w))
        w -= w % 4
        # The % 4 quantisation is anti-flicker: while u breathes with hand
        # jitter the raw size changes by a pixel nearly every frame, and
        # each change is a full window resize (re-layout, re-rounding,
        # border redraw). Stepping by 4px cuts the resizes to a quarter
        # and is invisible at 60Hz.
        # Both rects come from the RAW cursor in one call, which is what
        # keeps the picture under the cursor truthful. (An earlier version
        # smoothed the source centre to calm magnified tremor — that shifted
        # the content off the true click point while moving, which read as
        # "my clicks land below where I'm aiming". The pointer is already
        # One-Euro-smoothed upstream; truth wins.)
        win, src = lens_rects(x, y, w, h, self.zoom, mon)
        return alpha, (w, h), win, src


# ------------------------------------------------------------ win32 window ---
# Constants kept local: this is the only module that speaks Magnification.

_WS_POPUP = 0x80000000
_WS_CLIPCHILDREN = 0x02000000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_LWA_ALPHA = 0x2
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4
_HWND_TOPMOST = -1
_SWP_NOACTIVATE = 0x0010
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_MONITOR_DEFAULTTONEAREST = 2
_DWMWA_CORNER_PREFERENCE = 33   # Win11: let the compositor round us
_DWMWCP_ROUND = 2
_DWMWA_BORDER_COLOR = 34        # Win11: 1-physical-px hairline
_BORDER_GREY = 0x00787878       # reads as a hairline on dark AND light
_MW_FILTERMODE_EXCLUDE = 0
_PM_REMOVE = 1


class _MAGTRANSFORM(ctypes.Structure):
    _fields_ = [("v", (ctypes.c_float * 3) * 3)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                              ctypes.c_uint, ctypes.c_size_t,
                              ctypes.c_ssize_t)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


# A window class is registered once per PROCESS and its wndproc thunk must
# outlive every window ever created from it — a per-instance thunk would be
# freed with its LensWindow and a later construction would jump through the
# dangling pointer (RegisterClassW silently no-ops on the second call, so
# the stale registration would win). Module-level state makes both
# lifetimes right by construction.
_wnd_class = {"proc": None, "registered": False}
_ERROR_CLASS_ALREADY_EXISTS = 1410


def _register_window_class(user32, hinst):
    if _wnd_class["registered"]:
        return

    def wndproc(hwnd, msg, wp, lp):
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    _wnd_class["proc"] = _WNDPROC(wndproc)
    user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    cls = _WNDCLASSW(0, _wnd_class["proc"], 0, 0, hinst, None, None, None,
                     None, "AirMouseLens")
    if not user32.RegisterClassW(ctypes.byref(cls)) \
            and ctypes.get_last_error() != _ERROR_CLASS_ALREADY_EXISTS:
        raise OSError("RegisterClassW failed")
    _wnd_class["registered"] = True


class LensWindow:
    """The lens on screen: owns its thread, window and Magnification state.

    The tracker talks to it through three GIL-atomic calls — set_state()
    every frame, apply_params() on the live-config reload, stop() on quit.
    Construction never raises: if anything Win32-side fails (old Windows,
    WOW64, a hostile session), `ok` goes False and the app runs on
    without a lens."""

    def __init__(self, params=None):
        self.model = LensModel()
        self.model.apply_params(params or {})
        self.ok = True
        self._active = False
        self._brake = 0.0
        self._poke = 0.0      # when set_state last spoke; stale = inactive
        self._stop = threading.Event()
        self._thread = None

    # -- API for the tracker (any thread) ---------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="lens")
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def set_state(self, active, brake=0.0):
        self._active = bool(active)
        self._brake = float(brake)
        self._poke = time.perf_counter()

    def apply_params(self, params):
        self.model.apply_params(params)

    # -- the lens thread --------------------------------------------------
    def _run(self):
        try:
            self._loop()
        except Exception as exc:                       # noqa: BLE001
            print(f"lens: disabled ({exc!r})")
            self.ok = False

    def _loop(self):
        # A PRIVATE user32 handle: HWNDs are 64-bit, so every function
        # that takes or returns one needs argtypes/restype spelled out —
        # ctypes' default int conversion truncates handles and turns
        # HWND_TOPMOST (-1) into garbage, which made SetWindowPos fail
        # silently and left the lens parked at (0,0). Declaring them on a
        # private instance keeps the fix from leaking into the shared
        # ctypes.windll.user32 that magnet.py and airmouse.py use.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        _p, _i, _u = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        user32.CreateWindowExW.restype = _p
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, _i, _i, _i, _i, _p, _p, _p, _p]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [_p, _u, ctypes.c_size_t,
                                          ctypes.c_ssize_t]
        user32.SetWindowPos.argtypes = [_p, _p, _i, _i, _i, _i, _u]
        user32.MoveWindow.argtypes = [_p, _i, _i, _i, _i, wintypes.BOOL]
        user32.ShowWindow.argtypes = [_p, _i]
        user32.SetLayeredWindowAttributes.argtypes = [
            _p, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
        user32.InvalidateRect.argtypes = [_p, _p, wintypes.BOOL]
        user32.DestroyWindow.argtypes = [_p]
        user32.MonitorFromPoint.restype = _p
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        # Per-monitor DPI awareness for THIS THREAD only: the lens needs
        # physical pixels (the Magnification API speaks nothing else, and
        # the monitors run different scales), while the rest of the app
        # keeps the coordinate space it has always had.
        try:
            user32.SetThreadDpiAwarenessContext.restype = _p
            user32.SetThreadDpiAwarenessContext.argtypes = [_p]
            user32.SetThreadDpiAwarenessContext(_p(-4))
        except (AttributeError, OSError):
            pass
        mag = ctypes.WinDLL("Magnification.dll")
        if not mag.MagInitialize():
            raise OSError("MagInitialize failed")
        # Without this, Windows timers tick every ~15.6ms and sleep(1/60)
        # lands on an uneven 15.6/31.2ms beat — video inside the lens
        # visibly judders. 1ms resolution makes the 60Hz cadence real.
        winmm = ctypes.WinDLL("winmm")
        winmm.timeBeginPeriod(1)
        try:
            self._windows(user32, mag)
        finally:
            winmm.timeEndPeriod(1)
            mag.MagUninitialize()

    def _windows(self, user32, mag):
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        gdi_rect = wintypes.RECT
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                        ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_uint, ctypes.c_uint]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        mag.MagSetWindowTransform.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(_MAGTRANSFORM)]
        mag.MagSetWindowFilterList.argtypes = [ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.c_int,
                                               ctypes.POINTER(ctypes.c_void_p)]
        mag.MagSetWindowSource.argtypes = [ctypes.c_void_p, gdi_rect]

        hinst = kernel32.GetModuleHandleW(None)
        _register_window_class(user32, hinst)

        size0 = 320
        host = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
            "AirMouseLens", "", _WS_POPUP | _WS_CLIPCHILDREN,
            0, 0, size0, size0, None, None, hinst, None)
        if not host:
            raise OSError("lens host window failed")
        # Mandatory: a layered window has no surface until this call —
        # without it the magnifier renders nothing at all.
        user32.SetLayeredWindowAttributes(ctypes.c_void_p(host), 0, 255,
                                          _LWA_ALPHA)
        try:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p,
                                                  wintypes.DWORD,
                                                  ctypes.c_void_p,
                                                  wintypes.DWORD]
            pref = ctypes.c_int(_DWMWCP_ROUND)
            dwm.DwmSetWindowAttribute(host, _DWMWA_CORNER_PREFERENCE,
                                      ctypes.byref(pref), 4)
            col = wintypes.DWORD(_BORDER_GREY)
            dwm.DwmSetWindowAttribute(host, _DWMWA_BORDER_COLOR,
                                      ctypes.byref(col), 4)
        except OSError:
            pass    # Win10: square corners, still a perfectly good lens

        child = user32.CreateWindowExW(
            0, "Magnifier", "", _WS_CHILD | _WS_VISIBLE,
            0, 0, size0, size0, ctypes.c_void_p(host), None, hinst, None)
        if not child:
            raise OSError("magnifier control failed (Magnification API)")
        child = ctypes.c_void_p(child)
        hostp = ctypes.c_void_p(host)

        # Belt and braces against a hall-of-mirrors: the API excludes the
        # magnifier itself automatically, this excludes our whole host.
        flt = ctypes.c_void_p(host)
        mag.MagSetWindowFilterList(child, _MW_FILTERMODE_EXCLUDE, 1,
                                   ctypes.byref(flt))

        applied_zoom = 0.0
        applied_wh = (size0, size0)
        applied_alpha = 255
        applied_win = None
        shown = False
        TICK = 1.0 / 60.0
        next_tick = time.perf_counter()

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", gdi_rect), ("rcWork", gdi_rect),
                        ("dwFlags", wintypes.DWORD)]

        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(MONITORINFO)]

        pt = wintypes.POINT()
        msg = wintypes.MSG()
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)

        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0,
                                      _PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            if not self.model.enabled:
                if shown:
                    user32.ShowWindow(hostp, _SW_HIDE)
                    shown = False
                # Forget motion state while off: re-enabling must give a
                # fresh gated bloom, not a resumed u≈1 popping in at full
                # opacity. Then idle cheaply — nothing to draw or measure.
                self.model.reset()
                self._stop.wait(0.25)
                continue

            user32.GetCursorPos(ctypes.byref(pt))
            hmon = user32.MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)
            user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
            r = mi.rcMonitor
            now = time.perf_counter()
            # An `active` older than half a second means the camera loop
            # has stalled, not that the hand is calmly aiming — a frozen
            # cursor reads as perfect calm, and the lens is the only
            # component with its own thread that could keep acting on
            # that stale intent. Every consumer of a poke must expire it.
            active = self._active and (now - self._poke) < 0.5
            frame = self.model.update(
                pt.x, pt.y, now, (r.left, r.top, r.right, r.bottom),
                active=active, brake=self._brake)

            if frame is None:
                if shown:
                    user32.ShowWindow(hostp, _SW_HIDE)
                    shown = False
                next_tick = self._tick_wait(next_tick, TICK)
                continue

            alpha, (w, h), win, src = frame
            zoom = self.model.zoom
            if zoom != applied_zoom:
                t = _MAGTRANSFORM()
                t.v[0][0] = t.v[1][1] = zoom
                t.v[2][2] = 1.0
                mag.MagSetWindowTransform(child, ctypes.byref(t))
                applied_zoom = zoom
            if (w, h) != applied_wh:
                user32.MoveWindow(child, 0, 0, w, h, False)
                applied_wh = (w, h)
            if alpha != applied_alpha:
                user32.SetLayeredWindowAttributes(hostp, 0, alpha,
                                                  _LWA_ALPHA)
                applied_alpha = alpha
            mag.MagSetWindowSource(
                child, gdi_rect(src[0], src[1], src[2], src[3]))
            # Topmost is re-asserted every tick — tooltips appear exactly
            # while the cursor is parked and must land UNDER the magnifier
            # so it can enlarge them. But when nothing moved it is a pure
            # z-order poke: repositioning a parked window every 16ms is
            # needless churn that reads as shimmer.
            if (win, (w, h)) != applied_win:
                user32.SetWindowPos(hostp, _HWND_TOPMOST, win[0], win[1],
                                    w, h, _SWP_NOACTIVATE)
                applied_win = (win, (w, h))
            else:
                user32.SetWindowPos(hostp, _HWND_TOPMOST, 0, 0, 0, 0,
                                    _SWP_NOACTIVATE | _SWP_NOMOVE
                                    | _SWP_NOSIZE)
            if not shown:
                user32.ShowWindow(hostp, _SW_SHOWNOACTIVATE)
                shown = True
            user32.InvalidateRect(child, None, False)
            next_tick = self._tick_wait(next_tick, TICK)

        user32.DestroyWindow(hostp)

    @staticmethod
    def _tick_wait(next_tick, tick):
        """Sleep to an ABSOLUTE 60Hz schedule. Sleeping a fixed 16ms per
        loop drifts by the work done each tick and by timer granularity,
        and an uneven capture cadence is exactly what reads as flicker in
        magnified video."""
        next_tick += tick
        now = time.perf_counter()
        if next_tick <= now:            # fell behind; don't spiral
            return now
        time.sleep(next_tick - now)
        return next_tick



if __name__ == "__main__":
    # Standalone try-out: the lens follows the real cursor, always
    # "active", for 30 seconds. Move the mouse: stop to bloom, flick to
    # dismiss. Needs no camera and no config.
    lw = LensWindow({"enabled": True}).start()
    print("lens demo: 30 seconds — slow down to bloom, flick to hide")
    try:
        for _ in range(300):    # keep the active poke fresh, like the app
            lw.set_state(active=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    lw.stop()
    time.sleep(0.2)
    print("done")

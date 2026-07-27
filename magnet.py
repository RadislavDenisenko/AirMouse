"""Cursor magnetism — clickable things get sticky as the cursor nears them.

The cursor is driven by relative motion, so magnetism is applied by scaling
that motion: near a target your hand moves the pointer less, which makes it
much easier to stop on a small control like a window's close button. A
gentle pull toward the target's centre is layered on top, but only in
proportion to how far you actually moved — a still hand never drifts, which
is the same invariant the rest of the app keeps.

Finding targets, cheapest first:

  1. **Window caption buttons.** `WM_NCHITTEST` asks a window what is at a
     point and answers `HTCLOSE` / `HTMINBUTTON` / `HTMAXBUTTON` directly.
     No dependencies, works on standard title bars, and it is exactly the
     "little X button" case that motivated the feature.
  2. **Classic Win32 child controls.** `RealChildWindowFromPoint` plus the
     window class name catches real buttons in dialogs and older apps.
  3. **MSAA.** `AccessibleObjectFromPoint` reaches controls that draw
     themselves, which is most modern UI. This one talks COM through
     ctypes, so every call is wrapped — if it misbehaves on some app the
     finder silently falls back to the cheaper tiers.

All of that runs on a background thread and is cached, because a Win32 or
COM query can take milliseconds and the capture loop has a 33 ms budget.
Queries to other processes use `SendMessageTimeout`, so a hung application
can never stall the tracker.
"""

import ctypes
import math
import threading
import time
from ctypes import wintypes

from mouse_input import get_cursor_pos

_user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_NCHITTEST = 0x0084
SMTO_ABORTIFHUNG = 0x0002
GA_ROOT = 2

# WM_NCHITTEST replies we care about
HIT_KINDS = {20: "close button", 8: "minimise button", 9: "maximise button"}

# MSAA roles worth grabbing (ignore plain text, containers, etc.)
MSAA_ROLES = {43: "button", 30: "link", 12: "menu item", 44: "checkbox",
              45: "radio button", 46: "combo box", 42: "text field",
              50: "text field"}

# Never slow the cursor below this fraction of its normal speed, so a target
# can always be pushed out of no matter how strong the magnet is.
MIN_SCALE = 0.15
MAX_SLOW = 1.0 - MIN_SCALE

# The simple preset: what magnetism does with one toggle and no tuning. These
# are deliberately strong — the whole point is that it grabs without being
# configured. Custom mode swaps these for the config values.
PRESET = {
    "strength": 80.0,
    "reach_px": 90.0,
    "pull": 0.45,
    "capture_radius_px": 40.0,
    "escape_px": 40.0,
    "refractory_s": 0.3,
    "include_text_fields": False,
}

# How committed a movement has to be before a captured target lets go.
ESCAPE_EFFORT = {"light": 24.0, "medium": 40.0, "heavy": 64.0}


# ------------------------------------------------------------- pure maths ---
def dist_to_rect(x, y, rect):
    """Distance from a point to a rectangle; 0 when the point is inside."""
    l, t, r, b = rect
    dx = max(l - x, 0, x - r)
    dy = max(t - y, 0, y - b)
    return math.hypot(dx, dy)


def dist_segment_rect(p0, p1, rect, step_px=4.0):
    """Closest the cursor gets to a rect while travelling p0 -> p1.

    This is what makes the hook work: at high sensitivity one frame can move
    the pointer further than a close button is wide, so testing only the
    start and end points misses a pass straight over the target."""
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    n = max(1, min(32, int(length / max(1.0, step_px)) + 1))
    best = float("inf")
    for i in range(n + 1):
        t = i / n
        d = dist_to_rect(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, rect)
        if d < best:
            best = d
        if best <= 0.0:
            break
    return best


def rects_match(a, b, tol=10):
    """Whether two probes found the same control (rects wobble slightly)."""
    if a is None or b is None:
        return False
    return all(abs(a[i] - b[i]) <= tol for i in range(4))


def apply_magnet(dx, dy, cursor, rect, strength, reach_px, pull=0.35):
    """Bend one frame of cursor motion around a target. Pure — no Win32.

    dx, dy      motion the gesture layer wants to apply, in pixels
    cursor      current pointer position (x, y)
    rect        target (left, top, right, bottom), or None for no target
    strength    0..100, how sticky
    reach_px    how far out the effect starts
    pull        how hard to draw toward the centre, as a share of the motion

    Returns (dx, dy) as floats — the caller carries the sub-pixel remainder.
    """
    if rect is None or strength <= 0 or reach_px <= 0:
        return float(dx), float(dy)
    if dx == 0 and dy == 0:
        return 0.0, 0.0                 # a still hand never moves the cursor

    cx, cy = cursor
    d = dist_to_rect(cx, cy, rect)
    if d >= reach_px:
        return float(dx), float(dy)

    influence = 1.0 - d / reach_px      # 1 inside the target, 0 at the rim
    s = max(0.0, min(1.0, strength / 100.0))

    l, t, r, b = rect
    tx, ty = (l + r) / 2.0, (t + b) / 2.0
    away_x, away_y = cx - tx, cy - ty
    # Moving outward? Ease off sharply so leaving never feels like a fight.
    leaving = (dx * away_x + dy * away_y) > 0
    if leaving:
        influence *= 0.35

    scale = 1.0 - MAX_SLOW * s * influence
    out_x, out_y = dx * scale, dy * scale

    if not leaving and pull > 0.0:
        # Pull is proportional to how far the hand moved, so it can only ever
        # shape motion you are already making, and is capped at the remaining
        # distance so it cannot overshoot the centre.
        to_x, to_y = tx - cx, ty - cy
        dist = math.hypot(to_x, to_y)
        if dist > 1e-6:
            step = min(pull * s * influence * math.hypot(dx, dy), dist)
            out_x += to_x / dist * step
            out_y += to_y / dist * step
    return out_x, out_y


def slowdown_factor(cursor, rect, strength, reach_px):
    """The scale a *centred* motion would get — for the debug readout."""
    if rect is None or strength <= 0 or reach_px <= 0:
        return 1.0
    d = dist_to_rect(cursor[0], cursor[1], rect)
    if d >= reach_px:
        return 1.0
    s = max(0.0, min(1.0, strength / 100.0))
    return 1.0 - MAX_SLOW * s * (1.0 - d / reach_px)


def resolve_params(mag_cfg):
    """Effective magnet parameters from a config block.

    Simple mode (the default) ignores the sliders entirely and uses PRESET,
    so a fresh install is strong without being configured, and leaving the
    sliders somewhere odd can never weaken it. Custom tuning swaps in the
    config values."""
    cfg = mag_cfg or {}
    out = dict(PRESET)
    out["enabled"] = bool(cfg.get("enabled", True))
    out["custom"] = bool(cfg.get("custom_tuning", False))
    if out["custom"]:
        for key in ("strength", "reach_px", "pull", "capture_radius_px",
                    "escape_px", "refractory_s"):
            if cfg.get(key) is not None:
                out[key] = float(cfg[key])
        out["include_text_fields"] = bool(cfg.get("include_text_fields", False))
    return out


# ------------------------------------------------------------ target hunt ---
def _lparam(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _hit_test(hwnd, x, y, timeout_ms=30):
    """WM_NCHITTEST with a timeout, so a hung app can't stall us."""
    res = ctypes.c_size_t(0)
    ok = _user32.SendMessageTimeoutW(hwnd, WM_NCHITTEST, 0, _lparam(x, y),
                                     SMTO_ABORTIFHUNG, timeout_ms,
                                     ctypes.byref(res))
    return int(res.value) if ok else None


def caption_button_at(x, y, radius=44, coarse=8, budget_s=0.05):
    """A window's close/minimise/maximise button near (x, y), as
    ((l, t, r, b), kind) — or None.

    `budget_s` caps the whole probe: a window belonging to a busy or dying
    process answers slowly even with a per-call timeout, and a stale handle
    can otherwise burn hundreds of milliseconds of the finder thread."""
    deadline = time.perf_counter() + budget_s
    pt = wintypes.POINT(int(x), int(y))
    hwnd = _user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    hwnd = _user32.GetAncestor(hwnd, GA_ROOT) or hwnd

    hit = None
    # coarse scan outward from the cursor: the first ring that lands on a
    # caption button wins, so the nearest one is found first
    for ring in range(0, radius + 1, coarse):
        if time.perf_counter() > deadline:
            return None
        for px in range(x - ring, x + ring + 1, coarse):
            for py in range(y - ring, y + ring + 1, coarse):
                if ring and abs(px - x) != ring and abs(py - y) != ring:
                    continue           # interior already covered
                code = _hit_test(hwnd, px, py)
                if code in HIT_KINDS:
                    hit = (px, py, code)
                    break
            if hit:
                break
        if hit:
            break
    if not hit:
        return None

    hx, hy, code = hit
    # walk out in each direction while the answer stays the same
    l = r = hx
    t = b = hy
    for _ in range(40):
        if time.perf_counter() > deadline:
            break
        if _hit_test(hwnd, l - 2, hy) == code:
            l -= 2
        else:
            break
    for _ in range(40):
        if time.perf_counter() > deadline:
            break
        if _hit_test(hwnd, r + 2, hy) == code:
            r += 2
        else:
            break
    for _ in range(30):
        if _hit_test(hwnd, hx, t - 2) == code:
            t -= 2
        else:
            break
    for _ in range(30):
        if _hit_test(hwnd, hx, b + 2) == code:
            b += 2
        else:
            break
    return (l, t, r, b), HIT_KINDS[code]


def child_control_at(x, y):
    """A classic Win32 child control under the point, as (rect, kind)."""
    pt = wintypes.POINT(int(x), int(y))
    top = _user32.WindowFromPoint(pt)
    if not top:
        return None
    root = _user32.GetAncestor(top, GA_ROOT) or top
    client = wintypes.POINT(int(x), int(y))
    _user32.ScreenToClient(root, ctypes.byref(client))
    child = _user32.RealChildWindowFromPoint(root, client)
    if not child or child == root:
        return None
    buf = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(child, buf, 64)
    cls = buf.value.lower()
    if "button" not in cls and "edit" not in cls and "combobox" not in cls:
        return None
    rc = wintypes.RECT()
    if not _user32.GetWindowRect(child, ctypes.byref(rc)):
        return None
    if rc.right <= rc.left or rc.bottom <= rc.top:
        return None
    kind = ("button" if "button" in cls
            else "text field" if "edit" in cls else "combo box")
    return (rc.left, rc.top, rc.right, rc.bottom), kind


class _MsaaProbe:
    """AccessibleObjectFromPoint through raw ctypes COM.

    IAccessible derives from IDispatch, so the vtable slots we need sit at
    fixed offsets: get_accRole is 13 and accLocation is 22. Everything here
    is defensive — any failure disables the probe rather than risking the
    capture loop.
    """

    _VT_I4 = 3

    class _VARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("val", ctypes.c_longlong), ("pad", ctypes.c_longlong)]

    def __init__(self):
        self.ok = False
        try:
            self._ole = ctypes.oledll.oleacc
            ctypes.oledll.ole32.CoInitializeEx(None, 0)
            self.ok = True
        except Exception:
            self.ok = False

    def at(self, x, y):
        if not self.ok:
            return None
        try:
            acc = ctypes.c_void_p()
            child = self._VARIANT()
            pt = wintypes.POINT(int(x), int(y))
            self._ole.AccessibleObjectFromPoint(pt, ctypes.byref(acc),
                                                ctypes.byref(child))
            if not acc:
                return None
            try:
                return self._read(acc, child)
            finally:
                self._release(acc)
        except Exception:
            return None

    def _vtbl(self, acc, index, restype, *argtypes):
        vtbl = ctypes.cast(acc, ctypes.POINTER(ctypes.c_void_p))[0]
        fn_ptr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(fn_ptr)

    def _release(self, acc):
        try:
            self._vtbl(acc, 2, ctypes.c_ulong)(acc)
        except Exception:
            pass

    def _read(self, acc, child):
        role_v = self._VARIANT()
        get_role = self._vtbl(acc, 13, ctypes.c_long,
                              self._VARIANT, ctypes.POINTER(self._VARIANT))
        if get_role(acc, child, ctypes.byref(role_v)) != 0:
            return None
        if role_v.vt != self._VT_I4:
            return None
        kind = MSAA_ROLES.get(int(role_v.val))
        if kind is None:
            return None

        L = ctypes.c_long(0)
        T = ctypes.c_long(0)
        W = ctypes.c_long(0)
        H = ctypes.c_long(0)
        loc = self._vtbl(acc, 22, ctypes.c_long,
                         ctypes.POINTER(ctypes.c_long),
                         ctypes.POINTER(ctypes.c_long),
                         ctypes.POINTER(ctypes.c_long),
                         ctypes.POINTER(ctypes.c_long), self._VARIANT)
        if loc(acc, ctypes.byref(L), ctypes.byref(T), ctypes.byref(W),
               ctypes.byref(H), child) != 0:
            return None
        if W.value <= 0 or H.value <= 0 or W.value > 600 or H.value > 400:
            return None           # containers and giant panes aren't targets
        return (L.value, T.value, L.value + W.value, T.value + H.value), kind


class TargetFinder:
    """Polls for the target near the cursor on a background thread."""

    def __init__(self, hz=12.0, reach_px=40.0, use_msaa=True,
                 include_text_fields=False):
        self.period = 1.0 / max(1.0, hz)
        self.reach_px = reach_px
        self.include_text_fields = include_text_fields
        self._msaa = _MsaaProbe() if use_msaa else None
        self._lock = threading.Lock()
        self._target = None           # (rect, kind, stamp)
        self._stop = threading.Event()
        self._thread = None
        self.errors = 0

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def current(self, max_age=0.5):
        """(rect, kind) if a fresh target is known, else None."""
        with self._lock:
            got = self._target
        if not got:
            return None
        rect, kind, stamp = got
        if time.perf_counter() - stamp > max_age:
            return None
        return rect, kind

    def _keep(self, hit):
        """Text fields are excluded by default: hooking onto them fights you
        when you are aiming at text rather than at a control."""
        if hit is None:
            return None
        if not self.include_text_fields and hit[1] in ("text field",
                                                       "combo box"):
            return None
        return hit

    def _find(self, x, y):
        try:
            hit = caption_button_at(x, y, radius=int(self.reach_px) + 8)
            if hit:
                return hit
        except Exception:
            self.errors += 1
        try:
            hit = self._keep(child_control_at(x, y))
            if hit:
                return hit
        except Exception:
            self.errors += 1
        if self._msaa is not None:
            hit = self._keep(self._msaa.at(x, y))
            if hit:
                return hit
        return None

    def _run(self):
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                x, y = get_cursor_pos()
                found = self._find(x, y)
            except Exception:
                self.errors += 1
                found = None
            with self._lock:
                self._target = ((found[0], found[1], time.perf_counter())
                                if found else None)
            time.sleep(max(0.0, self.period - (time.perf_counter() - t0)))



# --------------------------------------------------------------- the hook ---
class Hooker:
    """Capture-and-hold: the behaviour that makes magnetism feel like a hook.

    Damping alone was never enough. At sensitivity 9 a single frame can move
    the pointer further than a close button is wide, so the cursor could sail
    clean over a target however heavily it was slowed. Instead:

      * **capture** — if this frame's motion path passes near a target, the
        cursor snaps onto its centre instead of flying past;
      * **hold** — while captured the cursor is parked; small hand movements
        do nothing at all, so it genuinely feels stuck to the control;
      * **escape** — motion accumulates as a vector while held, so only
        *committed* movement in one direction adds up (wobble cancels
        itself out). Past the threshold the target lets go and a short
        refractory stops it grabbing the same thing again straight away.

    A still hand still never moves the cursor: every path here returns zero
    motion for zero input.
    """

    LOST_GRACE_S = 0.4     # keep the hook through a brief detection dropout

    def __init__(self, strength=80.0, reach_px=90.0, pull=0.45,
                 capture_radius_px=40.0, escape_px=40.0, refractory_s=0.3):
        self.strength = strength
        self.reach_px = reach_px
        self.pull = pull
        self.capture_radius_px = capture_radius_px
        self.escape_px = escape_px
        self.refractory_s = refractory_s
        self.reset()

    def reset(self):
        self.captured = None          # rect currently held
        self.state = "idle"           # idle | approach | captured | escaping
        self.escape_frac = 0.0        # 0..1 progress toward letting go
        self._sum = [0.0, 0.0]        # committed motion while held
        self._released = None         # last rect let go of, for the refractory
        self._refractory_until = 0.0
        self._lost_since = None

    def step(self, dx, dy, cursor, rect, now):
        """One frame. Returns the motion to actually apply, as floats."""
        if dx == 0 and dy == 0:
            if self.captured is None:
                self.state = "approach" if rect is not None else "idle"
            return 0.0, 0.0

        if self.captured is not None:
            # tolerate the finder briefly losing the control we are holding
            if rect is None:
                if self._lost_since is None:
                    self._lost_since = now
                elif now - self._lost_since > self.LOST_GRACE_S:
                    self._let_go(now)
                    self.state = "idle"   # the control is simply gone
                    return float(dx), float(dy)
            else:
                self._lost_since = None
                if rects_match(rect, self.captured):
                    self.captured = rect      # track small rect wobble

            self._sum[0] += dx
            self._sum[1] += dy
            committed = math.hypot(self._sum[0], self._sum[1])
            self.escape_frac = min(1.0, committed / max(1e-6, self.escape_px))
            if committed >= self.escape_px:
                self._let_go(now)
                self.state = "escaping"
                return float(dx), float(dy)   # pop out along the way you left
            self.state = "captured"
            return 0.0, 0.0                   # parked on the target

        if rect is None:
            self.state = "idle"
            self.escape_frac = 0.0
            return float(dx), float(dy)

        blocked = (now < self._refractory_until
                   and rects_match(rect, self._released))
        if not blocked:
            projected = (cursor[0] + dx, cursor[1] + dy)
            if dist_segment_rect(cursor, projected,
                                 rect) <= self.capture_radius_px:
                self.captured = rect
                self._sum = [0.0, 0.0]
                self.escape_frac = 0.0
                self.state = "captured"
                self._lost_since = None
                cx = (rect[0] + rect[2]) / 2.0
                cy = (rect[1] + rect[3]) / 2.0
                return cx - cursor[0], cy - cursor[1]      # snap on

        self.state = "approach"
        self.escape_frac = 0.0
        return apply_magnet(dx, dy, cursor, rect, self.strength,
                            self.reach_px, self.pull)

    def _let_go(self, now):
        self._released = self.captured
        self._refractory_until = now + self.refractory_s
        self.captured = None
        self._sum = [0.0, 0.0]
        self.escape_frac = 0.0
        self._lost_since = None


# ------------------------------------------------------------- the wrapper ---
class MagnetMouse:
    """Wraps a mouse backend and hooks its motion onto nearby targets.

    Everything except `move` (and drag tracking) is delegated untouched, so
    clicks, scrolling and navigation behave exactly as before. Parameters are
    live-tunable: `apply_params` is called from the capture loop when
    config.json changes, so tuning takes effect without a restart.
    """

    def __init__(self, inner, finder, strength=80.0, reach_px=90.0,
                 pull=0.45, enabled=True, capture_radius_px=40.0,
                 escape_px=40.0, refractory_s=0.3):
        self.inner = inner
        self.finder = finder
        self.enabled = enabled
        self.hook = Hooker(strength, reach_px, pull, capture_radius_px,
                           escape_px, refractory_s)
        self._res = [0.0, 0.0]
        self._dragging = False
        self.last_target = None       # (rect, kind) for the overlay
        self.last_scale = 1.0

    # -- live tuning ------------------------------------------------------
    def apply_params(self, enabled=None, **params):
        if enabled is not None and enabled != self.enabled:
            self.enabled = enabled
            self.hook.reset()
        for name, value in params.items():
            if value is not None and hasattr(self.hook, name):
                setattr(self.hook, name, value)

    @property
    def strength(self):
        return self.hook.strength

    @property
    def reach_px(self):
        return self.hook.reach_px

    @property
    def state(self):
        return "off" if not self.enabled else self.hook.state

    @property
    def escape_frac(self):
        return self.hook.escape_frac

    def move(self, dx, dy):
        if not self.enabled or self._dragging:
            self.last_target = None
            self.last_scale = 1.0
            if self.hook.captured is not None:
                self.hook.reset()
            self.inner.move(dx, dy)
            return

        got = self.finder.current() if self.finder else None
        rect, kind = got if got else (None, None)
        # while holding something, keep reporting it even if this poll missed
        if rect is None and self.hook.captured is not None:
            rect, kind = self.hook.captured, "held"
        self.last_target = (rect, kind) if rect else None

        cursor = get_cursor_pos()
        ox, oy = self.hook.step(dx, dy, cursor,
                                rect if got or self.hook.captured else None,
                                time.perf_counter())
        moved = math.hypot(ox, oy)
        asked = math.hypot(dx, dy)
        self.last_scale = (moved / asked) if asked > 1e-6 else 1.0

        # carry the sub-pixel remainder so slow motion can never stall
        self._res[0] += ox
        self._res[1] += oy
        ix, iy = int(self._res[0]), int(self._res[1])
        if ix or iy:
            self._res[0] -= ix
            self._res[1] -= iy
            self.inner.move(ix, iy)

    def left_down(self):
        self._dragging = True         # dragging past a target must not stick
        self.hook.reset()
        self.inner.left_down()

    def left_up(self):
        self._dragging = False
        self.inner.left_up()

    def __getattr__(self, name):
        return getattr(self.inner, name)

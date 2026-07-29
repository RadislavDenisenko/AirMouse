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
     themselves, which is most modern UI — including the close button on
     each Chrome tab.
  4. **UI Automation.** The same question asked through the newer API. It
     answers on windows where MSAA stays silent, and it returns the
     control's name, which is what the D readout shows you.

Tiers 3 and 4 talk COM through ctypes, so every call is wrapped: if either
misbehaves on some app the finder falls back to the cheaper tiers instead of
taking the tracker down with it.

**Chromium builds its accessibility tree lazily.** Nothing is there to find
until something asks — an assistive tool announces itself by sending
`WM_GETOBJECT`, and the tree is built asynchronously a beat later. `wake()`
sends that nudge. It must *retry*: a window is only recorded as awake once a
probe has actually read something out of it, because a nudge that arrives at
the wrong moment (or a Chrome that later drops accessibility again because
it thinks nothing is listening) would otherwise leave that window marked
done and never asked again. That single missing retry is why tab buttons
worked from a test script and never from inside the app.

All of that runs on a background thread and is cached, because a Win32 or
COM query can take milliseconds and the capture loop has a 33 ms budget.
Queries to other processes use `SendMessageTimeout`, so a hung application
can never stall the tracker.
"""

import ctypes
import math
import threading
import time
from collections import deque
from ctypes import wintypes

from mouse_input import get_cursor_pos

_user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_NCHITTEST = 0x0084
WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC
OBJID_UIA = 0xFFFFFFE7          # UiaRootObjectId (-25)
SMTO_ABORTIFHUNG = 0x0002
GA_ROOT = 2

# WM_NCHITTEST replies we care about
HIT_KINDS = {20: "close button", 8: "minimise button", 9: "maximise button"}

# MSAA roles worth grabbing (ignore plain text, containers, etc.)
MSAA_ROLES = {43: "button", 30: "link", 12: "menu item", 44: "checkbox",
              45: "radio button", 46: "combo box", 42: "text field",
              50: "text field"}

# The same vocabulary in UI Automation's control-type ids, so both COM tiers
# hand back kinds the rest of the module already understands.
UIA_KINDS = {50000: "button", 50031: "button", 50002: "checkbox",
             50013: "radio button", 50005: "link", 50011: "menu item",
             50003: "combo box", 50004: "text field"}

CLSID_CUIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
IID_IUIAUTOMATION = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"

# Anything bigger than this is a container or a pane, not something you aim
# at, and hooking onto it would fight every normal cursor movement.
MAX_TARGET_W = 600
MAX_TARGET_H = 400

# How far below a window's top edge a caption button can still plausibly be.
# Generous: custom title bars (Chrome's tab strip, Explorer's ribbon) are
# taller than the standard one.
CAPTION_BAND_PX = 60

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
class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


def _guid(text):
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


def ensure_com():
    """Initialise COM for the calling thread, tolerantly.

    COM is per-thread, so the finder thread must do this itself or every COM
    call from it fails silently. Deliberately `windll` and not `oledll`: an
    already-initialised thread answers S_FALSE, and a thread someone else got
    to first answers RPC_E_CHANGED_MODE — `oledll` would raise on the latter,
    and a raise here would disable a whole tier for the life of the process.
    """
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED
    except Exception:
        pass


def _lparam(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _hit_test(hwnd, x, y, timeout_ms=30):
    """WM_NCHITTEST with a timeout, so a hung app can't stall us."""
    res = ctypes.c_size_t(0)
    ok = _user32.SendMessageTimeoutW(hwnd, WM_NCHITTEST, 0, _lparam(x, y),
                                     SMTO_ABORTIFHUNG, timeout_ms,
                                     ctypes.byref(res))
    return int(res.value) if ok else None


def caption_button_at(x, y, radius=44, coarse=10, budget_s=0.012):
    """A window's close/minimise/maximise button near (x, y), as
    ((l, t, r, b), kind) — or None.

    `budget_s` caps the whole probe, and the cap matters more than it looks:
    a *hit* is found in the first ring or two and costs about a millisecond,
    but an exhaustive *miss* at full reach is some six hundred cross-process
    messages. Over a Chrome tab strip — where the answer is always no, and
    the tier that can actually help comes later — that was eating 20 ms of
    every poll. Caption buttons are far larger than the step, so the coarse
    grid never walks past one."""
    deadline = time.perf_counter() + budget_s
    pt = wintypes.POINT(int(x), int(y))
    hwnd = _user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    hwnd = _user32.GetAncestor(hwnd, GA_ROOT) or hwnd

    # A caption button is in the caption, which is at the top of the window.
    # Far below it there is nothing to find, so don't pay for the search —
    # this is most of the screen most of the time.
    rc = wintypes.RECT()
    if (_user32.GetWindowRect(hwnd, ctypes.byref(rc))
            and y - rc.top > radius + CAPTION_BAND_PX):
        return None

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


def _vtbl(obj, index, restype, *argtypes):
    """Call slot `index` of a COM object's vtable. Both COM tiers here are
    raw ctypes rather than a generated wrapper, so no extra dependency ends
    up in the packaged build."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
    fn_ptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fn_ptr)


def _com_release(obj):
    try:
        _vtbl(obj, 2, ctypes.c_ulong)(obj)
    except Exception:
        pass


def _root_at(x, y):
    """The top-level window under a screen point, or 0."""
    hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return _user32.GetAncestor(hwnd, GA_ROOT) or hwnd


class _Waker:
    """Nudges lazily-built accessibility trees into existence, and retries.

    Chromium apps expose nothing until an assistive tool asks, so we send the
    same `WM_GETOBJECT` one would. The important part is what counts as done:
    a window is only remembered as awake once a probe has actually read a
    control out of it (`proven`). Nudges that didn't take are retried on a
    cooldown, a handful of times, because the tree is built asynchronously
    and because Chrome will drop accessibility again if it decides nothing is
    listening. Marking a window done at nudge time — the original bug — meant
    one badly-timed nudge disabled tab buttons for that window forever.
    """

    RETRY_S = 1.0
    ATTEMPTS = 6
    MAX_TRACKED = 64

    def __init__(self):
        self.proven = set()      # hwnds that have answered at least once
        self._tries = {}         # hwnd -> [attempts, last attempt time]

    def stats(self):
        return len(self.proven), len(self._tries)

    def nudge(self, hwnd, now=None):
        if not hwnd or hwnd in self.proven:
            return False
        now = time.perf_counter() if now is None else now
        state = self._tries.get(hwnd)
        if state is None:
            if len(self._tries) >= self.MAX_TRACKED:
                self._tries.clear()
            state = self._tries[hwnd] = [0, -1e9]
        if state[0] >= self.ATTEMPTS or now - state[1] < self.RETRY_S:
            return False
        state[0] += 1
        state[1] = now
        return self._send(hwnd)

    def _send(self, hwnd):
        """The Win32 half, kept separate so the retry bookkeeping above can
        be tested without a window to talk to."""
        try:
            res = ctypes.c_size_t(0)
            for objid in (OBJID_CLIENT, OBJID_UIA):
                _user32.SendMessageTimeoutW(hwnd, WM_GETOBJECT, 0, objid,
                                            SMTO_ABORTIFHUNG, 60,
                                            ctypes.byref(res))
        except Exception:
            return False
        return True

    def mark_awake(self, hwnd):
        if hwnd:
            if len(self.proven) >= self.MAX_TRACKED:
                self.proven.clear()
            self.proven.add(hwnd)
            self._tries.pop(hwnd, None)


class _MsaaProbe:
    """AccessibleObjectFromPoint through raw ctypes COM.

    IAccessible derives from IDispatch, so the vtable slots we need sit at
    fixed offsets: get_accName is 10, get_accRole 13 and accLocation 22.
    Everything here is defensive — any failure disables the probe rather than
    risking the capture loop.
    """

    _VT_I4 = 3

    class _VARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("val", ctypes.c_longlong), ("pad", ctypes.c_longlong)]

    def __init__(self, waker=None):
        self.ok = False
        self.waker = waker if waker is not None else _Waker()
        try:
            self._ole = ctypes.oledll.oleacc
            self._oleaut = ctypes.WinDLL("oleaut32")
            self.ok = True
        except Exception:
            self.ok = False

    def at(self, x, y, hwnd=None):
        """(rect, kind, name) under the point, or None."""
        if not self.ok:
            return None
        hwnd = _root_at(x, y) if hwnd is None else hwnd
        self.waker.nudge(hwnd)
        try:
            acc = ctypes.c_void_p()
            child = self._VARIANT()
            pt = wintypes.POINT(int(x), int(y))
            self._ole.AccessibleObjectFromPoint(pt, ctypes.byref(acc),
                                                ctypes.byref(child))
            if not acc:
                return None
            try:
                got = self._read(acc, child)
            finally:
                _com_release(acc)
            if got:
                self.waker.mark_awake(hwnd)
            return got
        except Exception:
            return None

    def _name(self, acc, child):
        bstr = ctypes.c_void_p()
        try:
            get_name = _vtbl(acc, 10, ctypes.c_long, self._VARIANT,
                             ctypes.POINTER(ctypes.c_void_p))
            if get_name(acc, child, ctypes.byref(bstr)) != 0 or not bstr:
                return ""
            text = ctypes.cast(bstr, ctypes.c_wchar_p).value or ""
            self._oleaut.SysFreeString(bstr)
            return text
        except Exception:
            return ""

    def _read(self, acc, child):
        role_v = self._VARIANT()
        get_role = _vtbl(acc, 13, ctypes.c_long,
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
        loc = _vtbl(acc, 22, ctypes.c_long,
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long), self._VARIANT)
        if loc(acc, ctypes.byref(L), ctypes.byref(T), ctypes.byref(W),
               ctypes.byref(H), child) != 0:
            return None
        if (W.value <= 0 or H.value <= 0
                or W.value > MAX_TARGET_W or H.value > MAX_TARGET_H):
            return None           # containers and giant panes aren't targets
        return ((L.value, T.value, L.value + W.value, T.value + H.value),
                kind, self._name(acc, child))


class _UiaProbe:
    """IUIAutomation::ElementFromPoint, again through raw ctypes.

    Kept as the last tier because it costs a shade more than MSAA and finds
    the same controls most of the time — but not always: on windows where
    MSAA answered nothing at all, this still returned the buttons. Having a
    UIA client alive in the process also helps Chromium keep its tree up.

    The COM object is created lazily, on whichever thread first asks, so it
    is always built *after* that thread has initialised COM.
    """

    _ELEMENT_FROM_POINT = 7
    _GET_CONTROL_TYPE = 21
    _GET_NAME = 23
    _GET_BOUNDING_RECT = 43

    def __init__(self, waker=None):
        self.ok = True            # until proven otherwise
        self.waker = waker if waker is not None else _Waker()
        self._uia = None
        self._tried = False

    def _client(self):
        if self._tried:
            return self._uia
        self._tried = True
        try:
            self._oleaut = ctypes.WinDLL("oleaut32")
            clsid = _guid(CLSID_CUIAUTOMATION)
            iid = _guid(IID_IUIAUTOMATION)
            ptr = ctypes.c_void_p()
            ctypes.oledll.ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                ctypes.byref(ptr))
            self._uia = ptr if ptr else None
        except Exception:
            self._uia = None
        self.ok = self._uia is not None
        return self._uia

    def at(self, x, y, hwnd=None):
        uia = self._client()
        if uia is None:
            return None
        hwnd = _root_at(x, y) if hwnd is None else hwnd
        self.waker.nudge(hwnd)
        try:
            el = ctypes.c_void_p()
            hr = _vtbl(uia, self._ELEMENT_FROM_POINT, ctypes.c_long,
                       wintypes.POINT, ctypes.POINTER(ctypes.c_void_p))(
                uia, wintypes.POINT(int(x), int(y)), ctypes.byref(el))
            if hr != 0 or not el:
                return None
            try:
                got = self._read(el)
            finally:
                _com_release(el)
            if got:
                self.waker.mark_awake(hwnd)
            return got
        except Exception:
            return None

    def _read(self, el):
        ctype = ctypes.c_long(0)
        if _vtbl(el, self._GET_CONTROL_TYPE, ctypes.c_long,
                 ctypes.POINTER(ctypes.c_long))(el, ctypes.byref(ctype)) != 0:
            return None
        kind = UIA_KINDS.get(int(ctype.value))
        if kind is None:
            return None
        rc = wintypes.RECT()
        if _vtbl(el, self._GET_BOUNDING_RECT, ctypes.c_long,
                 ctypes.POINTER(wintypes.RECT))(el, ctypes.byref(rc)) != 0:
            return None
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w <= 0 or h <= 0 or w > MAX_TARGET_W or h > MAX_TARGET_H:
            return None
        name = ""
        try:
            bstr = ctypes.c_void_p()
            if _vtbl(el, self._GET_NAME, ctypes.c_long,
                     ctypes.POINTER(ctypes.c_void_p))(
                    el, ctypes.byref(bstr)) == 0 and bstr:
                name = ctypes.cast(bstr, ctypes.c_wchar_p).value or ""
                self._oleaut.SysFreeString(bstr)
        except Exception:
            pass
        return (rc.left, rc.top, rc.right, rc.bottom), kind, name


class TargetFinder:
    """Polls for the target near the cursor on a background thread.

    Alongside the target it keeps a little diagnostic trail — which tier
    answered, what the control is called, the last few distinct controls seen
    and how long a poll costs — because "the magnet isn't grabbing this" is
    otherwise impossible to tell apart from "the magnet never saw it". Press
    D in the preview to read it.
    """

    RECENT = 6            # distinct controls remembered for the D readout

    def __init__(self, hz=12.0, reach_px=40.0, use_msaa=True,
                 include_text_fields=False, use_uia=True):
        self.period = 1.0 / max(1.0, hz)
        self.reach_px = reach_px
        self.include_text_fields = include_text_fields
        self.waker = _Waker()
        self._msaa = _MsaaProbe(self.waker) if use_msaa else None
        self._uia = _UiaProbe(self.waker) if use_uia else None
        self._lock = threading.Lock()
        self._target = None           # (rect, kind, stamp)
        self._stop = threading.Event()
        self._thread = None
        self.errors = 0
        self.last_tier = None         # which tier answered last
        self.last_name = ""           # ...and what it called the control
        self.probe_ms = 0.0           # cost of the last poll
        self.recent = deque(maxlen=self.RECENT)

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

    def _keep(self, hit, tier):
        """Normalise a tier's answer to (rect, kind, name, tier), or None.

        Text fields are excluded by default: hooking onto them fights you
        when you are aiming at text rather than at a control."""
        if hit is None:
            return None
        rect, kind = hit[0], hit[1]
        if not self.include_text_fields and kind in ("text field",
                                                     "combo box"):
            return None
        return rect, kind, (hit[2] if len(hit) > 2 else ""), tier

    def _find(self, x, y):
        # Resolve the window once and hand it to both COM tiers: they each
        # want it for the accessibility nudge, and WindowFromPoint is not
        # free enough to pay for twice per poll.
        hwnd = _root_at(x, y)
        try:
            got = self._keep(
                caption_button_at(x, y, radius=int(self.reach_px) + 8),
                "caption")
            if got:
                return got
        except Exception:
            self.errors += 1
        try:
            got = self._keep(child_control_at(x, y), "child")
            if got:
                return got
        except Exception:
            self.errors += 1
        if self._msaa is not None:
            got = self._keep(self._msaa.at(x, y, hwnd), "msaa")
            if got:
                return got
        if self._uia is not None:
            got = self._keep(self._uia.at(x, y, hwnd), "uia")
            if got:
                return got
        return None

    def _run(self):
        ensure_com()          # per-thread; see ensure_com's docstring
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                x, y = get_cursor_pos()
                found = self._find(x, y)
            except Exception:
                self.errors += 1
                found = None
            done = time.perf_counter()
            self.probe_ms = (done - t0) * 1000.0
            with self._lock:
                self._target = (found[0], found[1], done) if found else None
            if found:
                rect, kind, name, tier = found
                self.last_tier, self.last_name = tier, name
                seen = (kind, name, rect[2] - rect[0], rect[3] - rect[1], tier)
                if seen not in self.recent:
                    self.recent.append(seen)
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

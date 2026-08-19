"""Low-latency Windows mouse control via SendInput (ctypes)."""

import ctypes
from ctypes import wintypes

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

XBUTTON1 = 0x0001   # "Back"
XBUTTON2 = 0x0002   # "Forward"

SW_MINIMIZE = 6
SW_RESTORE = 9

# Media keys — the same ones a keyboard's volume buttons send.
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _send(flags: int, dx: int = 0, dy: int = 0, data: int = 0):
    inp = INPUT(type=INPUT_MOUSE,
                union=_INPUTUNION(mi=MOUSEINPUT(dx=dx, dy=dy,
                                                mouseData=data & 0xFFFFFFFF,
                                                dwFlags=flags, time=0,
                                                dwExtraInfo=0)))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _keybd(vk: int):
    """One press+release of a virtual key."""
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# Named keys the launcher can press ("keys:esc", "keys:ctrl+w"). Letters
# and digits resolve on their own (VK codes match their ASCII uppercase),
# so this maps everything a name is needed for.
KEY_VKS = {
    "esc": 0x1B, "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "win": 0x5B, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "printscreen": 0x2C, "pause": 0x13,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
    "stopmedia": 0xB2, "mute": 0xAD, "volumeup": 0xAF, "volumedown": 0xAE,
}
KEY_VKS.update({f"f{n}": 0x70 + n - 1 for n in range(1, 25)})


def parse_key_spec(spec):
    """'ctrl+shift+t' -> [VK_CONTROL, VK_SHIFT, 0x54], or None if any part
    is unknown. Order is press order; releases happen in reverse."""
    parts = [p.strip().lower() for p in (spec or "").split("+")]
    if not parts or any(not p for p in parts):
        return None
    vks = []
    for p in parts:
        if p in KEY_VKS:
            vks.append(KEY_VKS[p])
        elif len(p) == 1 and (p.isascii() and (p.isalpha() or p.isdigit())):
            vks.append(ord(p.upper()))
        else:
            return None
    return vks


def press_keys(spec) -> bool:
    """Press a key or chord for the finger launcher: hold each part in
    order, release in reverse — 'ctrl+w' really is Ctrl held while W taps.
    Returns False (pressing nothing) on a spec it doesn't understand."""
    vks = parse_key_spec(spec)
    if not vks:
        return False
    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return True


WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                wintypes.LPARAM)


def _most_recent_iconic():
    """The most recently used minimized top-level window, or 0.

    EnumWindows walks in z-order, and iconic windows keep their z-order
    slot — so the first visible, titled, non-tool iconic window it meets
    is the one the user most recently had in front."""
    found = wintypes.HWND(0)

    def cb(hwnd, _l):
        if (_user32.IsWindowVisible(hwnd) and _user32.IsIconic(hwnd)
                and _user32.GetWindowTextLengthW(hwnd) > 0
                and not (_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                         & WS_EX_TOOLWINDOW)):
            found.value = hwnd
            return False          # stop: first hit is the newest
        return True

    _user32.EnumWindows(_ENUM_PROC(cb), 0)
    return found.value or 0


class Mouse:
    """Real mouse backend."""

    def __init__(self):
        self._minimized = []   # windows the down-flick minimized, oldest first

    def move(self, dx: int, dy: int):
        if dx or dy:
            _send(MOUSEEVENTF_MOVE, dx, dy)

    def left_down(self):
        _send(MOUSEEVENTF_LEFTDOWN)

    def left_up(self):
        _send(MOUSEEVENTF_LEFTUP)

    def right_down(self):
        _send(MOUSEEVENTF_RIGHTDOWN)

    def right_up(self):
        _send(MOUSEEVENTF_RIGHTUP)

    def wheel(self, delta: int):
        """Vertical scroll; positive = up, 120 = one notch (fractions OK)."""
        if delta:
            _send(MOUSEEVENTF_WHEEL, data=delta)

    def hwheel(self, delta: int):
        """Horizontal scroll; positive = right."""
        if delta:
            _send(MOUSEEVENTF_HWHEEL, data=delta)

    def _xclick(self, xbutton: int):
        _send(MOUSEEVENTF_XDOWN, data=xbutton)
        _send(MOUSEEVENTF_XUP, data=xbutton)

    def back(self):
        """Browser/Explorer Back (mouse button 4)."""
        self._xclick(XBUTTON1)

    def forward(self):
        """Browser/Explorer Forward (mouse button 5)."""
        self._xclick(XBUTTON2)

    def minimize_window(self):
        """Minimize whatever window is currently in the foreground, and
        remember it so the pull-up gesture can bring it back."""
        hwnd = _user32.GetForegroundWindow()
        if hwnd:
            _user32.ShowWindow(hwnd, SW_MINIMIZE)
            self._minimized.append(hwnd)
            del self._minimized[:-8]    # only the recent few matter

    def restore_window(self) -> bool:
        """Un-minimize a window: the most recent one THIS app minimized,
        or — when its own memory is empty (fresh start, window minimized
        by hand, taskbar click) — the most recently used minimized window
        on the desktop. Without the fallback the gesture silently did
        nothing in exactly the situation someone first tries it."""
        while self._minimized:
            hwnd = self._minimized.pop()
            if _user32.IsWindow(hwnd) and _user32.IsIconic(hwnd):
                _user32.ShowWindow(hwnd, SW_RESTORE)
                _user32.SetForegroundWindow(hwnd)
                return True
        hwnd = _most_recent_iconic()
        if hwnd:
            _user32.ShowWindow(hwnd, SW_RESTORE)
            _user32.SetForegroundWindow(hwnd)
            return True
        return False

    def center(self):
        """Park the pointer in the middle of the primary screen.

        SetCursorPos rather than a computed SendInput move: the point of the
        gesture is "wherever the cursor ended up, bring it home", and an
        absolute set cannot accumulate error the way a relative jump can."""
        w = _user32.GetSystemMetrics(0)      # SM_CXSCREEN
        h = _user32.GetSystemMetrics(1)      # SM_CYSCREEN
        _user32.SetCursorPos(w // 2, h // 2)

    def volume(self, steps: int):
        """System volume, one notch per step (+ up, - down).

        Sends the media keys rather than touching a mixer API, so it moves
        the same volume Windows' own on-screen indicator shows, works in
        every app, and needs no COM."""
        if not steps:
            return
        vk = VK_VOLUME_UP if steps > 0 else VK_VOLUME_DOWN
        for _ in range(min(abs(steps), 20)):    # sanity cap on one frame
            _keybd(vk)


class NullMouse:
    """Recording backend for tests / paused mode — no real input sent."""

    def __init__(self):
        self.events = []

    def move(self, dx: int, dy: int):
        if dx or dy:
            self.events.append(("move", dx, dy))

    def left_down(self):
        self.events.append(("down",))

    def left_up(self):
        self.events.append(("up",))

    def right_down(self):
        self.events.append(("rdown",))

    def right_up(self):
        self.events.append(("rup",))

    def wheel(self, delta: int):
        if delta:
            self.events.append(("wheel", delta))

    def hwheel(self, delta: int):
        if delta:
            self.events.append(("hwheel", delta))

    def back(self):
        self.events.append(("back",))

    def forward(self):
        self.events.append(("forward",))

    def minimize_window(self):
        self.events.append(("minimize",))

    def restore_window(self) -> bool:
        self.events.append(("restore",))
        return True

    def center(self):
        self.events.append(("center",))

    def volume(self, steps: int):
        if steps:
            self.events.append(("volume", steps))


def get_cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

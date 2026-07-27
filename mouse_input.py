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


class Mouse:
    """Real mouse backend."""

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
        """Minimize whatever window is currently in the foreground."""
        hwnd = _user32.GetForegroundWindow()
        if hwnd:
            _user32.ShowWindow(hwnd, SW_MINIMIZE)

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

    def volume(self, steps: int):
        if steps:
            self.events.append(("volume", steps))


def get_cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

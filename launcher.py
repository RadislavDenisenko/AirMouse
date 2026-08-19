"""Off-hand finger-count launcher: hold up N fingers to fire slot N.

The non-dominant hand raises 1-4 fingers (thumb ignored) and, held briefly,
triggers the command bound to that COUNT — it's how many fingers are up, not
which ones: 1 = one finger (usually the index), 2 = index+middle, 3 =
index+middle+ring, 4 = all four. Any combination that adds up to the same
count works. Returns the slot index; the caller owns running the configured
command. Debounced so a passing pose doesn't fire, and latched so one
continuous hold fires exactly once.

`OffhandIntent` is the gatekeeper in front of all that: the difference
between a hand that is COMMANDING and a hand that is just living. A person
in front of this app scratches their face, drinks, talks with their hands,
leans on a fist — all of it puts a raised hand with some fingers out in
front of the camera. What none of it does is hold a still, palm-forward,
fingers-up pose: the palm faces the body while scratching (the camera sees
the BACK of the hand), fingers angle sideways around whatever they touch,
and idle gestures move. So intent = palm squarely to the camera + counted
fingers pointing up + the hand near-still — and anywhere near the face the
launcher simply goes silent, no counting and no coaching, because touching
your own face is never a command.
"""

import math

from gestures import (fingers_extended, hand_size, hand_width, palm_center,
                      thumb_extended, INDEX_MCP, PINKY_MCP)

SLOT_LABELS = ("1 finger", "2 fingers", "3 fingers", "4 fingers")  # slots 0..3

_FINGER_BASE = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}


def palm_to_camera(pts, is_left_hand=True, margin_frac=0.35):
    """Is the palm squarely shown to the camera?

    The frame is mirrored before detection, so this is mirror geometry:
    raise your LEFT hand palm-to-the-mirror, fingers up, and the thumb —
    and with it the index knuckle — appears on the image's RIGHT side of
    the hand; turn the palm toward yourself (the scratching pose) and the
    knuckle order flips. The margin is normalised by wrist-to-knuckle
    LENGTH, not knuckle span — the span itself collapses as the hand
    turns edge-on, which is exactly the case that must fail, so it can't
    be its own yardstick. A sideways hand is nobody's command."""
    span = pts[PINKY_MCP][0] - pts[INDEX_MCP][0]
    m = margin_frac * hand_size(pts)
    return span < -m if is_left_hand else span > m


def fingers_upright(pts, slope=1.2):
    """Do ALL the extended fingers point roughly straight up?

    A deliberate count is fingers to the sky. Fingers wrapped around a
    cup, splayed mid-gesture, or dug into an itch angle off-vertical —
    any one of them leaning past ~40 degrees fails the whole hand."""
    ext = fingers_extended(pts)
    names = [n for n, e in ext.items() if e]
    if not names:
        return False
    for n in names:
        b = _FINGER_BASE[n]
        vx = pts[b + 3][0] - pts[b][0]
        vy = pts[b + 3][1] - pts[b][1]
        if -vy < slope * abs(vx):        # y grows downward
            return False
    return True


class OffhandIntent:
    """Is the off hand deliberately commanding, or just living?

    Feed it the launcher hand every frame (None when there isn't an
    eligible one). `update` returns True only for a still, palm-forward,
    fingers-up pose away from the face. `hint` holds one short coaching
    line — set only when someone is plausibly TRYING (a countable pose,
    held a beat, not at the face) and exactly one thing is off, so the
    preview can teach the pose instead of silently ignoring it."""

    SPEED_TAU = 0.15          # EMA on hand speed (s)
    STILL_HW_S = 1.0          # slower than this counts as held still
    FACE_PAD = 0.45           # face box grows by this share of its size
    HINT_AFTER_S = 0.4        # how long a near-miss persists before coaching

    def __init__(self, is_left_hand=True):
        self.is_left_hand = is_left_hand
        self.speed_hw = 0.0
        self.hint = None
        self._prev = None                 # (palm, t)
        self._problem_since = {}          # problem -> first seen

    def _quiet(self):
        self.hint = None
        self._problem_since.clear()

    def update(self, pts, now, face_box=None):
        if pts is None:
            self._prev = None
            self.speed_hw = 0.0
            self._quiet()
            return False
        palm = palm_center(pts)
        hw = hand_width(pts)
        if self._prev is not None and hw > 1e-6:
            (px, py), pt = self._prev
            dt = min(0.1, max(1e-3, now - pt))
            v = math.hypot(palm[0] - px, palm[1] - py) / hw / dt
            self.speed_hw += ((v - self.speed_hw)
                              * (1.0 - math.exp(-dt / self.SPEED_TAU)))
        self._prev = (palm, now)

        # Anywhere near the face the launcher is simply not listening —
        # no counting and no coaching. Touching your own face is never a
        # command, and being nagged mid-eye-rub would be worse than a
        # missed launch.
        if face_box is not None:
            x0, y0, x1, y1 = face_box
            pad = self.FACE_PAD * max(x1 - x0, y1 - y0)
            if (x0 - pad <= palm[0] <= x1 + pad
                    and y0 - pad <= palm[1] <= y1 + pad):
                self._quiet()
                return False

        problems = []
        if not palm_to_camera(pts, self.is_left_hand):
            problems.append("show your palm to the camera")
        if not fingers_upright(pts):
            problems.append("point your fingers straight up")
        if self.speed_hw > self.STILL_HW_S:
            problems.append("hold your hand still")
        if not problems:
            self._quiet()
            return True

        # Coach only a plausible attempt: a countable number of fingers,
        # one persistent problem — not every passing wave.
        n = sum(fingers_extended(pts).values())
        self._problem_since = {p: self._problem_since.get(p, now)
                               for p in problems}
        first = problems[0]
        if (1 <= n <= 4
                and now - self._problem_since[first] >= self.HINT_AFTER_S):
            self.hint = first
        else:
            self.hint = None
        return False


class FingerLauncher:
    def __init__(self, hold_s: float = 0.3, cooldown_s: float = 1.0):
        self.hold_s = hold_s
        self.cooldown_s = cooldown_s
        self._candidate = None      # slot index being held
        self._since = None          # when this count's hold began
        self._fired_for = None      # slot already fired this continuous hold
        self._cooldown_until = 0.0
        self.current = None         # current slot (finger count - 1), overlay
        self.progress = 0.0         # 0..1 hold progress (overlay)

    def _slot(self, pts):
        """Slot 0..3 from how many fingers are up (1..4), or None.

        A fully open hand — four fingers AND the thumb — is neutral, not
        slot 4. Raising an open hand is the most natural thing to do in
        front of this app (it is literally how the cursor engages), and
        counting it as a command meant simply showing your left hand
        launched whatever lived in slot 4. Tucking the thumb while the four
        fingers stay up is what says "this is the count, on purpose"."""
        if pts is None:
            return None
        n = sum(fingers_extended(pts).values())
        if n == 4 and thumb_extended(pts):
            return None
        return n - 1 if 1 <= n <= 4 else None

    def reset(self):
        self._candidate = self._since = self._fired_for = None
        self.current = None
        self.progress = 0.0

    def update(self, pts, now):
        """Feed the off-hand points (or None). Returns a fired slot 0..3 once
        per deliberate hold, else None."""
        idx = self._slot(pts)
        self.current = idx
        if idx is None:
            self._candidate = self._since = self._fired_for = None
            self.progress = 0.0
            return None
        if idx != self._candidate:
            self._candidate, self._since, self._fired_for = idx, now, None
        self.progress = (min(1.0, (now - self._since) / self.hold_s)
                         if self._since is not None and self.hold_s > 0 else 1.0)
        if now < self._cooldown_until or self._fired_for == idx:
            return None
        if self._since is not None and now - self._since >= self.hold_s:
            self._fired_for = idx
            self._cooldown_until = now + self.cooldown_s
            return idx
        return None

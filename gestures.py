"""Gesture state machine for the cursor (dominant) hand.

States:
  IDLE      - no hand / hand lowered. Cursor untouched.
  ENGAGING  - flat spread "high-five" palm seen; waiting for the hold time.
  TRACKING  - anchor locked. Sub-modes: POINT (cursor) and SCROLL (wheel).

Motion model (POINT): a self-scaling anchor circle whose radius tracks the
hand's size (so it feels the same near or far from the camera). Sensitivity
ramps from `sensitivity` at the center up to `sensitivity * edge_multiplier`
at the rim, so the middle is precise and the edge is fast. Reach the rim and
the circle trails along with the hand (re-anchors), so you can cross the whole
screen without the cursor ever moving on its own.

Also on the cursor hand: thumb+index pinch = left click/drag, thumb+middle =
right click, peace sign = joystick scroll (hold the hand off the zero point
and the page scrolls at a constant speed — farther = faster; moving
decisively back toward the zero RECENTERS it to the hand, so reversing
direction is instant instead of a trek back across the old zero).

Navigation is FIST-ARMED: clench a fist first (a relaxed grab is enough),
then swipe left/right for browser Forward/Back, or push down and let the
hand land to minimize the active window. An open hand moving fast is just
cursor motion — it can never fire navigation. The fist stays armed while
held, so repeated swipes (back-back-back) work without re-clenching; only
the per-gesture refractory paces them.
"""

import math

from hand_tracker import (WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP,
                          MIDDLE_TIP, MIDDLE_MCP, RING_TIP, PINKY_MCP,
                          PINKY_TIP)
from one_euro import OneEuro2D

# Landmark indices: (tip, pip) per finger
_FINGERS = {"index": (8, 6), "middle": (12, 10), "ring": (16, 14), "pinky": (20, 18)}

IDLE = "IDLE"
ENGAGING = "ENGAGING"
TRACKING = "TRACKING"
PINCHED = "PINCHED"    # left button held by thumb+index pinch
RCLICK = "R-CLICK"     # right button held by thumb+middle pinch
ARMED = "ARMED"        # clenched fist: swipe navigation armed, cursor frozen
SCROLL = "SCROLL"      # peace-sign scroll mode
VOLUME = "VOLUME"      # thumb+ring pinch held: system volume
BRAKING = "BRAKING"    # middle+ring+pinky squeezed: precision slowdown

POINT = "POINT"        # TRACKING sub-mode: normal cursor control


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def palm_center(pts):
    """Stable hand position: midpoint of wrist and middle-finger base knuckle."""
    w, m = pts[WRIST], pts[MIDDLE_MCP]
    return ((w[0] + m[0]) / 2.0, (w[1] + m[1]) / 2.0)


def fingers_extended(pts):
    """Which fingers are extended: tip farther from wrist than its PIP joint."""
    wrist = pts[WRIST]
    return {name: _dist(pts[tip], wrist) > _dist(pts[pip], wrist)
            for name, (tip, pip) in _FINGERS.items()}


def hand_size(pts):
    """Wrist to middle knuckle — the scale reference for all ratios. Always
    available (pose-independent), so it also sets the anchor-circle radius."""
    return max(1e-6, _dist(pts[WRIST], pts[MIDDLE_MCP]))


def hand_width(pts):
    """Knuckle span (index MCP to pinky MCP) — the 'hand-width' unit used to
    make the swipe distance-invariant to how far you are from the camera."""
    return max(1e-6, _dist(pts[INDEX_MCP], pts[PINKY_MCP]))


def spread_ratio(pts):
    """Thumb-tip to pinky-tip span / hand size. A splayed high-five hand is
    large (~2.2+); a loose or fingers-together hand is much smaller."""
    return _dist(pts[THUMB_TIP], pts[PINKY_TIP]) / hand_size(pts)


def pinch_ratio(pts, tip=INDEX_TIP):
    """Thumb-tip to fingertip distance normalized by hand size
    — invariant to distance from camera."""
    return _dist(pts[THUMB_TIP], pts[tip]) / hand_size(pts)


def curl_ratio(pts):
    """Mean fingertip-to-wrist distance / hand size. Open hand ~1.8+,
    clenched fist below ~1.0."""
    w = pts[WRIST]
    tips = (pts[8], pts[12], pts[16], pts[20])
    return sum(_dist(t, w) for t in tips) / (4.0 * hand_size(pts))


BRAKE_FINGERS = ("middle", "ring", "pinky")


def brake_curl(pts):
    """How deeply the middle, ring and pinky are curled, in hand-size units.

    Mean of (PIP-to-wrist minus tip-to-wrist) across those three fingers:
    negative when they are straight, rising through zero as they fold in. It
    measures curl DEPTH continuously rather than classifying it, which is
    what lets the brake be proportional instead of a switch.

    The index and thumb are deliberately excluded. That is the whole trick
    behind squeezing and clicking at the same time: pinching curls the index,
    and if the index counted, every click would release the brake at the
    exact moment you needed it.
    """
    w = pts[WRIST]
    hs = hand_size(pts)
    total = 0.0
    for name in BRAKE_FINGERS:
        tip, pip = _FINGERS[name]
        total += (_dist(pts[pip], w) - _dist(pts[tip], w)) / hs
    return total / len(BRAKE_FINGERS)


def hand_raised(pts, frame_h):
    """Is this hand actually held UP, knuckles clearly above the wrist?

    The launcher used to count fingers on any detected hand, and from a
    couple of metres back the camera can see a hand resting on a knee well
    enough to read fingers on it — so sitting still launched apps. A resting
    hand lies flat or tilted; a hand deliberately shown to the camera hangs
    from a raised forearm, knuckles on top. Palm or back of the hand both
    pass; orientation is not the point, elevation is."""
    return pts[MIDDLE_MCP][1] < pts[WRIST][1] - 0.05 * frame_h


def thumb_extended(pts, min_ratio=0.5):
    """Is the thumb clearly stuck out, away from the palm?

    A tucked thumb lies across the palm near the finger bases; an extended
    one reaches well clear of the index knuckle. Distance-normalised so it
    holds at any range. Less reliable than the finger tests (the thumb's
    landmarks wobble), which is why most poses ignore it — the two that
    can't are the launcher's open-hand neutral and the reset sign."""
    return _dist(pts[THUMB_TIP], pts[INDEX_MCP]) / hand_size(pts) >= min_ratio


def finger_folded(pts, name, margin=0.35):
    """Is this finger FULLY folded into the palm — not merely 'not extended'?

    `fingers_extended` answers a binary tip-past-knuckle question, and its
    complement includes every half-curled in-between shape a relaxed hand
    makes. Gestures that must never fire by accident need the stronger
    test: the tip pulled back inside the knuckle by a real margin (in
    hand-size units), which only a deliberate fold produces."""
    tip, pip = _FINGERS[name]
    w = pts[WRIST]
    return (_dist(pts[pip], w) - _dist(pts[tip], w)) / hand_size(pts) >= margin


def is_reset_pose(pts):
    """Thumb and pinky out, everything else FULLY folded — the shaka.

    This replaced the rock-and-roll horns, which fired from a barely-curled
    resting hand: 'not extended' let half-bent fingers through, and horns
    share too much outline with casual poses. The shaka demands opposite
    extremes from the hand at once — the two outermost digits stretched,
    all three middle fingers folded to the palm — which nothing relaxed
    produces."""
    ext = fingers_extended(pts)
    return (ext["pinky"] and thumb_extended(pts)
            and not ext["index"]
            and all(finger_folded(pts, f) for f in ("index", "middle", "ring")))


class RockerDetector:
    """Hold the shaka briefly to teleport the cursor to the screen centre.

    A relative-motion mouse has no home position, so after a long session
    the mapping between where your arm is comfortable and where the cursor
    sits can drift somewhere awkward. This is the reset.

    The hold is short (~0.3s) but real: fingers pass through all sorts of
    shapes between poses, and firing on a single frame of accidental horns
    would fling the cursor mid-gesture — the same transient-pose lesson the
    brake taught. A refractory stops the held pose re-firing every frame.
    """

    def __init__(self, enabled=True, hold_s=0.3, refractory_s=1.0):
        self.enabled = enabled
        self.hold_s = hold_s
        self.refractory_s = refractory_s
        self.reset()

    def reset(self):
        self._t0 = None
        self._until = 0.0
        self.progress = 0.0

    def update(self, pts, now, gate_on=True):
        """Feed one frame; returns True on the frame the recenter fires."""
        if not self.enabled or pts is None or not gate_on \
                or not is_reset_pose(pts):
            self._t0 = None
            self.progress = 0.0
            return False
        if now < self._until:
            self.progress = 0.0
            return False
        if self._t0 is None:
            self._t0 = now
        self.progress = min(1.0, (now - self._t0) / max(1e-6, self.hold_s))
        if now - self._t0 >= self.hold_s:
            self._t0 = None
            self.progress = 0.0
            self._until = now + self.refractory_s
            return True
        return False


def is_brake_pose(pts):
    """Precision-brake shape: index clear of the curl, middle folded into it.

    Both tests earn their place. The index one separates a brake from a
    fist, which curls everything. The middle one separates it from a peace
    sign — that pose folds ring and pinky away, so it reads as a deep curl
    on the three-finger average even though it means scroll. Gating on shape
    rather than on curl depth is what lets the onset threshold stay low
    enough to feel responsive without collecting other poses on the way.
    """
    ext = fingers_extended(pts)
    return ext["index"] and not ext["middle"]


class BrakeDetector:
    """Squeeze the middle, ring and pinky to slow the cursor down.

    A precision brake for small targets — the manual alternative to
    magnetism. It is CONTINUOUS (curl further, go slower) but only past an
    onset, because fingers rest half-curled during ordinary pointing and that
    must never brake on its own. Below `onset` nothing happens at all; from
    `onset` to `full` the slowdown ramps in to `min_scale`.

    Two details that matter as much as the mapping:

      * **Smoothing.** Curl depth is noisy frame to frame, and feeding it
        straight through would make pointer speed shimmer while you hold a
        steady squeeze. The amount is eased toward its target instead.
      * **Debounced entry.** The pose has to be HELD before it counts. A
        hand closing into a fist passes through the brake shape on the way
        — the index finishes last, so for a frame or two the index is out
        while the middle is already folded. Engaging on that transient
        latched the brake, which gated fist arming off and killed swipe
        navigation until the hand was opened again. Every other pose here
        debounces for the same reason; this one is no different.
      * **Bounded stickiness.** A pinch curls the index, so an established
        brake must survive its own click. That licence is deliberately
        limited: indefinite while a button is actually held, and only
        `stick_frames` long otherwise, so a slow clench that does manage to
        establish the brake still lets go of it by itself.

    A still hand is unaffected either way: this scales motion, and scaling
    zero is zero.
    """

    def __init__(self, enabled=True, onset=0.08, full=0.55, min_scale=0.15,
                 smoothing=0.35, enter_frames=4, stick_frames=8):
        self.enabled = enabled
        self.onset = onset
        self.full = full
        self.min_scale = min_scale
        self.smoothing = smoothing
        self.enter_frames = enter_frames
        self.stick_frames = stick_frames
        self.reset()

    def reset(self):
        self.amount = 0.0        # 0..1, how far into the brake we are
        self.raw = 0.0           # last curl reading, for the D readout
        self.posed = False       # is the hand shaped for braking right now?
        self._hold = 0           # consecutive frames holding the pose
        self._slip = 0           # consecutive frames out of it while engaged

    @property
    def scale(self):
        """Multiplier to apply to cursor motion: 1.0 = untouched.

        The floor is clamped into (0, 1]: a hand-edited config with a
        negative or above-one min_scale would otherwise invert or amplify
        cursor motion rather than slowing it.
        """
        floor = min(1.0, max(0.05, self.min_scale))
        return 1.0 - self.amount * (1.0 - floor)

    @property
    def engaged(self):
        return self.amount > 0.02

    def update(self, pts, pose_ok=True, hold_ok=False):
        """One frame. `pose_ok` says the hand is shaped for braking right
        now; `hold_ok` says a button is being held, which licenses the brake
        to stay on while the pinch keeps the pose from reading. Returns the
        brake amount."""
        if not self.enabled or pts is None:
            target = 0.0
            self.posed = False
            self._hold = self._slip = 0
        else:
            self.raw = brake_curl(pts)
            if pose_ok:
                self._hold += 1
                self._slip = 0
            else:
                self._hold = 0
                self._slip += 1
            ready = self._hold >= self.enter_frames
            sticky = self.engaged and (hold_ok
                                       or self._slip <= self.stick_frames)
            self.posed = ready or sticky
            span = max(1e-6, self.full - self.onset)
            target = ((self.raw - self.onset) / span) if self.posed else 0.0
            target = min(1.0, max(0.0, target))
        self.amount += self.smoothing * (target - self.amount)
        if self.amount < 1e-3:
            self.amount = 0.0
        return self.amount

    @property
    def metrics(self):
        return {"curl": self.raw, "amount": self.amount,
                "scale": self.scale, "posed": self.posed}


def is_scroll_pose(pts):
    """Peace sign: index+middle extended, ring+pinky curled."""
    ext = fingers_extended(pts)
    return ext["index"] and ext["middle"] and not ext["ring"] and not ext["pinky"]


def scroll_pose_score(pts):
    """Continuous peace-sign margin in hand-size units: positive = peace-like
    (index+middle clearly extended AND ring+pinky clearly curled), negative =
    anything else. It is the SMALLEST margin across the four fingers — i.e.
    the distance to the nearest classification flip — so thresholding it with
    a hysteresis band tolerates the landmark noise that flips the raw boolean
    when a half-curled ring finger sits near its PIP joint."""
    hs = hand_size(pts)
    w = pts[WRIST]
    margins = []
    for name, (tip, pip) in _FINGERS.items():
        ext = (_dist(pts[tip], w) - _dist(pts[pip], w)) / hs
        margins.append(ext if name in ("index", "middle") else -ext)
    return min(margins)


class _HysteresisSwitch:
    """Generic debounced hysteresis: ON below down_thresh, OFF above
    up_thresh; a candidate state must hold `debounce_frames` consecutive
    frames to commit. With `require_prime` (default), must see the OFF side
    once before the first ON, so a hand that appears already closed can't
    ghost-trigger — right for click gestures, wrong for the fist (which only
    ARMS navigation and should register even if the hand enters frame
    already clenched)."""

    def __init__(self, down_thresh, up_thresh, debounce_frames,
                 require_prime=True):
        self.down_thresh = down_thresh
        self.up_thresh = up_thresh
        self.debounce_frames = debounce_frames
        self.require_prime = require_prime
        self.reset()

    def reset(self):
        self.is_on = False
        self.value = None
        self._armed = not self.require_prime
        self._candidate = None
        self._count = 0

    def cancel_pending(self):
        self._candidate, self._count = None, 0

    def update(self, value, gate_on=True):
        """Feed one frame's value. Returns 'on', 'off', or None (no edge).
        gate_on only gates turning ON; releases are never gated."""
        self.value = value
        if not self._armed:
            if value > self.up_thresh:
                self._armed = True
            return None

        if self.is_on:
            candidate = False if value > self.up_thresh else None
        else:
            candidate = True if (value < self.down_thresh and gate_on) else None

        if candidate is None:
            self._candidate, self._count = None, 0
            return None
        if candidate != self._candidate:
            self._candidate, self._count = candidate, 1
        else:
            self._count += 1
        if self._count >= self.debounce_frames:
            self.is_on = candidate
            self._candidate, self._count = None, 0
            return "on" if candidate else "off"
        return None


class PinchDetector:
    """Thumb-to-fingertip pinch. tip selects the finger: INDEX_TIP = left
    click, MIDDLE_TIP = right click.

    up_ratio default 0.38: a naturally relaxed open hand measures ~0.43, so a
    release threshold of 0.45 would leave drags stuck; 0.38 keeps a solid
    hysteresis band above the 0.28 down threshold while releasing reliably."""

    def __init__(self, down_ratio: float = 0.28, up_ratio: float = 0.38,
                 debounce_frames: int = 2, tip: int = INDEX_TIP):
        self._sw = _HysteresisSwitch(down_ratio, up_ratio, debounce_frames)
        self.tip = tip

    @property
    def is_down(self):
        return self._sw.is_on

    @property
    def ratio(self):
        return self._sw.value

    @property
    def down_ratio(self):
        return self._sw.down_thresh

    @property
    def up_ratio(self):
        return self._sw.up_thresh

    def reset(self):
        self._sw.reset()

    def cancel_pending(self):
        self._sw.cancel_pending()

    def update(self, pts, gate_on=True):
        """Feed one frame. Returns 'down', 'up', or None (no edge)."""
        edge = self._sw.update(pinch_ratio(pts, self.tip), gate_on)
        return {"on": "down", "off": "up", None: None}[edge]


class FistDetector:
    """Clenched fist on the curl ratio (all fingertips pulled to the wrist).
    Since v3.2 the fist no longer drags — it ARMS swipe navigation. The
    hysteresis band (clench below down_ratio, reopen above up_ratio) keeps
    the armed state stable through the motion blur of a fast stroke.

    v3.3: priming is off — raising an already-clenched hand arms
    immediately, since arming can't click anything by itself.

    v3.4: arming also requires NO extended fingers. curl_ratio alone cannot
    separate the gestures — a relaxed "doorknob" grab measures ~1.2 while a
    peace sign measures ~1.35, so any threshold loose enough to catch the
    grab also catches the scroll pose. Finger count separates them cleanly
    (grab = 0 extended, peace = 2), which lets the curl band be generous
    enough for a comfortable grab instead of demanding a hard clench."""

    def __init__(self, down_ratio: float = 1.10, up_ratio: float = 1.50,
                 debounce_frames: int = 2):
        self._sw = _HysteresisSwitch(down_ratio, up_ratio, debounce_frames,
                                     require_prime=False)
        self.n_ext = 0

    @property
    def is_down(self):
        return self._sw.is_on

    @property
    def ratio(self):
        return self._sw.value

    def reset(self):
        self._sw.reset()
        self.n_ext = 0

    def cancel_pending(self):
        self._sw.cancel_pending()

    def update(self, pts, gate_on=True):
        """Releases are never gated on finger count — a motion-blurred frame
        mid-swipe must not be able to disarm the grab."""
        self.n_ext = sum(fingers_extended(pts).values())
        edge = self._sw.update(curl_ratio(pts), gate_on and self.n_ext == 0)
        return {"on": "down", "off": "up", None: None}[edge]


class SwipeDetector:
    """Fist-armed horizontal swipe, measured in hand-widths so the distance
    is the same near or far from the camera.

    Deliberately forgiving about tracking quality: fast arm motion blurs the
    hand and tracking can drop out for a frame or two mid-stroke. We keep a
    rolling window of positions, tolerate short gaps, require the ARMED flag
    (fist held, debounced by the caller) for only a *fraction* of the stroke,
    and use the median hand-width (blurred frames measure wide).

    v3.3 metering fix: the stroke is measured from the BEST start sample in
    the window, not the oldest. Measuring only from the oldest made "speed"
    really travel/window — a hidden distance bar of min_speed*window that
    silently overrode hand_widths (dead config) and forced the user to hold
    the fist perfectly still first. Scanning starts makes distance and speed
    independent, honest gates. Axis choice is dominant-axis (|dx| >= |dy|),
    shared with FlickDownDetector's dy > |dx|, so every stroke direction
    belongs to exactly one gesture — no dead diagonal cone.

    Fires 'forward'/'back' once per stroke; exposes `.active` while a stroke
    is building so the caller can freeze the cursor instead of smearing it."""

    def __init__(self, hand_widths: float = 1.8, window_s: float = 0.7,
                 min_speed_hw_s: float = 5.0,
                 refractory_s: float = 0.8, invert: bool = False,
                 armed_frac: float = 0.6, gap_tolerance_s: float = 0.2):
        self.hand_widths = hand_widths
        self.window_s = window_s
        self.min_speed_hw_s = min_speed_hw_s
        self.refractory_s = refractory_s
        self.invert = invert
        self.armed_frac = armed_frac
        self.gap_tolerance_s = gap_tolerance_s
        self.reset()

    def reset(self):
        self._hist = []               # (t, x, y, hand_width, armed)
        self._refractory_until = 0.0
        self.active = False
        self.progress = 0.0           # 0..1 stroke completion (overlay/debug)
        # best candidate this frame, for the on-screen debug readout
        self.metrics = {"travel": 0.0, "speed": 0.0, "armed": 0.0}

    def update(self, palm, hw, armed, now):
        """palm=(x,y) px (or None on a dropout), hw=hand-width px, armed=
        fist held this frame. Returns 'forward' | 'back' | None."""
        self.active = False
        self.progress = 0.0
        if now < self._refractory_until:
            self._hist = []
            return None
        if palm is None or hw <= 1e-6:
            # tolerate a short tracking gap mid-stroke; clear only when stale
            if self._hist and now - self._hist[-1][0] > self.gap_tolerance_s:
                self._hist = []
            return None
        if self._hist and now - self._hist[-1][0] > self.gap_tolerance_s:
            self._hist = []           # too-old history = a different motion
        x, y = palm
        self._hist.append((now, x, y, hw, bool(armed)))
        cutoff = now - self.window_s
        self._hist = [h for h in self._hist if h[0] >= cutoff]
        if len(self._hist) < 3:
            return None

        widths = sorted(h[3] for h in self._hist)
        hw_ref = widths[len(widths) // 2]

        fired_dx = None
        best = {"travel": 0.0, "speed": 0.0, "armed": 0.0}
        for i in range(len(self._hist) - 2):
            t0, x0, y0 = self._hist[i][:3]
            span = now - t0
            if span <= 1e-3:
                continue
            dx, dy = x - x0, y - y0
            if abs(dx) < abs(dy):          # not horizontally dominant
                continue
            sub = self._hist[i:]
            armed_share = sum(1 for h in sub if h[4]) / len(sub)
            travel_hw = abs(dx) / hw_ref
            speed_hw_s = travel_hw / span
            if travel_hw > best["travel"]:
                best = {"travel": travel_hw, "speed": speed_hw_s,
                        "armed": armed_share}
            self.progress = max(self.progress,
                                min(1.0, travel_hw / self.hand_widths))
            # a plausible stroke is building: suppress cursor motion early so
            # the swipe doesn't drag the pointer across the screen first
            if (travel_hw >= 0.35 * self.hand_widths
                    and speed_hw_s >= 0.6 * self.min_speed_hw_s
                    and armed_share >= 0.5 * self.armed_frac):
                self.active = True
            # firing requires the start sample itself armed: the grab must
            # precede the stroke, not join it halfway
            if (travel_hw >= self.hand_widths
                    and speed_hw_s >= self.min_speed_hw_s
                    and armed_share >= self.armed_frac
                    and self._hist[i][4]):
                fired_dx = dx
                break
        self.metrics = best

        if fired_dx is not None:
            # mirrored frame: hand moving right->left (dx<0) = Forward
            forward = (fired_dx < 0) != self.invert
            self._refractory_until = now + self.refractory_s
            self._hist = []
            self.active = True
            self.progress = 1.0
            return "forward" if forward else "back"
        return None


class FlickDownDetector:
    """Grab and push down -> minimize; grab and pull up -> restore.

    One detector class, two instances: `direction` +1 is the downward push
    (fires "minimize"), -1 the upward tug (fires "restore" — bring back
    the window the push most recently hid).

    v3.3: tuned for a SHORT deliberate push (~0.9 hand-widths, ~7 cm) at a
    moderate speed floor, using the same best-start metering fix as
    SwipeDetector (measuring only from the oldest sample made the speed
    floor a hidden ~2.3-hand-width distance bar that overrode hand_widths).

    LANDING CHECK (`require_landing`, now OFF by default): the push used to
    wait for the hand to decelerate and settle before firing. That reliably
    stopped a dropped arm minimising a window — but it also made this the
    hardest gesture in the app to do deliberately, because "push and then
    hold still" is not what the movement feels like. Set it back to True to
    restore the old behaviour.

    CLENCH FIRST (`arm_age_s`): the fist must have been armed for this long
    BEFORE the stroke began. Firing on the pull alone let a lowered arm
    minimise a window: a hand relaxes into a loose curl on its way down,
    arms mid-drop, and the remainder of the drop is a fast, downward,
    armed stroke. Requiring the clench to predate the stroke separates
    "clench, then pull" (deliberate) from "closes while falling"
    (an arm going to a lap) — the one signal a dropped arm cannot fake.

    Axis: fires on the dominant vertical (|dy| > |dx| in the chosen
    direction), complementing the swipe's |dx| >= |dy| — every direction
    belongs to exactly one gesture."""

    def __init__(self, hand_widths: float = 0.9, window_s: float = 0.40,
                 min_speed_hw_s: float = 4.5,
                 refractory_s: float = 1.0, armed_frac: float = 0.6,
                 gap_tolerance_s: float = 0.15,
                 settle_speed_hw_s: float = 1.5,
                 settle_timeout_s: float = 0.4,
                 require_landing: bool = False,
                 arm_age_s: float = 0.15,
                 direction: int = 1, fires: str = "minimize"):
        self.require_landing = require_landing
        self.hand_widths = hand_widths
        self.window_s = window_s
        self.min_speed_hw_s = min_speed_hw_s
        self.refractory_s = refractory_s
        self.armed_frac = armed_frac
        self.gap_tolerance_s = gap_tolerance_s
        self.settle_speed_hw_s = settle_speed_hw_s
        self.settle_timeout_s = settle_timeout_s
        self.arm_age_s = arm_age_s
        self.direction = 1 if direction >= 0 else -1
        self.fires = fires
        self.reset()

    def reset(self):
        self._hist = []               # (t, x, y, hand_width, armed)
        self._refractory_until = 0.0
        self.active = False
        self._pending_t0 = None       # push seen; waiting for the landing
        self.metrics = {"travel": 0.0, "speed": 0.0, "armed": 0.0}

    def _cancel_pending(self):
        self._pending_t0 = None
        self._hist = []

    def hold_off(self, until):
        """External suppression: another gesture just fired and its follow-
        through must not read as a flick. The dominant case is the arm drop
        after a swipe — the fist stays armed while held (by design, so
        back-back-back works), and lowering the still-closed hand out of
        frame is downward-dominant, fast, and easily far enough to satisfy
        the tug thresholds. The swipe's own refractory does not cover this
        detector, so the caller extends ours instead."""
        self._refractory_until = max(self._refractory_until, until)
        self._cancel_pending()

    def update(self, palm, hw, armed, now, armed_since=None):
        """Returns `fires` once per deliberate clench-then-stroke, else
        None. `armed_since` is when the current clench began — the clench
        must predate the stroke by arm_age_s or nothing fires."""
        self.active = False
        if now < self._refractory_until:
            self._cancel_pending()
            return None

        # --- landing phase: push detected, wait for the hand to settle ----
        if self._pending_t0 is not None:
            if palm is None or hw <= 1e-6:
                self._cancel_pending()      # fell out of frame: not a push
                return None
            if not armed or now - self._pending_t0 > self.settle_timeout_s:
                self._cancel_pending()      # opened the hand / kept moving
            else:
                self.active = True
                x, y = palm
                if self._hist:
                    tp, xp, yp = self._hist[-1][:3]
                    dt = max(1e-3, now - tp)
                    settle_speed = abs(y - yp) / max(1e-6, hw) / dt
                    if settle_speed <= self.settle_speed_hw_s:
                        self._cancel_pending()
                        self._refractory_until = now + self.refractory_s
                        return self.fires
                self._hist.append((now, x, y, hw, bool(armed)))
                return None

        # --- stroke phase ---------------------------------------------------
        if palm is None or hw <= 1e-6:
            if self._hist and now - self._hist[-1][0] > self.gap_tolerance_s:
                self._hist = []
            return None
        if self._hist and now - self._hist[-1][0] > self.gap_tolerance_s:
            self._hist = []
        x, y = palm
        self._hist.append((now, x, y, hw, bool(armed)))
        cutoff = now - self.window_s
        self._hist = [h for h in self._hist if h[0] >= cutoff]
        if len(self._hist) < 3:
            return None

        widths = sorted(h[3] for h in self._hist)
        hw_ref = widths[len(widths) // 2]

        best = {"travel": 0.0, "speed": 0.0, "armed": 0.0}
        for i in range(len(self._hist) - 2):
            t0, x0, y0 = self._hist[i][:3]
            span = now - t0
            if span <= 1e-3:
                continue
            dx = x - x0
            dy = (y - y0) * self.direction   # +ve = along OUR direction
            if dy <= 0 or dy <= abs(dx):     # not dominant our way
                continue
            sub = self._hist[i:]
            armed_share = sum(1 for h in sub if h[4]) / len(sub)
            travel_hw = dy / hw_ref
            speed_hw_s = travel_hw / span
            if travel_hw > best["travel"]:
                best = {"travel": travel_hw, "speed": speed_hw_s,
                        "armed": armed_share}
            if (travel_hw >= 0.4 * self.hand_widths
                    and speed_hw_s >= 0.6 * self.min_speed_hw_s
                    and armed_share >= 0.5 * self.armed_frac):
                self.active = True
            # The clench must predate this stroke's start: a hand that
            # armed mid-motion is a hand closing on its way somewhere,
            # not a fist being used.
            aged = (armed_since is not None
                    and t0 - armed_since >= self.arm_age_s)
            if (travel_hw >= self.hand_widths
                    and speed_hw_s >= self.min_speed_hw_s
                    and armed_share >= self.armed_frac
                    and self._hist[i][4] and aged):
                self.active = True
                if self.require_landing:
                    self._pending_t0 = now      # wait for the hand to settle
                    break
                # Fire the moment the stroke is unambiguous. Waiting for
                # the hand to decelerate and stop made this the hardest
                # gesture in the app to perform on purpose; clench-first
                # plus a committed stroke is already specific enough.
                self._hist = []
                self._refractory_until = now + self.refractory_s
                self.metrics = best
                return self.fires
        self.metrics = best
        return None


class GestureController:
    def __init__(self, mouse, sensitivity: float = 5.0,
                 edge_multiplier: float = 1.0, radius_scale: float = 2.2,
                 min_radius_px: float = 40.0,
                 engage_hold_s: float = 1.2, engage_spread_ratio: float = 1.7,
                 lose_grace_s: float = 0.25, dead_zone_px: float = 5.0,
                 filter_min_cutoff: float = 0.5, filter_beta: float = 0.007,
                 pinch_down_ratio: float = 0.28, pinch_up_ratio: float = 0.38,
                 pinch_debounce_frames: int = 2,
                 pinch_freeze_s: float = 0.15, release_freeze_s: float = 0.10,
                 right_down_ratio: float = 0.28, right_up_ratio: float = 0.38,
                 right_debounce_frames: int = 2,
                 volume_enabled: bool = True,
                 volume_down_ratio: float = 0.28,
                 volume_up_ratio: float = 0.38,
                 volume_debounce_frames: int = 2,
                 volume_steps_per_hs: float = 8.0,
                 fist_down_ratio: float = 1.05, fist_up_ratio: float = 1.50,
                 fist_debounce_frames: int = 2,
                 brake_enabled: bool = True, brake_onset: float = 0.08,
                 brake_full: float = 0.55, brake_min_scale: float = 0.15,
                 brake_smoothing: float = 0.35,
                 recenter_enabled: bool = True,
                 recenter_hold_s: float = 0.3,
                 recenter_refractory_s: float = 1.0,
                 scroll_natural: bool = False,
                 scroll_dead_zone_hs: float = 0.3,
                 scroll_gain_notches_s: float = 30.0,
                 scroll_max_notches_s: float = 120.0,
                 scroll_curve: float = 1.5,
                 scroll_enter_score: float = 0.10,
                 scroll_exit_score: float = 0.0,
                 scroll_enter_hold_s: float = 0.06,
                 scroll_exit_hold_s: float = 0.20,
                 scroll_restore_s: float = 0.5,
                 scroll_restore_dist_hs: float = 0.5,
                 scroll_recenter_speed_hs: float = 1.0,
                 scroll_recenter_hold_s: float = 0.1,
                 attention_drop_s: float = 2.0,
                 swipe_enabled: bool = True, swipe_hand_widths: float = 1.8,
                 swipe_window_s: float = 0.7, swipe_min_speed_hw_s: float = 5.0,
                 swipe_refractory_s: float = 0.8,
                 swipe_invert: bool = False, swipe_armed_frac: float = 0.6,
                 flick_down_enabled: bool = True,
                 flick_down_hand_widths: float = 0.9,
                 flick_down_window_s: float = 0.40,
                 flick_down_min_speed_hw_s: float = 4.5,
                 flick_down_refractory_s: float = 1.0,
                 flick_down_armed_frac: float = 0.6,
                 flick_down_require_landing: bool = False,
                 flick_down_settle_speed_hw_s: float = 1.5,
                 flick_down_settle_timeout_s: float = 0.4,
                 flick_down_arm_age_s: float = 0.15,
                 flick_up_enabled: bool = True):
        self.mouse = mouse
        self.sensitivity = sensitivity
        self.edge_multiplier = edge_multiplier
        self.radius_scale = radius_scale
        self.min_radius_px = min_radius_px
        self.engage_hold_s = engage_hold_s
        self.engage_spread_ratio = engage_spread_ratio
        self.lose_grace_s = lose_grace_s
        self.attention_drop_s = attention_drop_s
        self.dead_zone_px = dead_zone_px
        self.pinch_freeze_s = pinch_freeze_s
        self.release_freeze_s = release_freeze_s
        self.scroll_natural = scroll_natural
        self.scroll_dead_zone_hs = scroll_dead_zone_hs
        self.scroll_gain_notches_s = scroll_gain_notches_s
        self.scroll_max_notches_s = scroll_max_notches_s
        self.scroll_curve = scroll_curve
        self.scroll_enter_score = scroll_enter_score
        self.scroll_exit_score = scroll_exit_score
        self.scroll_enter_hold_s = scroll_enter_hold_s
        self.scroll_exit_hold_s = scroll_exit_hold_s
        self.scroll_restore_s = scroll_restore_s
        self.scroll_restore_dist_hs = scroll_restore_dist_hs
        self.scroll_recenter_speed_hs = scroll_recenter_speed_hs
        self.scroll_recenter_hold_s = scroll_recenter_hold_s
        self.swipe_enabled = swipe_enabled

        self.state = IDLE
        self.mode = POINT
        self.anchor = None          # (x, y) camera px, locked at engage
        self.palm = None            # latest palm center (One-Euro filtered)
        self.radius = 0.0           # current anchor-circle radius (px)
        self._hand_scale = None     # smoothed hand size driving the radius
        # filter_min_cutoff of None/0 disables smoothing (debug/testing)
        self._filter = (OneEuro2D(filter_min_cutoff, filter_beta)
                        if filter_min_cutoff else None)
        self._lpinch = PinchDetector(pinch_down_ratio, pinch_up_ratio,
                                     pinch_debounce_frames, INDEX_TIP)
        self._rpinch = PinchDetector(right_down_ratio, right_up_ratio,
                                     right_debounce_frames, MIDDLE_TIP)
        self._fist = FistDetector(fist_down_ratio, fist_up_ratio,
                                  fist_debounce_frames)
        self._brake = BrakeDetector(brake_enabled, brake_onset, brake_full,
                                    brake_min_scale, brake_smoothing)
        self._rocker = RockerDetector(recenter_enabled, recenter_hold_s,
                                      recenter_refractory_s)
        self.volume_enabled = volume_enabled
        self.volume_steps_per_hs = volume_steps_per_hs
        self._vpinch = PinchDetector(volume_down_ratio, volume_up_ratio,
                                     volume_debounce_frames, RING_TIP)
        self._vol_down = False        # thumb+ring currently held
        self._vol_ref = None          # palm y the current step count is from
        self._vol_emitted = 0         # steps already sent this hold
        self._swipe = SwipeDetector(swipe_hand_widths, swipe_window_s,
                                    swipe_min_speed_hw_s,
                                    swipe_refractory_s, swipe_invert,
                                    swipe_armed_frac)
        self._swipe_active = False
        self.last_swipe = None      # (direction, time) for the overlay
        self.flick_down_enabled = flick_down_enabled
        self.flick_up_enabled = flick_up_enabled
        self._flick = FlickDownDetector(
            flick_down_hand_widths, flick_down_window_s,
            flick_down_min_speed_hw_s, flick_down_refractory_s,
            flick_down_armed_frac,
            settle_speed_hw_s=flick_down_settle_speed_hw_s,
            settle_timeout_s=flick_down_settle_timeout_s,
            require_landing=flick_down_require_landing,
            arm_age_s=flick_down_arm_age_s)
        # The upward mirror shares every threshold with the push — the two
        # motions should demand the same effort — and restores the window
        # the push most recently minimised.
        self._flick_up = FlickDownDetector(
            flick_down_hand_widths, flick_down_window_s,
            flick_down_min_speed_hw_s, flick_down_refractory_s,
            flick_down_armed_frac,
            settle_speed_hw_s=flick_down_settle_speed_hw_s,
            settle_timeout_s=flick_down_settle_timeout_s,
            require_landing=False,
            arm_age_s=flick_down_arm_age_s,
            direction=-1, fires="restore")
        self._fist_since = None     # when the current clench began
        self._left_owner = None     # None | 'pinch' (left button holder)
        self._right_down = False
        self._freeze_until = 0.0    # cursor frozen while now < this
        self._prev_palm = None      # previous emitted-from position (None = no jump)
        self._prev_now = None
        self._engage_t0 = None      # when the engage pose was first seen
        self._lost_t0 = None        # when tracking conditions first failed
        self._attn_lost_t0 = None   # when the user first looked away
        self._attending = True      # gate: is the user looking at the screen?
        self._suspended = False     # a hand is up, but not the cursor hand
        self._residual = [0.0, 0.0]  # sub-pixel move remainder
        self._pose_hold_t0 = None   # when the pending scroll pose-change began
        self._scroll_origin = None  # joystick neutral point, set at scroll entry
        self._scroll_hs = None      # hand-size normaliser FROZEN at entry
        self._origin_stash = None   # (origin, hs, t) kept across a brief exit
        self._recenter_x_t0 = None  # reversal-hold timers, per axis
        self._recenter_y_t0 = None
        self._fist_lost_t0 = None   # when the cursor hand vanished (fist gap)
        self._wheel_acc = 0.0
        self._hwheel_acc = 0.0

    def apply_brake_params(self, params=None, **kw):
        """Retune the precision brake without a restart.

        The brake's thresholds only mean anything against a real hand, so
        tuning them has to be a live loop: squeeze, read the D readout,
        nudge the slider, squeeze again. This is fed straight from
        config.json every 30 frames, so it takes whatever is in there:
        anything that isn't a mapping is ignored, and unknown or None
        values are skipped rather than crashing the capture loop."""
        values = dict(params) if isinstance(params, dict) else {}
        values.update(kw)
        for name in ("enabled", "onset", "full", "min_scale", "smoothing",
                     "enter_frames", "stick_frames"):
            if values.get(name) is not None:
                setattr(self._brake, name, values[name])

    # -- gesture predicates ------------------------------------------------

    def _is_engage_pose(self, pts, frame_h):
        """ENGAGE: just raise an open hand.

        This used to demand a splayed "high five" — every finger extended AND
        a wide thumb-to-pinky span — held for over a second. Both were there
        to stop a hand wandering through frame taking the cursor, and both
        made the app feel like it was ignoring you. The open-fingers test
        alone already rules out the poses that mean something else (a fist
        arms navigation, a peace sign scrolls), so the span requirement was
        only ever costing users their first impression.

        `engage_spread_ratio` still applies if raised above 0 in config, for
        anyone who wants the stricter pose back.
        """
        ext = fingers_extended(pts)
        upright = pts[MIDDLE_MCP][1] < pts[WRIST][1] - 0.05 * frame_h
        return (upright and all(ext.values())
                and (self.engage_spread_ratio <= 0
                     or spread_ratio(pts) >= self.engage_spread_ratio))

    def _stay_ok(self, pts, frame_h):
        """Stay tracking while the hand is roughly upright. Fingers stay free
        for pinch/fist/scroll poses; dropping or lowering the hand (or losing
        it) is what disengages."""
        return pts[MIDDLE_MCP][1] < pts[WRIST][1] + 0.02 * frame_h

    # -- state machine -----------------------------------------------------

    def update(self, pts, frame_size, now, attending=True, suspend=False):
        """Advance one frame. pts = 21 (x,y) camera-pixel points for the cursor
        hand, or None. `attending` is the attention gate: when False the user
        isn't looking at the screen, so no cursor motion, clicks, navigation, or
        engaging happen. `suspend` says a hand IS in frame but it isn't the
        cursor hand (the launcher hand is up, or it hasn't been identified
        yet): hold everything exactly as it is rather than treating it as a
        lost hand. Returns an overlay info dict."""
        self._attending = attending
        self._suspended = suspend
        frame_h = frame_size[1]
        if pts:
            raw = palm_center(pts)
            self.palm = self._filter.filter(raw, now) if self._filter else raw
        elif self._filter:
            self.palm = None
            self._filter.reset()  # never smooth across a tracking gap
        else:
            self.palm = None

        # Fist = arming state for swipe navigation, tracked in every state.
        # The hysteresis (clench < down_ratio, reopen > up_ratio) keeps it
        # stable through the motion blur of a fast stroke. A brief tracking
        # dropout (common exactly when a fist self-occludes) keeps the armed
        # state; only a sustained loss disarms.
        # The precision brake shares its shape with a fist (both curl the
        # middle, ring and pinky), so it is measured FIRST and the fist can
        # only arm when the brake is not engaged. Entering the brake needs
        # the index clear of the curl, which a clench never satisfies — so a
        # fist still arms normally, while a pinch made from an already-held
        # brake reads as a click rather than as a fist.
        if pts:
            self._brake.update(pts, pose_ok=is_brake_pose(pts),
                               hold_ok=self._left_owner == "pinch")
            self._fist.update(pts, gate_on=not self._brake.engaged)
            self._fist_lost_t0 = None
        else:
            self._brake.update(None)
            if self._fist_lost_t0 is None:
                self._fist_lost_t0 = now
            elif now - self._fist_lost_t0 > 0.3:
                self._fist.reset()  # hand truly gone -> disarm cleanly
        # When did the current clench begin? The vertical flicks only fire
        # if the fist predates the stroke, which is what stops a lowered
        # arm (a hand that closes on its way down) from minimising things.
        if self._fist.is_down:
            if self._fist_since is None:
                self._fist_since = now
        else:
            self._fist_since = None

        # Fist-armed swipe navigation + down-flick minimize run on the cursor
        # hand in any state (no need to engage), but only while looking at
        # the screen.
        self._swipe_active = False
        self._flick.active = False
        self._flick_up.active = False
        if pts and attending:
            self._handle_swipe(pts, now)
            self._handle_flick(pts, now)

        if self.state == IDLE:
            self._handle_idle(pts, frame_h, now)
        elif self.state == ENGAGING:
            self._handle_engaging(pts, frame_h, now)
        elif self.state == TRACKING:
            self._handle_tracking(pts, frame_h, now)

        self._prev_now = now

        progress = 0.0
        if self.state == ENGAGING and self._engage_t0 is not None:
            progress = min(1.0, (now - self._engage_t0) / self.engage_hold_s)
        if self.state == TRACKING:
            if self.mode == SCROLL:
                display = SCROLL
            elif self._left_owner == "pinch":
                display = PINCHED
            elif self._vol_down:
                display = VOLUME
            elif self._right_down:
                display = RCLICK
            elif self._fist.is_down:
                display = ARMED
            elif self._brake.engaged:
                display = BRAKING
            else:
                display = TRACKING
        else:
            display = self.state
        return {"state": display, "anchor": self.anchor, "palm": self.palm,
                "engage_progress": progress, "dead_zone": self.dead_zone_px,
                "pinch_ratio": (self._lpinch.ratio
                                if pts and self.state == TRACKING
                                and self.mode == POINT else None),
                "pinch_down": self._left_owner is not None,
                "left_owner": self._left_owner,
                "right_down": self._right_down,
                "mode": self.mode if self.state == TRACKING else None,
                "radius": self.radius,
                "scroll_origin": (self._scroll_origin
                                  if self.state == TRACKING
                                  and self.mode == SCROLL else None),
                "scroll_dead_px": self.scroll_dead_zone_hs
                                  * (self._scroll_hs or self._hand_scale or 0.0),
                "volume_down": self._vol_down,
                "recenter_progress": self._rocker.progress,
                "brake": self._brake.amount,
                "brake_scale": self._brake.scale,
                "brake_m": self._brake.metrics,
                "fist_armed": self._fist.is_down,
                "fist_curl": self._fist.ratio,
                "fist_ext": self._fist.n_ext,
                "swipe_m": self._swipe.metrics,
                "flick_m": self._flick.metrics,
                "flick_landing": self._flick._pending_t0 is not None,
                "swipe_active": self._swipe_active,
                "swipe_progress": self._swipe.progress,
                "last_swipe": self.last_swipe,
                "attending": self._attending,
                "suspended": self.state == TRACKING
                             and (not self._attending or self._suspended)}

    def _handle_swipe(self, pts, now):
        """Fist-armed horizontal swipe = browser Back/Forward. Never fires
        while a button is held (that's a drag) or in scroll mode (the peace
        sign can sit inside the fist hysteresis band, so a scroll stroke
        must not read as a swipe)."""
        if (not self.swipe_enabled or self._left_owner is not None
                or self._right_down
                or (self.state == TRACKING and self.mode == SCROLL)):
            self._swipe.reset()
            return
        raw = palm_center(pts)
        hw = hand_width(pts)
        res = self._swipe.update(raw, hw, self._fist.is_down, now)
        self._swipe_active = self._swipe.active
        if self._swipe_active:
            # stroke building: freeze the cursor so the swipe doesn't drag it
            self._freeze_until = max(self._freeze_until, now + 0.08)
        if res == "forward":
            self.mouse.forward()
        elif res == "back":
            self.mouse.back()
        if res is not None:
            self.last_swipe = (res, now)
            self._freeze_until = max(self._freeze_until, now + 0.30)
            self._prev_palm = None
            # The natural exit from a swipe is dropping the arm with the
            # hand still loosely closed — a downward stroke the flick
            # detector would happily read as "minimise". Cross-refractory:
            # a fresh swipe buys the flick detector a second of silence.
            self._flick.hold_off(now + 1.0)
            self._flick_up.hold_off(now + 1.0)

    def _handle_flick(self, pts, now):
        """Fist-armed downward snap = minimize; upward tug = bring the
        last minimized window back. Never fires while a button is held or
        in scroll mode."""
        if (self._left_owner is not None or self._right_down
                or (self.state == TRACKING and self.mode == SCROLL)):
            self._flick.reset()
            self._flick_up.reset()
            return
        raw = palm_center(pts)
        hw = hand_width(pts)
        armed = self._fist.is_down
        since = self._fist_since
        res = down = up = None
        if self.flick_down_enabled:
            down = self._flick.update(raw, hw, armed, now, armed_since=since)
        if self.flick_up_enabled:
            up = self._flick_up.update(raw, hw, armed, now, armed_since=since)
        res = down or up
        if self._flick.active or self._flick_up.active:
            self._freeze_until = max(self._freeze_until, now + 0.08)
        if res == "minimize":
            self.mouse.minimize_window()
        elif res == "restore":
            self.mouse.restore_window()
        if res:
            self.last_swipe = (res, now)
            self._freeze_until = max(self._freeze_until, now + 0.30)
            self._prev_palm = None
            # A stroke's return motion is its own mirror image: the arm
            # comes back up after a push (and settles back down after a
            # tug), which would fire the opposite gesture immediately.
            other = self._flick_up if res == "minimize" else self._flick
            other.hold_off(now + self._flick.refractory_s)

    def _handle_idle(self, pts, frame_h, now):
        # Don't arm while the user is looking away — a raised palm off-screen
        # must not lock an anchor.
        if self._attending and pts and self._is_engage_pose(pts, frame_h):
            self.state = ENGAGING
            self._engage_t0 = now

    def _handle_engaging(self, pts, frame_h, now):
        # A suspended frame pauses the hold instead of cancelling it: the pose
        # is presumably still there, we just have no cursor hand this frame.
        # It can never COMPLETE while suspended — there'd be nothing to anchor
        # to — because this returns before the timer is checked.
        if self._suspended and self._attending:
            if self._prev_now is not None:
                self._engage_t0 += max(0.0, now - self._prev_now)
            return
        if not self._attending or not pts or not self._is_engage_pose(pts, frame_h):
            self._to_idle()
            return
        if now - self._engage_t0 >= self.engage_hold_s:
            # Lock the anchor at the current palm position — this is "the circle",
            # sized to the hand so control feels the same at any distance.
            self.state = TRACKING
            self.mode = POINT
            self.anchor = self.palm
            self._hand_scale = hand_size(pts)
            self.radius = max(self.min_radius_px, self.radius_scale * self._hand_scale)
            self._prev_palm = None   # first tracking frame emits no motion
            self._lost_t0 = None
            self._residual = [0.0, 0.0]
            self._lpinch.reset()     # must see an open hand before pinching
            self._rpinch.reset()
            self._freeze_until = 0.0
            self._pose_hold_t0 = None
            self._scroll_origin = None
            self._scroll_hs = None
            self._origin_stash = None
            self._wheel_acc = self._hwheel_acc = 0.0

    def _handle_tracking(self, pts, frame_h, now):
        dt = 1.0 / 30.0
        if self._prev_now is not None:
            dt = min(0.1, max(1e-3, now - self._prev_now))

        # Attention gate: looked away -> suspend. Freeze the cursor immediately
        # but keep the anchor AND any held button latched, so a quick glance
        # away mid-drag doesn't drop it and looking back resumes seamlessly.
        # Only a sustained look-away (past attention_drop_s) fully disengages.
        if not self._attending:
            if self._attn_lost_t0 is None:
                self._attn_lost_t0 = now
            self._prev_palm = None
            self._wheel_acc = self._hwheel_acc = 0.0
            if now - self._attn_lost_t0 >= self.attention_drop_s:
                self._to_idle()
            return
        self._attn_lost_t0 = None

        # The hand in frame simply isn't the cursor hand — the launcher hand
        # is up on its own, or the vote hasn't told them apart yet. Freeze the
        # pointer and keep the anchor and any held button latched: raising the
        # launcher hand must not cost a fresh engage. Unlike a real tracking
        # loss this never times out; dropping every hand is what disengages.
        if self._suspended:
            self._prev_palm = None
            self._lost_t0 = None
            self._wheel_acc = self._hwheel_acc = 0.0
            return

        valid = pts is not None and self._stay_ok(pts, frame_h)
        if not valid:
            # freeze cursor; drop to IDLE after grace period — never jump
            self._prev_palm = None
            self._wheel_acc = self._hwheel_acc = 0.0
            if self._lost_t0 is None:
                self._lost_t0 = now
            elif now - self._lost_t0 >= self.lose_grace_s:
                self._to_idle()
            return
        self._lost_t0 = None
        pos = self.palm

        # keep the circle sized to the hand (smoothed), so moving toward/away
        # from the camera rescales it and control speed stays consistent
        scale = hand_size(pts)
        self._hand_scale = (scale if self._hand_scale is None
                            else self._hand_scale + 0.3 * (scale - self._hand_scale))
        self.radius = max(self.min_radius_px, self.radius_scale * self._hand_scale)

        # Peace-sign pose toggles scroll mode. The pose is scored continuously
        # with a hysteresis band (enter needs a clear peace margin, exit needs
        # a clearly-not-peace margin), and the debounce is TIME-based and
        # asymmetric: entry is fast, exit takes scroll_exit_hold_s of
        # sustained not-peace — so a couple of noisy landmark frames
        # mid-scroll can never exit-and-re-enter and silently relocate the
        # origin ("restarting a new checkpoint" without asking).
        score = scroll_pose_score(pts)
        if self.mode == POINT:
            switching = score >= self.scroll_enter_score
            hold_s = self.scroll_enter_hold_s
        else:
            switching = score <= self.scroll_exit_score
            hold_s = self.scroll_exit_hold_s
        if switching:
            if self._pose_hold_t0 is None:
                self._pose_hold_t0 = now
            if self.mode == POINT:
                # forming the peace sign: hold the cursor still so the pose
                # change can't wiggle it
                self._freeze_until = max(self._freeze_until, now + 0.05)
            if now - self._pose_hold_t0 >= hold_s:
                self._pose_hold_t0 = None
                if self.mode == POINT:
                    self._enter_scroll(now)
                else:
                    self._exit_scroll(now)
        else:
            self._pose_hold_t0 = None

        if self.mode == SCROLL:
            self._handle_scroll(pos, dt, now)
            self._prev_palm = pos
            return

        # --- POINT mode: buttons -----------------------------------------
        # Pinch clicks. The thumb "belongs" to whichever fingertip it is
        # closest to (index = left, middle = right); ring must still be
        # extended so a forming/armed fist can't fake a pinch.
        # While the brake is held the ring finger is curled BY DEFINITION, so
        # the "ring must be extended" guard would block the very clicks the
        # brake exists to make easier. Braking replaces that guard: the fist
        # can't arm while braking, so there is no forming fist to fake a
        # pinch. Right-click and volume stay blocked either way — both need a
        # finger the squeeze has already folded away, so reading them here
        # would be guesswork.
        lp = pinch_ratio(pts, INDEX_TIP)
        rp = pinch_ratio(pts, MIDDLE_TIP)
        ext = fingers_extended(pts)
        braking = self._brake.engaged
        l_gate = (self._left_owner is None and not self._fist.is_down
                  and (ext["ring"] or braking) and lp <= rp)
        r_gate = (not self._right_down and not self._fist.is_down
                  and ext["ring"] and not braking and rp < lp)
        left_edge = self._lpinch.update(pts, gate_on=l_gate)
        right_edge = self._rpinch.update(pts, gate_on=r_gate)

        # Thumb + ring = volume, the third rung of the pinch ladder. The
        # thumb belongs to whichever fingertip it is nearest, so index and
        # middle pinches can't cross-fire; index must stay extended so a
        # forming fist can't fake it.
        vp = pinch_ratio(pts, RING_TIP)
        v_gate = (self.volume_enabled and not self._vol_down
                  and not self._fist.is_down and self._left_owner is None
                  and not self._right_down and not braking
                  and vp < lp and vp < rp and ext["index"])
        vol_edge = self._vpinch.update(pts, gate_on=v_gate)
        if vol_edge == "down" and not self._vol_down:
            self._vol_down = True
            self._vol_ref = pos[1]
            self._vol_emitted = 0
            self._freeze_until = now + self.pinch_freeze_s
        elif vol_edge == "up" and self._vol_down:
            self._vol_down = False
            self._freeze_until = now + self.release_freeze_s

        if self._vol_down:
            # 1:1 position mapping: how far the hand has moved from where the
            # pinch started IS the volume change, so moving back down undoes
            # it exactly. That also makes a reversal anchor unnecessary —
            # unlike rate-based scrolling there is no dead travel to cross.
            hs = self._hand_scale or hand_size(pts)
            up_hs = (self._vol_ref - pos[1]) / max(1e-6, hs)
            target = int(up_hs * self.volume_steps_per_hs)
            if target != self._vol_emitted:
                self.mouse.volume(target - self._vol_emitted)
                self._vol_emitted = target
            self._prev_palm = pos      # cursor stays put while setting volume
            return

        if left_edge == "down" and self._left_owner is None:
            self._left_owner = "pinch"
            self.mouse.left_down()
            self._freeze_until = now + self.pinch_freeze_s
        elif left_edge == "up" and self._left_owner == "pinch":
            self._left_owner = None
            self.mouse.left_up()
            self._freeze_until = now + self.release_freeze_s
        if right_edge == "down" and not self._right_down:
            self._right_down = True
            self.mouse.right_down()
            self._freeze_until = now + self.pinch_freeze_s
        elif right_edge == "up" and self._right_down:
            self._right_down = False
            self.mouse.right_up()
            self._freeze_until = now + self.release_freeze_s

        # Rock-and-roll horns held briefly = teleport the cursor home to the
        # screen centre. A relative mouse has no home position, so a long
        # session can drift the comfortable arm position somewhere awkward;
        # this is the reset. Gated off any held button so a drag can never
        # be yanked across the screen mid-hold.
        if self._rocker.update(pts, now,
                               gate_on=(self._left_owner is None
                                        and not self._right_down
                                        and not self._vol_down)):
            self.mouse.center()
            self._freeze_until = max(self._freeze_until, now + 0.25)
            self._prev_palm = pos

        # Fist armed: the hand is a swipe trigger now, not a pointer — freeze
        # the cursor so the coming stroke can't smear it across the screen.
        if self._fist.is_down:
            self._prev_palm = pos
            return

        if now < self._freeze_until:
            self._prev_palm = pos   # frozen: swallow motion, no catch-up jump
            return

        offset = (pos[0] - self.anchor[0], pos[1] - self.anchor[1])
        dist = math.hypot(offset[0], offset[1])

        # Dead zone: a hand resting at the anchor must not drift the cursor
        if dist < self.dead_zone_px:
            self._prev_palm = pos   # stay synced so leaving the zone can't jump
            return

        # Edge-gradient sensitivity: precise near the center, up to
        # edge_multiplier x faster at the rim of the self-scaling circle.
        edge_frac = min(1.0, dist / self.radius) if self.radius > 0 else 0.0
        eff = self.sensitivity * (1.0 + (self.edge_multiplier - 1.0) * edge_frac)
        # Squeezing the last three fingers scales this down — proportionally,
        # so it trims as much as you ask for. It multiplies the motion rather
        # than offsetting it, so a still hand still emits exactly nothing.
        eff *= self._brake.scale

        dx = dy = 0.0
        if self._prev_palm is not None:
            dx = (pos[0] - self._prev_palm[0]) * eff
            dy = (pos[1] - self._prev_palm[1]) * eff
        if dx or dy:
            self._emit(dx, dy)
        self._prev_palm = pos

        # Trailing re-anchor: once the hand reaches the rim, the circle follows
        # it (keeping the hand on the edge) so you can cross the whole screen.
        if dist > self.radius:
            self.anchor = (pos[0] - offset[0] / dist * self.radius,
                           pos[1] - offset[1] / dist * self.radius)

    def _recenter_axis(self, axis, cur, prev, dt, hs, now, t0):
        """Reversal watcher for one scroll axis. Deliberate, sustained motion
        back TOWARD the origin means "reverse", not "slow down": after
        scroll_recenter_hold_s of it, the origin's coordinate jumps to the
        hand, so the very next bit of travel scrolls the other way. A still
        hand never recenters (holds its speed forever), and jitter/slow drift
        stay below the speed floor. Returns the updated hold-start time."""
        off = cur - self._scroll_origin[axis]
        vel = (cur - prev) / dt
        opposing = (abs(off) > self.scroll_dead_zone_hs * hs
                    and off * vel < 0
                    and abs(vel) >= self.scroll_recenter_speed_hs * hs)
        if not opposing:
            return None
        if t0 is None:
            return now
        if now - t0 >= self.scroll_recenter_hold_s:
            origin = list(self._scroll_origin)
            origin[axis] = cur
            self._scroll_origin = tuple(origin)
            return None
        return t0

    def _handle_scroll(self, pos, dt, now):
        """Joystick scroll with recenter-on-reversal: the palm position where
        the peace sign formed is the zero point. Hold the hand off it and the
        page scrolls at a constant speed set by the offset — farther = faster,
        still hand = same speed forever. Moving decisively back toward the
        zero RECENTERS it to the hand (per axis), so "scroll down, stop, lift
        a little" scrolls up immediately instead of demanding the hand travel
        all the way back across the old zero.

        Offsets are normalised by the hand size FROZEN at scroll entry, so
        live hand-scale wobble can't change the speed of a physically still
        hand. A dead zone around the zero means a resting hand scrolls
        nothing."""
        if self._scroll_origin is None:
            self._scroll_origin = pos
            self._scroll_hs = self._hand_scale
            return
        hs = self._scroll_hs if self._scroll_hs else (self._hand_scale or 1.0)

        if self._prev_palm is not None and dt > 0:
            self._recenter_x_t0 = self._recenter_axis(
                0, pos[0], self._prev_palm[0], dt, hs, now, self._recenter_x_t0)
            self._recenter_y_t0 = self._recenter_axis(
                1, pos[1], self._prev_palm[1], dt, hs, now, self._recenter_y_t0)

        off_x = (pos[0] - self._scroll_origin[0]) / hs
        off_y = (pos[1] - self._scroll_origin[1]) / hs

        def notches_s(off):
            excess = abs(off) - self.scroll_dead_zone_hs
            if excess <= 0.0:
                return 0.0
            speed = self.scroll_gain_notches_s * (excess ** self.scroll_curve)
            return math.copysign(min(speed, self.scroll_max_notches_s), off)

        sign = 1.0 if self.scroll_natural else -1.0
        # 120 wheel units = one notch
        self._wheel_acc += sign * notches_s(off_y) * 120.0 * dt
        self._hwheel_acc += notches_s(off_x) * 120.0 * dt

        w = int(self._wheel_acc)
        if w:
            self._wheel_acc -= w
            self.mouse.wheel(w)
        hw = int(self._hwheel_acc)
        if hw:
            self._hwheel_acc -= hw
            self.mouse.hwheel(hw)

    def _enter_scroll(self, now):
        self.mode = SCROLL
        self._release_all_buttons()
        self._release_volume()
        self._lpinch.cancel_pending()
        self._rpinch.cancel_pending()
        self._fist.cancel_pending()
        # If scroll dropped a moment ago and the hand is still near the old
        # zero, RESTORE it — the pose classifier flickering must not hand the
        # user a relocated zero. A fresh deliberate peace sign (later, or
        # somewhere else) locks fresh.
        stash = self._origin_stash
        if (stash is not None and now - stash[2] <= self.scroll_restore_s
                and self.palm is not None and stash[1]
                and _dist(self.palm, stash[0])
                <= self.scroll_restore_dist_hs * stash[1]):
            self._scroll_origin, self._scroll_hs = stash[0], stash[1]
        else:
            self._scroll_origin = self.palm  # joystick zero = where it started
            # freeze the normaliser for the whole session: live hand-scale
            # drift would change speed under a physically still hand
            self._scroll_hs = self._hand_scale
        self._origin_stash = None
        self._recenter_x_t0 = self._recenter_y_t0 = None
        self._wheel_acc = self._hwheel_acc = 0.0

    def _exit_scroll(self, now):
        self.mode = POINT
        if self._scroll_origin is not None and self._scroll_hs:
            self._origin_stash = (self._scroll_origin, self._scroll_hs, now)
        self._scroll_origin = None
        self._scroll_hs = None
        self._recenter_x_t0 = self._recenter_y_t0 = None
        self._wheel_acc = self._hwheel_acc = 0.0
        self._lpinch.cancel_pending()
        self._rpinch.cancel_pending()
        self._fist.cancel_pending()
        # leaving scroll: brief freeze so the pose change can't lurch the
        # pointer while the fingers re-form
        self._freeze_until = max(self._freeze_until, now + 0.15)
        self._prev_palm = None

    def _release_volume(self):
        self._vol_down = False
        self._vol_ref = None
        self._vol_emitted = 0
        self._vpinch.reset()

    def _release_all_buttons(self):
        if self._left_owner is not None:
            self._left_owner = None
            self.mouse.left_up()    # never leave a button stuck down
        if self._right_down:
            self._right_down = False
            self.mouse.right_up()

    def _emit(self, dx: float, dy: float):
        """Send relative motion, carrying sub-pixel remainders."""
        self._residual[0] += dx
        self._residual[1] += dy
        ix, iy = int(self._residual[0]), int(self._residual[1])
        if ix or iy:
            self._residual[0] -= ix
            self._residual[1] -= iy
            self.mouse.move(ix, iy)

    def _to_idle(self):
        # NOTE: the fist detector is deliberately NOT reset here. Clenching
        # from a raised palm drops us out of ENGAGING into IDLE, and resetting
        # would disarm the fist at the exact moment the user made it — the
        # arming state is global (navigation works in any state) and only a
        # vanished hand disarms it, which update() handles.
        self._release_all_buttons()
        self._lpinch.reset()
        self._rpinch.reset()
        self._swipe.reset()
        self._flick.reset()
        self._flick_up.reset()
        self._swipe_active = False
        self.state = IDLE
        self.mode = POINT
        self.anchor = None
        self.radius = 0.0
        self._hand_scale = None
        self._prev_palm = None
        self._engage_t0 = None
        self._lost_t0 = None
        self._attn_lost_t0 = None
        self._pose_hold_t0 = None
        self._scroll_origin = None
        self._scroll_hs = None
        self._origin_stash = None
        self._recenter_x_t0 = self._recenter_y_t0 = None
        self._wheel_acc = self._hwheel_acc = 0.0

"""Fist-armed swipe navigation (hand-width invariant), hand-role assignment
(right = cursor, left = launcher), and the finger-count launcher."""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from gestures import GestureController, SwipeDetector, FlickDownDetector, \
    IDLE, ENGAGING, TRACKING, ARMED
from hand_roles import RoleAssigner
from launcher import FingerLauncher
from mouse_input import NullMouse
from test_gestures import synthetic_hand, hand, FRAME, DT


def fist(px, py):
    """Clenched fist at (px, py) — the arming pose for navigation."""
    return synthetic_hand(px, py, open_palm=False)

failures = []
W = FRAME[0]


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def count_hand(px, py, up=("index",)):
    """Left-hand launcher pose: the named fingers up, the rest curled."""
    pts = list(synthetic_hand(px, py))
    bases = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for name, b in bases.items():
        if name in up:
            continue
        x = pts[b][0]
        pts[b + 1] = (x, py - 70)     # pip stays high
        pts[b + 2] = (x, py - 40)
        pts[b + 3] = (x, py + 10)     # tip curled to the wrist
    return pts


# =============================== SwipeDetector ===============================
HW = 100.0

det = SwipeDetector(hand_widths=2.5, window_s=0.7, min_speed_hw_s=4.0)
t, res = 0.0, None
for i in range(8):                       # right -> left, 45px/frame = fast
    t += DT
    r = det.update((600 - 45 * i, 240), HW, True, t)
    res = r or res
check("fast R->L swipe fires forward", res == "forward")

det2 = SwipeDetector()
t, res = 0.0, None
for i in range(8):                       # left -> right (backhand)
    t += DT
    r = det2.update((100 + 45 * i, 240), HW, True, t)
    res = r or res
check("L->R backhand fires back", res == "back")

# refractory: an immediate second stroke is swallowed
res2 = None
for i in range(8):
    t += DT
    r = det2.update((460 - 45 * i, 240), HW, True, t)
    res2 = r or res2
check("refractory blocks immediate re-fire", res2 is None)

# slow horizontal travel never fires
det3 = SwipeDetector()
t, res = 0.0, None
for i in range(30):                      # 8px/frame = 240px/s = 2.4 hw/s
    t += DT
    r = det3.update((600 - 8 * i, 240), HW, True, t)
    res = r or res
check("slow drag does not fire", res is None)

# fast vertical motion never fires
det4 = SwipeDetector()
t, res = 0.0, None
for i in range(8):
    t += DT
    r = det4.update((320, 40 + 45 * i), HW, True, t)
    res = r or res
check("vertical motion does not fire", res is None)

# armed-flag flicker mid-stroke is tolerated (motion blur breaks the fist
# classification for a frame here and there)
det5 = SwipeDetector()
t, res = 0.0, None
for i in range(8):
    t += DT
    r = det5.update((600 - 45 * i, 240), HW, i % 3 != 1, t)   # armed ~2/3 frames
    res = r or res
check("armed-flag flicker tolerated", res == "forward")

# never fires when the hand was never armed (no fist at all)
det5b = SwipeDetector()
t, res = 0.0, None
for i in range(8):
    t += DT
    r = det5b.update((600 - 45 * i, 240), HW, False, t)
    res = r or res
check("unarmed stroke fires nothing", res is None)

# a 1-frame tracking dropout mid-stroke is tolerated
det6 = SwipeDetector()
t, res = 0.0, None
for i in range(9):
    t += DT
    if i == 4:
        r = det6.update(None, 0.0, False, t)   # dropout frame
    else:
        r = det6.update((600 - 45 * i, 240), HW, True, t)
    res = r or res
check("tracking dropout tolerated", res == "forward")

# distance invariance: same physical swipe, half-size hand (farther away)
det7 = SwipeDetector()
t, res = 0.0, None
for i in range(8):                       # 22px/frame with a 50px-wide hand
    t += DT
    r = det7.update((300 - 22 * i, 240), 50.0, True, t)
    res = r or res
check("far-away hand: same swipe in hand-widths fires", res == "forward")

# ================= controller-level FIST-ARMED navigation ====================
# The fist must arm first; the detector needs the hysteresis switch to see an
# open hand once (arming) before a clench registers, exactly like real use.
def arm_then(ctrl, mouse, frames_xy, pose=fist, t0=0.0, attending=True,
             clench_frames=8):
    """Show an open hand (arms the switch), clench AND HOLD it a beat,
    then run the stroke. The hold matters: the vertical flicks demand the
    clench predate the stroke (arm_age_s), which is what separates a
    deliberate grab-and-pull from a hand closing on its way down."""
    t = t0
    for _ in range(3):                   # open hand: arms the hysteresis
        t += DT
        ctrl.update(synthetic_hand(frames_xy[0][0], frames_xy[0][1]),
                    FRAME, t, attending=attending)
    for _ in range(clench_frames):       # clench in place: fist commits
        t += DT
        ctrl.update(pose(frames_xy[0][0], frames_xy[0][1]), FRAME, t,
                    attending=attending)
    for (x, y) in frames_xy[1:]:
        t += DT
        ctrl.update(pose(x, y), FRAME, t, attending=attending)
    return t


m = NullMouse()
c = GestureController(m)                 # default 1.2s engage: stays IDLE
t = arm_then(c, m, [(560 - 45 * i, 240) for i in range(7)])
check("fist + R->L swipe -> mouse.forward()", ("forward",) in m.events,
      f"events={[e for e in m.events if e[0] != 'move']}")
check("fist swipe fires exactly one nav event",
      len([e for e in m.events if e[0] in ("forward", "back")]) == 1,
      f"events={[e for e in m.events if e[0] != 'move']}")
check("swipe never locks tracking mid-stroke", c.state != TRACKING)

# backhand: fist + left -> right = Back
m_b = NullMouse()
c_b = GestureController(m_b)
arm_then(c_b, m_b, [(100 + 45 * i, 240) for i in range(7)])
check("fist + L->R swipe -> mouse.back()", ("back",) in m_b.events,
      f"events={[e for e in m_b.events if e[0] != 'move']}")

# fist + push down + land = minimize (the landing check means the hand must
# settle in frame, fist still held, before it fires)
m_d = NullMouse()
c_d = GestureController(m_d)
t = arm_then(c_d, m_d, [(320, 100 + 45 * i) for i in range(6)])
for _ in range(4):                       # the push lands: hand stops, fist held
    t += DT
    c_d.update(fist(320, 325), FRAME, t)
check("fist + push + land -> minimize", ("minimize",) in m_d.events,
      f"events={[e for e in m_d.events if e[0] != 'move']}")

# a 40-degree diagonal push is claimed by exactly ONE gesture (the old
# vert/horiz ratio gates left a 34-56 degree cone where nothing fired)
import math as _math

m_dg = NullMouse()
c_dg = GestureController(m_dg)
_diag = [(320 + int(45 * _math.sin(_math.radians(40)) * i),
          100 + int(45 * _math.cos(_math.radians(40)) * i)) for i in range(6)]
t = arm_then(c_dg, m_dg, _diag)
for _ in range(4):
    t += DT
    c_dg.update(fist(*_diag[-1]), FRAME, t)
_nav = [e for e in m_dg.events if e[0] in ("forward", "back", "minimize")]
check("40-degree diagonal push fires exactly one gesture",
      _nav == [("minimize",)], f"nav={_nav}")

# an already-clenched hand entering frame arms with NO open hand first
m_ac = NullMouse()
c_ac = GestureController(m_ac)
t = 0.0
for _ in range(3):
    t += DT
    info = c_ac.update(fist(560, 240), FRAME, t)
check("already-clenched entry arms immediately", info["fist_armed"],
      f"armed={info['fist_armed']}")
for i in range(1, 8):
    t += DT
    c_ac.update(fist(560 - 45 * i, 240), FRAME, t)
check("swipe fires without the hand ever opening",
      ("forward",) in m_ac.events,
      f"events={[e for e in m_ac.events if e[0] != 'move']}")

# a brief tracking dropout (fists self-occlude) keeps the armed state
m_gap = NullMouse()
c_gap = GestureController(m_gap)
t = 0.0
for _ in range(3):
    t += DT
    c_gap.update(fist(320, 240), FRAME, t)
for _ in range(3):                       # 0.1 s dropout, under the 0.3 s limit
    t += DT
    c_gap.update(None, FRAME, t)
t += DT
info = c_gap.update(fist(320, 240), FRAME, t)
check("brief dropout keeps the fist armed", info["fist_armed"])

# Arming needs a REAL fist now. The 1.30 band deliberately let a relaxed
# ~1.1 "doorknob" grab arm, and in live use that read as the app firing on
# its own — a half-curled resting hand kept arming navigation. A deliberate
# clench measures well under 1.10; a loose grab does not.
def loose_grab(px, py):
    pts = list(synthetic_hand(px, py))
    for b in (5, 9, 13, 17):
        x = pts[b][0]
        dx = x - px
        dy = _math.sqrt(112.0 ** 2 - dx * dx)
        pts[b + 3] = (x, py + 50 - dy)   # tip 112px from the wrist
    return pts


from gestures import curl_ratio as _curl
_lg = loose_grab(320, 240)
assert 1.05 < _curl(_lg) < 1.15, f"helper curl={_curl(_lg):.3f}"
m_lg = NullMouse()
c_lg = GestureController(m_lg)
t = 0.0
for _ in range(3):
    t += DT
    info = c_lg.update(loose_grab(320, 240), FRAME, t)
check("a loose half-grab no longer arms navigation", not info["fist_armed"],
      f"curl={_curl(_lg):.2f} armed={info['fist_armed']}")
m_fg = NullMouse()
c_fg = GestureController(m_fg)
t = 0.0
for _ in range(3):
    t += DT
    info = c_fg.update(fist(320, 240), FRAME, t)
check("a genuine fist still arms", info["fist_armed"],
      f"curl={_curl(fist(320, 240)):.2f}")

# The arming gate is finger COUNT, not curl alone: that is what lets the curl
# band be loose enough for a relaxed grab without the peace sign (whose curl
# sits right next to a loose grab's) ever arming navigation.
from gestures import FistDetector as _FD
from test_gestures import peace_hand as _peace   # never import from
# test_scroll_and_rightclick: it runs its checks + sys.exit at module level
# and would kill this file mid-run with a false "ALL PASS".

_ph = _peace(320, 240)
_fd_peace = _FD()
for _ in range(6):
    _fd_peace.update(_ph)
check("peace sign never arms the grab (2 fingers up)",
      not _fd_peace.is_down and _fd_peace.n_ext == 2,
      f"curl={_curl(_ph):.2f} n_ext={_fd_peace.n_ext}")

# a pointing hand (1 finger up) must not arm either — that pose is reserved
_point = count_hand(320, 240, ("index",))
_fd_point = _FD()
for _ in range(6):
    _fd_point.update(_point)
check("pointing hand never arms the grab", not _fd_point.is_down)

# ...but releasing is NOT gated on finger count, so a motion-blurred frame
# that misreads a finger as extended cannot disarm a grab mid-swipe
_fd_blur = _FD()
for _ in range(4):
    _fd_blur.update(fist(320, 240))
assert _fd_blur.is_down
_blurred = list(fist(320, 240))
_blurred[8] = (_blurred[8][0], 240 - 160)     # index misread as extended
for _ in range(4):
    _fd_blur.update(_blurred)
check("a misread finger cannot disarm a held grab", _fd_blur.is_down,
      f"n_ext={_fd_blur.n_ext} armed={_fd_blur.is_down}")

# opening the hand still releases it (curl crosses the up threshold)
for _ in range(4):
    _fd_blur.update(synthetic_hand(320, 240))
check("opening the hand releases the grab", not _fd_blur.is_down)

# THE POINT OF v3.2: an open flat hand swiping fast fires NOTHING
m_o = NullMouse()
c_o = GestureController(m_o)
t = 0.0
for i in range(8):
    t += DT
    c_o.update(synthetic_hand(560 - 45 * i, 240), FRAME, t)
check("open-hand swipe fires no navigation",
      not [e for e in m_o.events if e[0] in ("forward", "back", "minimize")],
      f"events={[e for e in m_o.events if e[0] != 'move']}")

# open-hand fast downward motion also fires nothing
m_od = NullMouse()
c_od = GestureController(m_od)
t = 0.0
for i in range(7):
    t += DT
    c_od.update(synthetic_hand(320, 100 + 45 * i), FRAME, t)
check("open-hand down-swipe does not minimize",
      ("minimize",) not in m_od.events,
      f"events={[e for e in m_od.events if e[0] != 'move']}")

# slow fist movement = not a swipe (that's just a fist being carried around)
m_s = NullMouse()
c_s = GestureController(m_s)
arm_then(c_s, m_s, [(560 - 8 * i, 240) for i in range(25)])
check("slow fist movement fires nothing",
      not [e for e in m_s.events if e[0] in ("forward", "back", "minimize")],
      f"events={[e for e in m_s.events if e[0] != 'move']}")

# a fist NEVER holds the left mouse button anymore (fist-drag is gone)
m_nd = NullMouse()
c_nd = GestureController(m_nd, engage_hold_s=0.25, dead_zone_px=0.0,
                        filter_min_cutoff=None)
t = 0.0
for _ in range(12):                      # engage with an open palm
    t += DT
    info = c_nd.update(synthetic_hand(320, 240), FRAME, t)
assert c_nd.state == TRACKING, c_nd.state
for _ in range(5):                       # clench and hold
    t += DT
    info = c_nd.update(fist(320, 240), FRAME, t)
check("fist no longer presses the left button",
      not [e for e in m_nd.events if e[0] in ("down", "up")],
      f"events={m_nd.events[:4]}")
check("fist shows ARMED state", info["state"] == ARMED
      and info["fist_armed"], f"state={info['state']}")

# ...and the armed fist freezes the cursor instead of dragging it
m_nd.events.clear()
for i in range(1, 8):
    t += DT
    c_nd.update(fist(320 + 6 * i, 240), FRAME, t)
check("armed fist does not move the cursor",
      not [e for e in m_nd.events if e[0] == "move"],
      f"moves={[e for e in m_nd.events if e[0] == 'move'][:3]}")

# Rapid re-fire: fist stays clenched across strokes — flick, snap the hand
# back (the return lands inside the refractory, so it's swallowed rather than
# counter-firing), flick again. No re-clenching between strokes.
m_r = NullMouse()
c_r = GestureController(m_r, swipe_refractory_s=0.3)
t = arm_then(c_r, m_r, [(560 - 45 * i, 240) for i in range(7)])
first_n = len([e for e in m_r.events if e[0] == "forward"])
check("first fist swipe fires once", first_n == 1, f"n={first_n}")

for i in range(1, 11):                   # snap back right, inside refractory
    t += DT
    c_r.update(fist(290 + 27 * i, 240), FRAME, t)
check("return stroke does not counter-fire Back",
      not [e for e in m_r.events if e[0] == "back"],
      f"events={[e for e in m_r.events if e[0] != 'move']}")

for i in range(7):                       # second stroke, fist never reopened
    t += DT
    c_r.update(fist(560 - 45 * i, 240), FRAME, t)
second_n = len([e for e in m_r.events if e[0] == "forward"])
check("held fist swipes again without re-clenching (back-back-back)",
      second_n == 2, f"n={second_n}")
check("fist still armed after two strokes", c_r._fist.is_down)

# A swipe's follow-through must not minimise. The fist stays armed while
# held (by design, for back-back-back), and the natural exit after a swipe
# is dropping the arm with the hand still loosely closed — a downward-
# dominant stroke fast and long enough to satisfy the flick thresholds. A
# fresh swipe therefore buys the flick detector a moment of silence.
m_x = NullMouse()
c_x = GestureController(m_x)
t = arm_then(c_x, m_x, [(560 - 45 * i, 240) for i in range(7)])
assert [e for e in m_x.events if e[0] == "forward"], m_x.events
for i in range(1, 8):          # arm drop: fist still closed, fast, downward
    t += DT
    c_x.update(fist(290, 240 + 45 * i), FRAME, t)
check("post-swipe arm drop with a closed hand does not minimize",
      ("minimize",) not in m_x.events,
      f"events={[e for e in m_x.events if e[0] != 'move']}")

# navigation is gated behind attention
m2 = NullMouse()
c2 = GestureController(m2)
arm_then(c2, m2, [(560 - 45 * i, 240) for i in range(7)], attending=False)
check("no fist swipe while looking away",
      not [e for e in m2.events if e[0] in ("forward", "back", "minimize")],
      f"events={m2.events}")

# ================================ SideVote ===================================
# MediaPipe's handedness label is INVERTED here (we flip the frame before
# inference), so the model's "Left" is the user's RIGHT hand. user_side() is
# the one place that knows this; every test below goes through it.
from hand_roles import SideVote, user_side

check("label inversion: model 'Left' = the user's right hand",
      user_side("Left") == "right" and user_side("Right") == "left")
check("a missing label picks no side", user_side("") is None)


def vote_frames(sv, label, score, n, t0=0.0, dt=DT):
    """Feed n frames of one label; return (verdict, time)."""
    t, side = t0, sv.side()
    for _ in range(n):
        t += dt
        side = sv.update(label, score, t)
    return side, t


sv = SideVote()
check("one confident frame is not enough to decide",
      vote_frames(sv, "Left", 0.95, 1)[0] is None)
check("a sustained majority decides", vote_frames(sv, "Left", 0.95, 6)[0] == "right")

# a label that keeps flipping is exactly the case that must NOT decide
sv_flip = SideVote()
t, side = 0.0, None
for i in range(30):
    t += DT
    side = sv_flip.update("Left" if i % 2 else "Right", 0.95, t)
check("a flickering label never decides", side is None, f"side={side}")

# low-confidence frames abstain rather than voting badly
sv_low = SideVote()
check("low-confidence frames never decide",
      vote_frames(sv_low, "Left", 0.3, 20)[0] is None)

# one bad frame in an otherwise clean stream is outvoted, not obeyed
sv_blur = SideVote()
sv_blur.update("Right", 0.95, DT)                 # a single misread frame
check("one misread frame cannot decide alone", sv_blur.side() is None)
check("the majority still wins after a misread frame",
      vote_frames(sv_blur, "Left", 0.95, 6, t0=DT)[0] == "right")

# ...and the verdict is never latched: sustained disagreement flips it back
side, t = vote_frames(SideVote(), "Left", 0.95, 8)
sv_flipback = SideVote()
vote_frames(sv_flipback, "Left", 0.95, 8)
check("a wrong verdict is corrected by sustained disagreement",
      vote_frames(sv_flipback, "Right", 0.95, 20, t0=1.0, dt=DT)[0] == "left")

# leaving the frame forgets everything — no verdict survives an absence
sv_gap = SideVote()
vote_frames(sv_gap, "Left", 0.95, 8)
sv_gap.gap(5.0)                                   # hand gone for seconds
check("a real absence clears the verdict", sv_gap.side() is None)

# ...but a blink does not (detection drops frames constantly)
sv_blink = SideVote()
_, t = vote_frames(sv_blink, "Left", 0.95, 8)
sv_blink.gap(t + 0.1)
check("a brief dropout keeps the verdict", sv_blink.side() == "right")

# escape hatch: if handedness data never arrives at all, nothing could tell
# the hands apart, so a lone hand falls back to being the dominant one
sv_blind = SideVote(blind_frames=24)
check("no handedness data at all falls back to the dominant hand",
      vote_frames(sv_blind, "", 0.0, 24)[0] == "right")

# ============================= RoleAssigner ==================================
ra = RoleAssigner(dominant="right")
right_h = {"pts": synthetic_hand(500, 240), "label": "Right", "score": 0.95}
left_h = {"pts": synthetic_hand(140, 240), "label": "Left", "score": 0.95}
roles = ra.assign([left_h, right_h], W)
check("two hands: right side = cursor", roles["cursor"] is right_h["pts"]
      and roles["off"] is left_h["pts"])


def lone_frames(ra, label, score, n, px=320, t0=0.0, dt=DT):
    """Feed n frames of one lone hand; return (every role dict, end time)."""
    t, out = t0, []
    for _ in range(n):
        t += dt
        out.append(ra.assign([{"pts": synthetic_hand(px, 240), "label": label,
                               "score": score}], W, t))
    return out, t


# v3.5 policy: the off hand NEVER drives the cursor, so it works on its own.
# A lone LEFT hand (the model calls it "Right") is the launcher hand and is
# not the cursor on any frame — not even the first one, before the vote has
# settled, because an unidentified hand holds no role at all.
ra_l = RoleAssigner(dominant="right")
seq, _ = lone_frames(ra_l, "Right", 0.95, 30)
check("a lone left hand never drives the cursor, on any frame",
      all(r["cursor"] is None for r in seq))
check("a lone left hand does become the launcher hand",
      seq[-1]["off"] is not None)
check("...and holds no role at all until the vote settles",
      seq[0]["off"] is None and seq[0]["cursor"] is None)

# The dominant hand alone still gets the cursor, and quickly (~0.2 s) — the
# engage hold is 1.2 s, so the wait is invisible in real use.
ra_r = RoleAssigner(dominant="right")
seq, _ = lone_frames(ra_r, "Left", 0.95, 12)
settled = next((i for i, r in enumerate(seq) if r["cursor"] is not None), None)
check("a lone right hand becomes the cursor", seq[-1]["cursor"] is not None
      and seq[-1]["off"] is None)
check("...within about a fifth of a second",
      settled is not None and settled * DT <= 0.35, f"after {settled} frames")

# THE ORIGINAL FIELD BUG: the first frame a hand is seen is the blurriest,
# and it used to be latched. Here frame 1 confidently says "this is the left
# hand" and it is wrong — the vote must outvote it, not obey it.
ra_bug = RoleAssigner()
ra_bug.assign([{"pts": synthetic_hand(200, 240), "label": "Right",
                "score": 0.99}], W, DT)
seq, _ = lone_frames(ra_bug, "Left", 0.95, 12, px=200, t0=DT)
check("a confident wrong first frame cannot capture the role",
      seq[-1]["cursor"] is not None, "right hand still stuck as 'left'")

# no stale latch across an absence: the same hand position, a new identity
ra_gap = RoleAssigner()
lone_frames(ra_gap, "Right", 0.95, 12, px=300)       # left hand, identified
ra_gap.assign([], W, 5.0)                            # both hands down
seq, _ = lone_frames(ra_gap, "Left", 0.95, 12, px=300, t0=5.0)
check("identity is not carried across an absence",
      seq[-1]["cursor"] is not None)

# a two-hand stretch clears the vote, so the surviving hand cannot inherit
# the other one's identity when you drop one
ra_two = RoleAssigner()
lone_frames(ra_two, "Left", 0.95, 12, px=500)        # right hand alone: cursor
roles = ra_two.assign([left_h, right_h], W, 1.0)     # left hand joins
roles = ra_two.assign([{"pts": synthetic_hand(140, 240), "label": "Right",
                        "score": 0.95}], W, 1.0 + DT)   # right hand drops
check("the surviving hand does not inherit the other's identity",
      roles["cursor"] is None)

# launcher cooldown: the off hand must be present for launcher_cooldown_s
# before off_ready() lets the launcher fire — including when it is the ONLY
# hand up, which is the whole point of the new policy
ra_cd = RoleAssigner(launcher_cooldown_s=1.0)
right_cd = {"pts": synthetic_hand(500, 240), "label": "Left", "score": 0.9}
left_cd = {"pts": synthetic_hand(140, 240), "label": "Right", "score": 0.9}
ra_cd.assign([left_cd, right_cd], W, now=10.0)
check("off hand fresh -> launcher held back", not ra_cd.off_ready(10.5))
ra_cd.assign([left_cd, right_cd], W, now=11.2)
check("off hand stable past cooldown -> launcher allowed",
      ra_cd.off_ready(11.2))
ra_cd.assign([right_cd], W, now=11.3)                 # left hand drops out
ra_cd.assign([left_cd, right_cd], W, now=11.4)        # ...and returns
check("off hand reappearing restarts the cooldown", not ra_cd.off_ready(11.5))

ra_solo = RoleAssigner(launcher_cooldown_s=1.0)
seq, t = lone_frames(ra_solo, "Right", 0.95, 12, t0=20.0)   # left hand alone
check("a lone off hand still serves its launcher cooldown",
      not ra_solo.off_ready(t))
check("...and is allowed once the cooldown has run",
      ra_solo.off_ready(t + 1.0))

# overlap freeze: labels flip when hands come together; identity must hold
ra4 = RoleAssigner()
ra4.assign([{"pts": synthetic_hand(150, 240), "label": "Left", "score": 0.9},
            {"pts": synthetic_hand(500, 240), "label": "Right", "score": 0.9}], W)
a = synthetic_hand(310, 240)   # was-left hand drifting right
b = synthetic_hand(345, 200)   # was-right hand drifting left (overlapping now)
roles = ra4.assign([{"pts": b, "label": "Left", "score": 0.9},     # labels flipped!
                    {"pts": a, "label": "Right", "score": 0.9}], W)
check("overlapping hands keep identity (label flip ignored)",
      roles["left"] is a and roles["right"] is b)

# left-handed user: dominant='left' flips the cursor role
ra5 = RoleAssigner(dominant="left")
roles = ra5.assign([left_h, right_h], W)
check("dominant='left' -> left hand drives cursor",
      roles["cursor"] is left_h["pts"])

# ...and mirrors the lone-hand rule: now the RIGHT hand is launcher-only
ra6 = RoleAssigner(dominant="left")
seq, _ = lone_frames(ra6, "Left", 0.95, 30)          # the user's right hand
check("dominant='left': a lone right hand never drives the cursor",
      all(r["cursor"] is None for r in seq) and seq[-1]["off"] is not None)

ra7 = RoleAssigner(dominant="left")
seq, _ = lone_frames(ra7, "Right", 0.95, 12)         # the user's left hand
check("dominant='left': a lone left hand is the cursor",
      seq[-1]["cursor"] is not None)

# ===================== suspend: launcher hand up on its own ==================
# When the only hand in frame is the launcher hand (or one the vote hasn't
# identified yet) there is no cursor hand, but that is NOT a lost hand: the
# pointer freezes and stays engaged, so reaching for a launcher slot doesn't
# cost a fresh 1.2 s engage when you come back to pointing.
def engage(ctrl, px=320, py=240, t0=0.0, n=12):
    t = t0
    for _ in range(n):
        t += DT
        ctrl.update(synthetic_hand(px, py), FRAME, t)
    return t


def suspended(ctrl, n, t0):
    t, info = t0, None
    for _ in range(n):
        t += DT
        info = ctrl.update(None, FRAME, t, suspend=True)
    return info, t


m_sus = NullMouse()
c_sus = GestureController(m_sus, engage_hold_s=0.25, dead_zone_px=0.0,
                          filter_min_cutoff=None, lose_grace_s=0.25)
t = engage(c_sus)
assert c_sus.state == TRACKING, c_sus.state
anchor = c_sus.anchor
m_sus.events.clear()
info, t = suspended(c_sus, 20, t)                # 0.66 s, well past lose_grace
check("launcher hand up keeps the cursor engaged", info["state"] == TRACKING,
      f"state={info['state']}")
check("...with the anchor untouched", c_sus.anchor == anchor)
check("...and the pointer frozen",
      not [e for e in m_sus.events if e[0] == "move"],
      f"events={m_sus.events[:3]}")
check("...and reported as suspended", info["suspended"] is True)

for i in range(1, 5):                            # cursor hand comes back
    t += DT
    info = c_sus.update(synthetic_hand(320 + 15 * i, 240), FRAME, t)
check("pointing resumes with no re-engage", info["state"] == TRACKING
      and [e for e in m_sus.events if e[0] == "move"])

# the contrast: a genuinely lost hand (nothing in frame) still disengages
m_lost = NullMouse()
c_lost = GestureController(m_lost, engage_hold_s=0.25, dead_zone_px=0.0,
                           filter_min_cutoff=None, lose_grace_s=0.25)
t = engage(c_lost)
for _ in range(20):
    t += DT
    info = c_lost.update(None, FRAME, t)         # suspend defaults to False
check("both hands down still disengages", info["state"] == IDLE,
      f"state={info['state']}")

# a held drag survives the launcher hand going up
m_drag = NullMouse()
c_drag = GestureController(m_drag, engage_hold_s=0.25, dead_zone_px=0.0,
                           filter_min_cutoff=None)
t = engage(c_drag, px=300)
for _ in range(4):                               # pinch: left button down
    t += DT
    c_drag.update(hand(300, 240, 20), FRAME, t)
assert ("down",) in m_drag.events, m_drag.events
m_drag.events.clear()
info, t = suspended(c_drag, 20, t)
check("a held drag is not dropped while suspended",
      ("up",) not in m_drag.events and info["state"] != IDLE,
      f"events={m_drag.events} state={info['state']}")

# an engage in progress is PAUSED, not cancelled — a hand crossing the frame
# for a moment must not cost you the hold you were part-way through
m_eng = NullMouse()
c_eng = GestureController(m_eng, engage_hold_s=0.3, dead_zone_px=0.0,
                          filter_min_cutoff=None)
t = 0.0
for _ in range(5):                               # 0.17 s of the 0.3 s hold
    t += DT
    c_eng.update(synthetic_hand(320, 240), FRAME, t)
assert c_eng.state == ENGAGING, c_eng.state
info, t = suspended(c_eng, 15, t)                # 0.5 s suspended
check("a suspended engage neither completes nor resets",
      info["state"] == ENGAGING, f"state={info['state']}")
for _ in range(5):                               # the remaining 0.17 s
    t += DT
    info = c_eng.update(synthetic_hand(320, 240), FRAME, t)
check("the engage completes on the pose time it actually had",
      info["state"] == TRACKING, f"state={info['state']}")

# ================= a resting hand must not drive the launcher ================
# From a couple of metres back the camera reads fingers on a hand lying on a
# knee, and counting those launched apps while the user just sat there. The
# gate: the hand only counts while it is RAISED — knuckles clearly above the
# wrist — which a resting hand never is.
from gestures import hand_raised

FH = FRAME[1]
check("an upright hand counts as raised",
      hand_raised(synthetic_hand(320, 240), FH))


def resting_hand(px, py):
    """A hand lying flat-ish on a knee: wrist and knuckles nearly level,
    knuckles a touch BELOW the wrist, fingers readable."""
    pts = [(x, py + (y - py) * 0.15) for (x, y) in synthetic_hand(px, py)]
    pts[0] = (px, py - 6)                  # wrist a shade above the knuckles
    return pts


check("a hand resting on a knee is not raised",
      not hand_raised(resting_hand(320, 400), FH))

# barely-tilted is still not enough — the margin is deliberate
barely = list(synthetic_hand(320, 240))
barely[0] = (320, barely[9][1] + 0.02 * FH)    # wrist just under the knuckles
check("a barely-tilted hand stays below the raise margin",
      not hand_raised(barely, FH))

# ==================== the shaka sign = recenter the cursor ===================
# Thumb and pinky out, EVERYTHING else fully folded. It replaced the
# rock-and-roll horns, which fired from a barely-curled resting hand:
# "not extended" let half-bent fingers through.
from gestures import (RockerDetector, is_reset_pose, thumb_extended,
                      finger_folded)

horns = count_hand(320, 240, ("pinky",))       # thumb splays by default
check("thumb+pinky with the rest fully folded is the reset sign",
      is_reset_pose(horns))
check("a peace sign is not the reset sign",
      not is_reset_pose(count_hand(320, 240, ("index", "middle"))))
check("the brake pose is not the reset sign",
      not is_reset_pose(count_hand(320, 240, ("index",))))
check("an open hand is not the reset sign",
      not is_reset_pose(synthetic_hand(320, 240)))
check("the old horns (index up) are no longer the reset sign",
      not is_reset_pose(count_hand(320, 240, ("index", "pinky"))))


def half_curl(px, py, up=("pinky",)):
    """Fingers only PART-folded — a relaxed hand, not a deliberate sign."""
    pts = list(synthetic_hand(px, py))
    bases = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for name, b in bases.items():
        if name in up:
            continue
        x = pts[b][0]
        pts[b + 1] = (x, py - 90)
        pts[b + 2] = (x, py - 75)
        pts[b + 3] = (x, py - 62)      # tip barely inside the knuckle
    return pts


check("half-curled fingers are NOT 'fully folded' — a relaxed hand can't fire",
      not is_reset_pose(half_curl(320, 240)))
check("...because the fold test demands a real margin",
      not finger_folded(half_curl(320, 240), "middle"))

# a tucked thumb kills the sign too
tucked = list(horns)
tucked[4] = (320 - 10, 240 - 45)                # thumb across the palm
check("a tucked thumb is not the reset sign", not is_reset_pose(tucked))
check("...caught by the thumb test itself", not thumb_extended(tucked))

rk = RockerDetector(hold_s=0.3, refractory_s=1.0)
t, fired = 0.0, 0
for _ in range(30):                        # held a full second
    t += DT
    if rk.update(horns, t):
        fired += 1
check("held horns fire exactly once", fired == 1, f"fired={fired}")
check("...and not again until the refractory has passed",
      not rk.update(horns, t + 0.2))
check("...but do fire again after it", any(
      rk.update(horns, t + 1.1 + i * DT) for i in range(12)))

rk2 = RockerDetector(hold_s=0.3)
t = 0.0
for _ in range(4):                         # 0.13s — under the hold
    t += DT
    fired = rk2.update(horns, t)
check("a transient flash of horns does not fire", not fired)
rk2.update(synthetic_hand(320, 240), t + DT)
check("...and opening the hand resets the hold", rk2.progress == 0.0)

# through the controller: horns held -> mouse.center(), and never mid-drag
m_rc = NullMouse()
c_rc = GestureController(m_rc, engage_hold_s=0.25, dead_zone_px=0.0,
                         filter_min_cutoff=None, recenter_hold_s=0.3)
t = 0.0
for _ in range(12):                        # engage
    t += DT
    c_rc.update(synthetic_hand(320, 240), FRAME, t)
assert c_rc.state == TRACKING, c_rc.state
for _ in range(14):                        # hold the horns
    t += DT
    c_rc.update(horns, FRAME, t)
check("held horns teleport the cursor home", ("center",) in m_rc.events,
      f"events={[e for e in m_rc.events if e[0] != 'move']}")

m_rd = NullMouse()
c_rd = GestureController(m_rd, engage_hold_s=0.25, dead_zone_px=0.0,
                         filter_min_cutoff=None, recenter_hold_s=0.3)
t = 0.0
for _ in range(12):
    t += DT
    c_rd.update(synthetic_hand(300, 240), FRAME, t)
for _ in range(4):                         # pinch: left button held
    t += DT
    c_rd.update(hand(300, 240, 20), FRAME, t)
assert ("down",) in m_rd.events
for _ in range(14):                        # horns after the pinch
    t += DT
    c_rd.update(horns, FRAME, t)
# You cannot physically hold a pinch AND make horns — extending the index
# releases the button. So the invariant is ORDER: the drag always ends
# before any recenter can fire; the cursor is never yanked mid-hold.
evs = [e[0] for e in m_rd.events if e[0] != "move"]
check("the drag is released before any recenter fires",
      "center" not in evs[:evs.index("up") + 1] if "up" in evs else False,
      f"events={evs}")

m_off = NullMouse()
c_off = GestureController(m_off, engage_hold_s=0.25, dead_zone_px=0.0,
                          filter_min_cutoff=None, recenter_enabled=False)
t = 0.0
for _ in range(12):
    t += DT
    c_off.update(synthetic_hand(320, 240), FRAME, t)
for _ in range(14):
    t += DT
    c_off.update(horns, FRAME, t)
check("recenter can be switched off", ("center",) not in m_off.events)

# ==================== FingerLauncher (count-based slots) =====================
lc = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(15):                      # hold one finger up 0.5s
    t += DT
    f = lc.update(count_hand(200, 240, ("index",)), t)
    if f is not None:
        fired.append(f)
check("1 finger held 0.3s fires slot 0 exactly once", fired == [0],
      f"fired={fired}")

# it's the COUNT that matters, not which finger: a lone middle is still "1"
lc1b = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(15):
    t += DT
    f = lc1b.update(count_hand(200, 240, ("middle",)), t)
    if f is not None:
        fired.append(f)
check("a lone middle finger also fires slot 0 (count, not identity)",
      fired == [0], f"fired={fired}")

# index+middle = 2 fingers -> slot 1; index+middle+ring = 3 -> slot 2
lc2 = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(15):
    t += DT
    f = lc2.update(count_hand(200, 240, ("index", "middle")), t)
    if f is not None:
        fired.append(f)
check("2 fingers fire slot 1", fired == [1], f"fired={fired}")

lc3a = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(15):
    t += DT
    f = lc3a.update(count_hand(200, 240, ("index", "middle", "ring")), t)
    if f is not None:
        fired.append(f)
check("3 fingers fire slot 2", fired == [2], f"fired={fired}")

# Four fingers with the THUMB TUCKED -> slot 3. A fully open hand — thumb
# out too — is neutral now: raising an open hand is the most natural pose in
# front of this app (it is how the cursor engages), and counting it as a
# command meant showing your left hand launched whatever lived in slot 4.
def four_no_thumb(px, py):
    pts = list(synthetic_hand(px, py))
    pts[4] = (px - 10, py - 45)              # thumb across the palm
    return pts


lc4a = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(20):
    t += DT
    f = lc4a.update(four_no_thumb(200, 240), t)
    if f is not None:
        fired.append(f)
check("4 fingers with the thumb tucked fire slot 3 once", fired == [3],
      f"fired={fired}")

lc_open = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(20):
    t += DT
    f = lc_open.update(synthetic_hand(200, 240), t)
    if f is not None:
        fired.append(f)
check("a fully open hand (thumb out) is neutral, not slot 4", fired == [],
      f"fired={fired}")

# changing the count resets the hold; cooldown blocks a rapid second fire
lc3 = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t, fired = 0.0, []
for i in range(12):                      # fire slot 0
    t += DT
    f = lc3.update(count_hand(200, 240, ("index",)), t)
    if f is not None:
        fired.append(f)
for i in range(12):                      # immediately hold 2 up: in cooldown
    t += DT
    f = lc3.update(count_hand(200, 240, ("index", "middle")), t)
    if f is not None:
        fired.append(f)
check("cooldown blocks the next count", fired == [0], f"fired={fired}")
while t < 1.6:                           # wait out the cooldown, keep holding
    t += DT
    f = lc3.update(count_hand(200, 240, ("index", "middle")), t)
    if f is not None:
        fired.append(f)
check("after cooldown the held 2-count fires slot 1", fired == [0, 1],
      f"fired={fired}")

# hand disappearing resets cleanly
lc4 = FingerLauncher(hold_s=0.3, cooldown_s=1.0)
t = 0.0
for i in range(5):
    t += DT
    lc4.update(count_hand(200, 240, ("ring",)), t)
lc4.update(None, t + DT)
check("hand loss resets hold", lc4.current is None and lc4.progress == 0.0)

# ================= grab + push down + land -> minimize =======================
def run_flick(det, push, hold=4, armed=True, armed_since=-1.0):
    """Feed a push then a landing (hand still, fist held); return the
    result. armed_since defaults to long-before-the-stroke so detector
    tests exercise the stroke mechanics; the age gate has its own tests."""
    t2, res2 = 0.0, None
    for (x, y) in push:
        t2 += DT
        r = det.update((x, y), HW, armed, t2, armed_since=armed_since)
        res2 = r or res2
    for _ in range(hold):
        t2 += DT
        r = det.update(push[-1], HW, armed, t2, armed_since=armed_since)
        res2 = r or res2
    return res2


# a short deliberate push (~1.2 hand-widths at ~7.5 hw/s) + landing fires
push_short = [(320, 100 + 25 * i) for i in range(6)]
check("grab + short push + land fires minimize",
      run_flick(FlickDownDetector(), push_short) == "minimize")

# the two knobs are now INDEPENDENT gates (the old metering made
# min_speed*window a hidden distance bar that overrode hand_widths)
check("hand_widths independently rejects the same push",
      run_flick(FlickDownDetector(hand_widths=1.6), push_short) is None)
check("min_speed_hw_s independently rejects the same push",
      run_flick(FlickDownDetector(min_speed_hw_s=9.0), push_short) is None)

# The pull now fires on the stroke itself, without waiting for the hand to
# stop. "Push it down and hold still" was the single hardest gesture in the
# app to perform on purpose; what keeps it from firing by accident is the
# armed fist, not the landing.
fd_nl = FlickDownDetector()
t, res = 0.0, None
for i in range(20):                      # a pull that keeps travelling
    t += DT
    r = fd_nl.update((320, 100 + 30 * i), HW, True, t, armed_since=-1.0)
    res = r or res
check("a committed pull fires without waiting to land", res == "minimize")

# ...and it fires exactly once, not on every frame of a long pull
fd_once = FlickDownDetector()
t, fired = 0.0, 0
for i in range(20):
    t += DT
    if fd_once.update((320, 100 + 30 * i), HW, True, t,
                      armed_since=-1.0) == "minimize":
        fired += 1
check("a long pull still only minimises once", fired == 1, f"fired={fired}")

# the old behaviour is still available for anyone who wants it back
fd_land = FlickDownDetector(require_landing=True)
t, res = 0.0, None
for i in range(20):
    t += DT
    r = fd_land.update((320, 100 + 30 * i), HW, True, t, armed_since=-1.0)
    res = r or res
check("require_landing=True still waits for the hand to settle", res is None)

# casually lowering the hand (slow) never fires — even armed, even landing
fd2 = FlickDownDetector()
t, res = 0.0, None
for i in range(30):                      # 12px/frame = 3.6 hw/s, below floor
    t += DT
    r = fd2.update((320, 100 + 12 * i), HW, True, t)
    res = r or res
for _ in range(4):                       # comes to rest, fist still held
    t += DT
    r = fd2.update((320, 448), HW, True, t)
    res = r or res
check("slow hand lowering does not fire", res is None)

# fast UPWARD motion never fires
fd3 = FlickDownDetector()
t, res = 0.0, None
for i in range(6):
    t += DT
    r = fd3.update((320, 400 - 45 * i), HW, True, t)
    res = r or res
check("upward motion does not fire", res is None)

# fast horizontal motion never fires the down-flick
fd4 = FlickDownDetector()
t, res = 0.0, None
for i in range(8):
    t += DT
    r = fd4.update((600 - 45 * i, 240), HW, True, t)
    res = r or res
check("horizontal swipe does not minimize", res is None)

# an unarmed hand dropping fast never fires
fd5 = FlickDownDetector()
t, res = 0.0, None
for i in range(6):
    t += DT
    r = fd5.update((320, 100 + 45 * i), HW, False, t)
    res = r or res
check("unarmed drop does not fire", res is None)

# never fires while a button is held (dragging something downward). Pinch is
# now the only way to hold the left button, so this is a pinch-drag.
m6 = NullMouse()
c6 = GestureController(m6, engage_hold_s=0.25, dead_zone_px=0.0,
                       filter_min_cutoff=None)
t = 0.0
for _ in range(12):                      # engage
    t += DT
    c6.update(synthetic_hand(320, 160), FRAME, t)
assert c6.state == TRACKING
for _ in range(4):                       # pinch down (left button held)
    t += DT
    c6.update(hand(320, 160, 20), FRAME, t)
assert any(e == ("down",) for e in m6.events), m6.events
for i in range(6):                       # drag downward fast while pinched
    t += DT
    c6.update(hand(320, 160 + 45 * (i + 1), 20), FRAME, t)
check("no minimize while dragging", ("minimize",) not in m6.events,
      f"events={[e for e in m6.events if e[0] != 'move']}")

# ==================== volume: thumb + ring, 1:1 mapping ======================
from gestures import VOLUME

def ring_pinch(px, py, dist=20.0):
    """Thumb pinched to the RING fingertip, index+middle still up."""
    import math as _mm
    pts = list(synthetic_hand(px, py))
    rx, ry = pts[16][0], py - 90
    pts[16] = (rx, ry)                       # ring tip curls to meet the thumb
    rest = (px - 60, py - 40)
    d = _mm.hypot(rest[0] - rx, rest[1] - ry)
    ux, uy = (rest[0] - rx) / d, (rest[1] - ry) / d
    pts[4] = (rx + ux * dist, ry + uy * dist)
    return pts


m_v = NullMouse()
c_v = GestureController(m_v, engage_hold_s=0.25, dead_zone_px=0.0,
                        filter_min_cutoff=None)
t = 0.0
for _ in range(12):                          # engage
    t += DT
    c_v.update(synthetic_hand(320, 240), FRAME, t)
assert c_v.state == TRACKING
for _ in range(4):                           # pinch thumb to ring
    t += DT
    info = c_v.update(ring_pinch(320, 240), FRAME, t)
check("thumb+ring enters VOLUME", info["state"] == VOLUME
      and info["volume_down"], f"state={info['state']}")
check("volume pinch presses no mouse button",
      not [e for e in m_v.events if e[0] in ("down", "rdown")],
      f"events={m_v.events}")

m_v.events.clear()
for i in range(1, 9):                        # raise the hand: volume up
    t += DT
    c_v.update(ring_pinch(320, 240 - 12 * i), FRAME, t)
up_steps = sum(e[1] for e in m_v.events if e[0] == "volume")
check("raising the hand raises the volume", up_steps > 0, f"steps={up_steps}")
check("cursor stays put while setting volume",
      not [e for e in m_v.events if e[0] == "move"])

m_v.events.clear()
for i in range(1, 9):                        # back down to where it started
    t += DT
    c_v.update(ring_pinch(320, 240 - 96 + 12 * i), FRAME, t)
down_steps = sum(e[1] for e in m_v.events if e[0] == "volume")
check("returning to the start undoes it exactly (1:1)",
      up_steps + down_steps == 0, f"up={up_steps} down={down_steps}")

for _ in range(6):                           # release the pinch
    t += DT
    info = c_v.update(synthetic_hand(320, 240), FRAME, t)
check("releasing exits VOLUME", not info["volume_down"]
      and info["state"] == TRACKING, f"state={info['state']}")

# a plain index pinch must never be read as volume (nearest-finger rule)
m_c = NullMouse()
c_c = GestureController(m_c, engage_hold_s=0.25, dead_zone_px=0.0,
                        filter_min_cutoff=None)
t = 0.0
for _ in range(12):
    t += DT
    c_c.update(synthetic_hand(320, 240), FRAME, t)
for _ in range(5):
    t += DT
    info = c_c.update(hand(320, 240, 20), FRAME, t)
check("index pinch still clicks, never volume",
      ("down",) in m_c.events and not info["volume_down"]
      and not [e for e in m_c.events if e[0] == "volume"])

# volume can be switched off entirely
m_off = NullMouse()
c_off = GestureController(m_off, engage_hold_s=0.25, dead_zone_px=0.0,
                          filter_min_cutoff=None, volume_enabled=False)
t = 0.0
for _ in range(12):
    t += DT
    c_off.update(synthetic_hand(320, 240), FRAME, t)
for _ in range(6):
    t += DT
    info = c_off.update(ring_pinch(320, 240), FRAME, t)
for i in range(1, 6):
    t += DT
    c_off.update(ring_pinch(320, 240 - 15 * i), FRAME, t)
check("volume disabled = no volume events",
      not [e for e in m_off.events if e[0] == "volume"] and not info["volume_down"])

# ==================== the lowered arm must not minimise ======================
# A hand relaxes into a loose fist ON ITS WAY DOWN: open for the first part
# of the drop, closed for the rest, fast and downward-dominant throughout.
# The clench arms mid-stroke, so the clench-first age gate must refuse it.
m_la = NullMouse()
c_la = GestureController(m_la)
t = 0.0
for i in range(4):                       # open hand, already falling
    t += DT
    c_la.update(synthetic_hand(320, 100 + 40 * i), FRAME, t)
for i in range(4, 12):                   # closes mid-drop, keeps falling
    t += DT
    c_la.update(fist(320, 100 + 40 * i), FRAME, t)
check("a hand that closes while falling does not minimise",
      ("minimize",) not in m_la.events,
      f"events={[e for e in m_la.events if e[0] != 'move']}")

# ...but the same drop after a HELD clench is a deliberate pull and fires
m_dp = NullMouse()
c_dp = GestureController(m_dp)
arm_then(c_dp, m_dp, [(320, 100 + 40 * i) for i in range(7)])
check("clench, hold a beat, pull down -> minimise",
      ("minimize",) in m_dp.events,
      f"events={[e for e in m_dp.events if e[0] != 'move']}")

# a clench that exists but is YOUNGER than the stroke also refuses
m_yc = NullMouse()
c_yc = GestureController(m_yc)
arm_then(c_yc, m_yc, [(320, 100 + 40 * i) for i in range(7)],
         clench_frames=1)                # ~0.03s old: not a held fist
check("a just-formed fist cannot fire the pull",
      ("minimize",) not in m_yc.events,
      f"events={[e for e in m_yc.events if e[0] != 'move']}")

# ===================== grab + tug UP -> restore ==============================
m_up = NullMouse()
c_up = GestureController(m_up)
arm_then(c_up, m_up, [(320, 380 - 40 * i) for i in range(7)])
check("clench, hold, tug up -> restore", ("restore",) in m_up.events,
      f"events={[e for e in m_up.events if e[0] != 'move']}")
check("...and the tug does not also minimise",
      ("minimize",) not in m_up.events)

m_ud = NullMouse()
c_ud = GestureController(m_ud, flick_up_enabled=False)
arm_then(c_ud, m_ud, [(320, 380 - 40 * i) for i in range(7)])
check("tug up with the gesture disabled does nothing",
      ("restore",) not in m_ud.events)

# after a minimise, the arm's return upswing must NOT restore what it hid
m_rt = NullMouse()
c_rt = GestureController(m_rt)
t = arm_then(c_rt, m_rt, [(320, 100 + 40 * i) for i in range(7)])
assert ("minimize",) in m_rt.events
for i in range(7):                       # the hand comes straight back up
    t += DT
    c_rt.update(fist(320, 340 - 40 * i), FRAME, t)
check("the return upswing after a minimise does not restore",
      ("restore",) not in m_rt.events,
      f"events={[e for e in m_rt.events if e[0] != 'move']}")

# ===================== the restore stack (real backend) ======================
import mouse_input as _mi


class _WinRecorder:
    """user32 stand-in: three windows, all 'iconic' once minimised, plus a
    fake desktop for the EnumWindows fallback."""
    def __init__(self):
        self.fg = iter([111, 222, 333])
        self.shown = []
        self.closed = set()
        self.desktop = []   # (hwnd, visible, title_len, exstyle) in z-order

    def GetForegroundWindow(self):
        return next(self.fg)

    def ShowWindow(self, hwnd, how):
        self.shown.append((hwnd, how))

    def IsWindow(self, hwnd):
        return 0 if hwnd in self.closed else 1

    def IsIconic(self, hwnd):
        return 1

    def SetForegroundWindow(self, hwnd):
        self.shown.append((hwnd, "fg"))

    def IsWindowVisible(self, hwnd):
        return next((1 if v else 0 for (h, v, _t, _x) in self.desktop
                     if h == hwnd), 0)

    def GetWindowTextLengthW(self, hwnd):
        return next((t for (h, _v, t, _x) in self.desktop if h == hwnd), 0)

    def GetWindowLongPtrW(self, hwnd, _idx):
        return next((x for (h, _v, _t, x) in self.desktop if h == hwnd), 0)

    def EnumWindows(self, proc, lparam):
        for (h, _v, _t, _x) in self.desktop:
            if not proc(h, lparam):
                break
        return 1


_real_u32 = _mi._user32
_rec = _WinRecorder()
_mi._user32 = _rec
try:
    mm = _mi.Mouse()
    mm.minimize_window()                 # 111
    mm.minimize_window()                 # 222
    mm.minimize_window()                 # 333
    _rec.closed.add(333)                 # user closed the newest one
    ok1 = mm.restore_window()
    ok2 = mm.restore_window()
    ok3 = mm.restore_window()
finally:
    _mi._user32 = _real_u32

restores = [h for (h, how) in _rec.shown if how == _mi.SW_RESTORE]
check("restore brings back the most recent minimised window, newest first",
      ok1 and ok2 and restores == [222, 111], f"restores={restores}")
check("a window the user closed meanwhile is skipped, not crashed on",
      333 not in restores)
check("an empty stack with an empty desktop restores nothing", ok3 is False)

# the fallback: an empty stack still restores the desktop's most recently
# used minimised window — a fresh app start must not make the tug-up dead
_rec2 = _WinRecorder()
_rec2.desktop = [
    (901, True, 5, _mi.WS_EX_TOOLWINDOW),   # tool window: never a target
    (902, False, 5, 0),                     # invisible: skipped
    (903, True, 0, 0),                      # untitled: skipped
    (904, True, 9, 0),                      # the real one, nearest the top
    (905, True, 9, 0),                      # older; must not be chosen
]
_mi._user32 = _rec2
try:
    ok_fb = _mi.Mouse().restore_window()
finally:
    _mi._user32 = _real_u32
fb = [h for (h, how) in _rec2.shown if how == _mi.SW_RESTORE]
check("with nothing of its own, the tug restores the desktop's newest",
      ok_fb and fb == [904], f"restored={fb}")


print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

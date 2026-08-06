"""The precision brake: squeeze the middle, ring and pinky to slow the cursor.

Two halves. The first covers the pure logic — the curl measure, the pose
gate, and the proportional mapping. The second covers what the brake shares
a hand shape with: a fist curls the same three fingers, a peace sign curls
two of them, and a click curls the index. Those are the tests that matter,
because the brake is only safe if it can't be mistaken for any of them.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from gestures import (BrakeDetector, GestureController, brake_curl,
                      is_brake_pose, ARMED, BRAKING, PINCHED, SCROLL,
                      TRACKING, VOLUME)
from mouse_input import NullMouse
from test_gestures import (FRAME, DT, brake_hand, brake_pinch, clench, hand,
                           peace_hand, synthetic_hand)

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def fist(px, py):
    return synthetic_hand(px, py, open_palm=False)


# ============================== the curl measure =============================
open_curl = brake_curl(synthetic_hand(320, 240))
full_curl = brake_curl(brake_hand(320, 240, 1.0))
check("an open hand reads as uncurled", open_curl < 0, f"curl={open_curl:.2f}")
check("a full squeeze reads as curled", full_curl > 0.5, f"curl={full_curl:.2f}")

sweep = [brake_curl(brake_hand(320, 240, f / 10.0)) for f in range(11)]
check("curl rises monotonically with the squeeze",
      all(b >= a - 1e-9 for a, b in zip(sweep, sweep[1:])),
      f"{sweep[0]:.2f} -> {sweep[-1]:.2f}")

# distance invariance: the same pose farther from the camera reads the same
near = brake_curl(brake_hand(320, 240, 0.7))
far = brake_curl([(320 + (x - 320) * 0.5, 240 + (y - 240) * 0.5)
                  for (x, y) in brake_hand(320, 240, 0.7)])
check("curl is invariant to distance from the camera", abs(near - far) < 0.02,
      f"near={near:.3f} far={far:.3f}")

# a click must not change the reading — that is why the index is excluded
plain = brake_curl(brake_hand(320, 240))
pinched = brake_curl(brake_pinch(320, 240))
check("pinching does not change the curl reading", abs(plain - pinched) < 1e-9,
      f"plain={plain:.3f} pinched={pinched:.3f}")

# ================================ the pose gate ==============================
check("the brake pose is recognised", is_brake_pose(brake_hand(320, 240)))
check("an open hand is not a brake", not is_brake_pose(synthetic_hand(320, 240)))
check("a fist is not a brake (index is curled too)",
      not is_brake_pose(fist(320, 240)))
check("a peace sign is not a brake (middle is extended)",
      not is_brake_pose(peace_hand(320, 240)))
check("a plain pinch is not a brake", not is_brake_pose(hand(320, 240, 20)))

# ============================== the mapping ==================================
def settle(det, pts, n=40, pose_ok=None):
    """Hold a pose until the smoothing has converged."""
    for _ in range(n):
        det.update(pts, pose_ok=is_brake_pose(pts) if pose_ok is None
                   else pose_ok)
    return det.amount


det = BrakeDetector(onset=0.15, full=0.75, min_scale=0.25)
check("an open hand never brakes", settle(det, synthetic_hand(320, 240)) == 0.0)
check("...and leaves the speed untouched", det.scale == 1.0)

det = BrakeDetector(onset=0.15, full=0.75, min_scale=0.25)
check("a full squeeze reaches the floor", settle(det, brake_hand(320, 240)) > 0.95)
check("...slowing to min_scale", abs(det.scale - 0.25) < 0.02,
      f"scale={det.scale:.3f}")

# proportional in between: more squeeze, more slowdown, in order
amounts = []
for f in (0.5, 0.7, 0.85, 1.0):
    d = BrakeDetector(onset=0.15, full=0.75, min_scale=0.25)
    amounts.append(settle(d, brake_hand(320, 240, f)))
check("the brake is proportional, not a switch",
      all(a <= b + 1e-9 for a, b in zip(amounts, amounts[1:]))
      and amounts[-1] > amounts[0],
      f"amounts={[round(a, 2) for a in amounts]}")

d_off = BrakeDetector(enabled=False)
check("disabled means never braking",
      settle(d_off, brake_hand(320, 240)) == 0.0 and d_off.scale == 1.0)

# smoothing: one noisy frame cannot snap the speed
d_noise = BrakeDetector(smoothing=0.35)
settle(d_noise, synthetic_hand(320, 240))
d_noise.update(brake_hand(320, 240), pose_ok=True)     # a single squeezed frame
check("one frame cannot slam the brake on", d_noise.amount < 0.5,
      f"amount={d_noise.amount:.2f}")

# STICKY: a pinch curls the index, so the pose gate fails — but a brake
# already being held must survive it, or every click would release the brake
# at the exact moment it was needed. That grace covers the frames between
# the index starting to curl and the pinch actually registering as a button.
d_stick = BrakeDetector(stick_frames=8)
settle(d_stick, brake_hand(320, 240))
held = d_stick.amount
for _ in range(6):                                  # inside the grace window
    d_stick.update(brake_pinch(320, 240), pose_ok=False)
check("a held brake survives the pinch that fails the pose gate",
      d_stick.amount > 0.9 * held, f"{held:.2f} -> {d_stick.amount:.2f}")

# ...but opening the hand still releases it
for _ in range(40):
    d_stick.update(synthetic_hand(320, 240), pose_ok=False)
check("opening the hand releases the brake", d_stick.amount == 0.0)

d_gone = BrakeDetector()
settle(d_gone, brake_hand(320, 240))
for _ in range(40):
    d_gone.update(None)
check("losing the hand releases the brake", d_gone.amount == 0.0)

# ========================= in the running controller =========================
def ctrl(mouse, sensitivity=5.0, **kw):
    return GestureController(mouse, dead_zone_px=0.0, filter_min_cutoff=None,
                             engage_hold_s=0.25, sensitivity=sensitivity, **kw)


def engage(c, px=320, py=240, t0=0.0, n=12):
    t = t0
    for _ in range(n):
        t += DT
        c.update(synthetic_hand(px, py), FRAME, t)
    return t


def travel(c, mouse, pose, t0, steps=6, step=8):
    """Move the hand right in `pose`, return the pixels the cursor moved."""
    t = t0
    mouse.events.clear()
    for i in range(1, steps + 1):
        t += DT
        c.update(pose(320 + step * i, 240), FRAME, t)
    return sum(e[1] for e in mouse.events if e[0] == "move"), t


m_free = NullMouse()
c_free = ctrl(m_free)
t = engage(c_free)
free_px, _ = travel(c_free, m_free, lambda x, y: synthetic_hand(x, y), t)

m_brk = NullMouse()
c_brk = ctrl(m_brk)
t = engage(c_brk)
for _ in range(20):                       # squeeze, hand still
    t += DT
    info = c_brk.update(brake_hand(320, 240), FRAME, t)
brk_px, t = travel(c_brk, m_brk, lambda x, y: brake_hand(x, y), t)
check("squeezing slows the cursor", 0 < brk_px < free_px * 0.6,
      f"free={free_px}px braked={brk_px}px")
check("the state reads BRAKING", info["state"] == BRAKING,
      f"state={info['state']}")
check("...and reports its speed factor", info["brake_scale"] < 0.4,
      f"scale={info['brake_scale']:.2f}")

# THE INVARIANT: a still hand never moves the cursor, braked or not
m_still = NullMouse()
c_still = ctrl(m_still)
t = engage(c_still)
for _ in range(15):
    t += DT
    c_still.update(brake_hand(320, 240), FRAME, t)
m_still.events.clear()
for _ in range(15):
    t += DT
    c_still.update(brake_hand(320, 240), FRAME, t)
check("a still hand emits nothing while braking",
      not [e for e in m_still.events if e[0] == "move"],
      f"moves={[e for e in m_still.events if e[0] == 'move'][:3]}")

# releasing gives the speed straight back
t = engage(c_brk, t0=t, n=12)
back_px, t = travel(c_brk, m_brk, lambda x, y: synthetic_hand(x, y), t)
check("releasing the squeeze restores full speed", back_px >= free_px * 0.9,
      f"free={free_px}px after-release={back_px}px")

# CLICKING WHILE BRAKING — the whole point. The ring finger is curled by
# definition here, and the pinch gate used to require it extended.
m_click = NullMouse()
c_click = ctrl(m_click)
t = engage(c_click)
for _ in range(12):                       # squeeze first
    t += DT
    c_click.update(brake_hand(320, 240), FRAME, t)
assert c_click._brake.engaged, c_click._brake.amount
for _ in range(6):                        # now pinch, still squeezing
    t += DT
    info = c_click.update(brake_pinch(320, 240), FRAME, t)
check("a click still registers while braking", ("down",) in m_click.events,
      f"events={[e for e in m_click.events if e[0] != 'move']}")
check("...and the brake is still held", info["brake"] > 0.5,
      f"brake={info['brake']:.2f}")
check("...and no swipe got armed by the curled hand",
      not info["fist_armed"] and info["state"] == PINCHED,
      f"state={info['state']} armed={info['fist_armed']}")

# ===================== it must not eat the other gestures ====================
# a fist still arms navigation: the brake needs the index OUT, a clench
# never has that, so the shared curl can't shadow it
m_fist = NullMouse()
c_fist = ctrl(m_fist)
t = 0.0
for _ in range(8):
    t += DT
    info = c_fist.update(fist(320, 240), FRAME, t)
check("a fist still arms navigation", info["fist_armed"],
      f"armed={info['fist_armed']} brake={info['brake']:.2f}")
check("...and a fist is not read as braking", info["brake"] == 0.0,
      f"brake={info['brake']:.2f}")

# the peace sign still scrolls — it curls ring+pinky, which reads as a deep
# three-finger curl, so only the middle-extended test keeps them apart
m_pc = NullMouse()
c_pc = ctrl(m_pc)
t = engage(c_pc)
for _ in range(10):
    t += DT
    info = c_pc.update(peace_hand(320, 240), FRAME, t)
check("the peace sign still enters scroll", info["state"] == SCROLL,
      f"state={info['state']} brake={info['brake']:.2f}")
check("...and is never read as braking", info["brake"] == 0.0,
      f"brake={info['brake']:.2f}")

# volume is thumb+ring, and the squeeze folds the ring right next to the
# thumb — too ambiguous to read, so it stays blocked while braking
m_vol = NullMouse()
c_vol = ctrl(m_vol)
t = engage(c_vol)
for _ in range(12):
    t += DT
    c_vol.update(brake_hand(320, 240), FRAME, t)
m_vol.events.clear()
for i in range(1, 10):
    t += DT
    info = c_vol.update(brake_hand(320, 240 - 12 * i), FRAME, t)
check("volume cannot fire while braking",
      not [e for e in m_vol.events if e[0] == "volume"]
      and info["state"] != VOLUME,
      f"events={[e for e in m_vol.events if e[0] != 'move']}")

# a disabled brake leaves everything exactly as it was
m_dis = NullMouse()
c_dis = ctrl(m_dis, brake_enabled=False)
t = engage(c_dis)
for _ in range(15):
    t += DT
    info = c_dis.update(brake_hand(320, 240), FRAME, t)
dis_px, t = travel(c_dis, m_dis, lambda x, y: brake_hand(x, y), t)
check("with the brake off the squeeze does nothing",
      info["brake"] == 0.0 and dis_px >= free_px * 0.9,
      f"free={free_px}px squeezed={dis_px}px")

# live retuning, because these thresholds only mean anything against a real
# hand and tuning them with a restart in between is how a session gets lost
c_live = ctrl(NullMouse())
c_live.apply_brake_params(min_scale=0.5, onset=0.0)
check("brake settings retune live",
      c_live._brake.min_scale == 0.5 and c_live._brake.onset == 0.0)
c_live.apply_brake_params(bogus_key=1, min_scale=None)
check("...and an unknown or empty setting is ignored",
      c_live._brake.min_scale == 0.5)

# ============ REGRESSION: a clench must not latch the brake ==================
# Found by review and reproduced before fixing. A real hand closes with the
# index last, so mid-clench it reads as "index out, middle in" — the brake
# shape. Engaging on that transient latched the brake, which gated fist
# arming off: swipe navigation went dead and, because the fist never armed,
# the cursor-freeze never ran either, so the stroke smeared the pointer
# hundreds of pixels across the screen. One frame was enough to do it.
def clench_then_swipe(lag, close_frames=5, hold=40):
    """Close into a fist with the index trailing, hold it, then swipe."""
    m = NullMouse()
    c = ctrl(m, sensitivity=9.0)
    t = engage(c)
    for i in range(1, close_frames + 1):
        t += DT
        c.update(clench(320, 240, i / close_frames, lag), FRAME, t)
    for _ in range(hold):
        t += DT
        info = c.update(clench(320, 240, 1.0, lag), FRAME, t)
    m.events.clear()
    for i in range(1, 9):
        t += DT
        c.update(clench(320 - 28 * i, 240, 1.0, lag), FRAME, t)
    nav = [e[0] for e in m.events if e[0] in ("back", "forward", "minimize")]
    moved = sum(abs(e[1]) for e in m.events if e[0] == "move")
    return info, nav, moved


for lag in (0.0, 0.25, 0.4, 0.5):
    info, nav, moved = clench_then_swipe(lag)
    check(f"a clench with the index {int(lag * 100)}% behind still arms",
          info["fist_armed"] and info["brake"] == 0.0,
          f"armed={info['fist_armed']} brake={info['brake']:.2f}")
    check(f"...and its swipe navigates instead of dragging (lag {lag})",
          nav and moved == 0, f"nav={nav} cursor moved {moved}px")

# a SLOW clench holds the brake pose long enough to genuinely engage it —
# bounded stickiness is what has to hand navigation back
info, nav, moved = clench_then_swipe(0.7, close_frames=30)
check("a slow clench releases the brake by itself",
      info["fist_armed"] and info["brake"] == 0.0,
      f"armed={info['fist_armed']} brake={info['brake']:.2f}")
check("...and navigation works again", nav and moved == 0,
      f"nav={nav} cursor moved {moved}px")

# the mechanism behind that: entry is debounced
d_deb = BrakeDetector(enter_frames=4)
d_deb.update(brake_hand(320, 240), pose_ok=True)
check("one frame of the pose cannot engage the brake", not d_deb.engaged,
      f"amount={d_deb.amount:.3f}")
for _ in range(6):
    d_deb.update(brake_hand(320, 240), pose_ok=True)
check("holding the pose does engage it", d_deb.engaged,
      f"amount={d_deb.amount:.2f}")

# stickiness is bounded when no button is held...
d_slip = BrakeDetector(stick_frames=8)
settle(d_slip, brake_hand(320, 240))
for _ in range(40):
    d_slip.update(brake_hand(320, 240), pose_ok=False, hold_ok=False)
check("a brake nobody is clicking with lets go once the pose is lost",
      d_slip.amount == 0.0, f"amount={d_slip.amount:.2f}")

# ...but unbounded while the click is actually held, so a long precision
# drag doesn't speed up halfway through
d_drag = BrakeDetector(stick_frames=8)
settle(d_drag, brake_hand(320, 240))
for _ in range(120):                      # 4 seconds of dragging
    d_drag.update(brake_pinch(320, 240), pose_ok=False, hold_ok=True)
check("a held drag keeps the brake for as long as it lasts",
      d_drag.amount > 0.9, f"amount={d_drag.amount:.2f}")

# ===================== hand-edited config can't invert the cursor ============
d_bad = BrakeDetector(min_scale=-2.0)
settle(d_bad, brake_hand(320, 240))
check("a negative min_scale cannot reverse the cursor", d_bad.scale > 0.0,
      f"scale={d_bad.scale:.2f}")
d_big = BrakeDetector(min_scale=5.0)
settle(d_big, brake_hand(320, 240))
check("an over-one min_scale cannot speed the cursor up", d_big.scale <= 1.0,
      f"scale={d_big.scale:.2f}")
d_inv = BrakeDetector(onset=0.9, full=0.2)      # thresholds inverted
settle(d_inv, brake_hand(320, 240))
check("inverted thresholds degrade to a step, not a crash",
      0.0 <= d_inv.amount <= 1.0 and 0.0 < d_inv.scale <= 1.0,
      f"amount={d_inv.amount:.2f} scale={d_inv.scale:.2f}")

# the live re-read feeds this straight from config.json every 30 frames, so
# it has to survive whatever is in the file
c_junk = ctrl(NullMouse())
for junk in (None, [], "brake", 42):
    c_junk.apply_brake_params(junk)
from gestures import BrakeDetector as _BD
check("a malformed brake block in config.json is ignored, not fatal",
      c_junk._brake.min_scale == _BD().min_scale,
      f"min_scale={c_junk._brake.min_scale}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

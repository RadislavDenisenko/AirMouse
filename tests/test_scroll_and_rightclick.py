"""Right-click (thumb+middle), fist drag, and peace-sign joystick scroll
(offset from the start point = constant scroll speed)."""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from gestures import (GestureController, TRACKING, PINCHED, RCLICK, ARMED,
                      SCROLL, IDLE, curl_ratio, is_scroll_pose)
from mouse_input import NullMouse
from test_gestures import synthetic_hand, hand, FRAME, DT

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def peace_hand(px, py):
    """Peace sign: index+middle extended, ring+pinky curled, thumb at rest."""
    pts = list(synthetic_hand(px, py))
    for b in (13, 17):                     # ring, pinky bases
        x = pts[b][0]
        pts[b + 1] = (x, py - 70)          # pip stays high
        pts[b + 2] = (x, py - 40)
        pts[b + 3] = (x, py + 10)          # tip curled to wrist
    return pts


def lerp_hand(a, b, t):
    return [(pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t)
            for pa, pb in zip(a, b)]


def ctrl_raw(mouse, **kw):
    kw.setdefault("engage_hold_s", 0.25)
    kw.setdefault("sensitivity", 1.0)
    kw.setdefault("dead_zone_px", 0.0)
    kw.setdefault("filter_min_cutoff", None)
    return GestureController(mouse, **kw)


def engage(ctrl, t, px=320, py=240):
    for _ in range(12):
        t += DT
        info = ctrl.update(synthetic_hand(px, py), FRAME, t)
    assert ctrl.state == TRACKING, ctrl.state
    return t


def moves(mouse):
    return [e for e in mouse.events if e[0] == "move"]


def wheels(mouse):
    return [e for e in mouse.events if e[0] == "wheel"]


# --- sanity: synthetic poses read as intended --------------------------------
check("peace pose detected", is_scroll_pose(peace_hand(320, 240)))
check("open hand is not peace pose", not is_scroll_pose(synthetic_hand(320, 240)))
check("fist curl low", curl_ratio(synthetic_hand(320, 240, open_palm=False)) < 1.05,
      f"curl={curl_ratio(synthetic_hand(320, 240, open_palm=False)):.2f}")
check("open curl high", curl_ratio(synthetic_hand(320, 240)) > 1.30,
      f"curl={curl_ratio(synthetic_hand(320, 240)):.2f}")

# --- right-click: quick middle+thumb pinch -----------------------------------
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 0.0)
for _ in range(4):
    t += DT
    info = c.update(hand(320, 240, 20, tip="middle"), FRAME, t)
state_mid = info["state"]
for _ in range(3):
    t += DT
    info = c.update(hand(320, 240, 55, tip="middle"), FRAME, t)
seq = [e[0] for e in m.events]
check("right click fires", seq == ["rdown", "rup"], f"seq={seq}")
check("R-CLICK display state", state_mid == RCLICK, f"state={state_mid}")
check("no left events on right pinch", "down" not in seq)

# --- left pinch never cross-fires right --------------------------------------
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 50.0)
for _ in range(5):
    t += DT
    c.update(hand(320, 240, 20), FRAME, t)
for _ in range(3):
    t += DT
    c.update(hand(320, 240, 55), FRAME, t)
seq = [e[0] for e in m.events]
check("left pinch = left only", seq == ["down", "up"], f"seq={seq}")

# --- clenching arms navigation and clicks NOTHING (v3.2) ---------------------
# A gradual clench passes through half-closed shapes; none of them may fake a
# pinch, and the finished fist must not press a button at all.
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 100.0)
open_h = synthetic_hand(320, 240)
fist_h = synthetic_hand(320, 240, open_palm=False)
for i in range(1, 5):                       # gradual 4-frame clench
    t += DT
    info = c.update(lerp_hand(open_h, fist_h, i / 4), FRAME, t)
for _ in range(2):
    t += DT
    info = c.update(fist_h, FRAME, t)
check("clench presses no mouse button", not m.events, f"events={m.events[:6]}")
check("ARMED display state", info["state"] == ARMED and info["fist_armed"],
      f"state={info['state']}")

# an armed fist moving slowly is not a drag: the cursor stays put
for i in range(1, 16):
    t += DT
    info = c.update(synthetic_hand(320 + 6 * i, 240, open_palm=False), FRAME, t)
check("armed fist does not drag the cursor", not moves(m),
      f"moves={moves(m)[:3]}")
check("still no button held", not info["pinch_down"] and not m.events)

# reopening disarms and hands control back to the pointer
for _ in range(3):
    t += DT
    info = c.update(synthetic_hand(410, 240), FRAME, t)
check("reopen disarms, back to TRACKING", info["state"] == TRACKING
      and not info["fist_armed"], f"state={info['state']}")

# --- motion model: self-scaling circle + edge-gradient sensitivity ----------
# synthetic hand size is 100 px -> radius = radius_scale * 100
m = NullMouse()
c = ctrl_raw(m, edge_multiplier=3.0, radius_scale=2.0)   # radius 200
t = engage(c, 200.0)
check("circle sized to the hand", abs(c.radius - 200.0) < 1e-6,
      f"radius={c.radius}")

# same 6px step near the center vs near the rim: rim step must emit more
t += DT
c.update(synthetic_hand(320, 240), FRAME, t)             # warm-up (sets prev)
m.events.clear()
t += DT
c.update(synthetic_hand(326, 240), FRAME, t)             # step at ~3% radius
center_dx = sum(e[1] for e in moves(m))
for i in range(1, 23):                                   # walk out to +160
    t += DT                                              # (slow: not a swipe)
    c.update(synthetic_hand(326 + 7 * i, 240), FRAME, t)
m.events.clear()
t += DT
c.update(synthetic_hand(486, 240), FRAME, t)             # 6px step at 82% radius
edge_dx = sum(e[1] for e in moves(m))
check("sensitivity ramps toward the edge", edge_dx >= center_dx + 5,
      f"center={center_dx} edge={edge_dx}")

# NO auto-drift: hand held still (even far out) must not move the cursor
m.events.clear()
info = None
for _ in range(30):
    t += DT
    info = c.update(synthetic_hand(486, 240), FRAME, t)
check("still hand = zero drift (glide removed)", not moves(m),
      f"moves={moves(m)[:3]}")
check("no gliding key in info", "gliding" not in info)

# trailing re-anchor: crossing the rim drags the circle along
a0 = info["anchor"]
for i in range(1, 6):                                    # push out past the rim
    t += DT
    info = c.update(synthetic_hand(486 + 30 * i, 240), FRAME, t)
a1 = info["anchor"]
check("circle follows hand past the edge", a1[0] > a0[0] + 100,
      f"anchor {a0[0]:.0f} -> {a1[0]:.0f}")
palm_dist = ((636 - a1[0]) ** 2 + (240 - a1[1]) ** 2) ** 0.5
check("hand pinned to the rim while trailing", abs(palm_dist - c.radius) < 1.0,
      f"dist={palm_dist:.1f} R={c.radius}")

# pull back inside: circle stays put now
for _ in range(5):
    t += DT
    info = c.update(synthetic_hand(560, 240), FRAME, t)
check("circle stays once back inside", info["anchor"] == a1,
      f"anchor={info['anchor']}")

# radius scales with hand size (2x hand -> 2x circle)
m2 = NullMouse()
c2 = ctrl_raw(m2, radius_scale=2.0)
t2 = 0.0
big = [(x * 2, y * 2) for (x, y) in synthetic_hand(200, 200)]
for _ in range(12):
    t2 += DT
    c2.update(big, FRAME, t2)
check("2x hand -> 2x radius", abs(c2.radius - 400.0) < 1e-6,
      f"radius={c2.radius}")

# --- scroll mode: joystick (offset from the start point = constant speed) ----
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 300.0)
for _ in range(3):                           # form peace sign -> origin locks
    t += DT
    info = c.update(peace_hand(320, 240), FRAME, t)
check("peace enters SCROLL", info["state"] == SCROLL
      and info["mode"] == SCROLL)
check("scroll origin locked at entry", info["scroll_origin"] is not None)

# inside the dead zone (0.3 hand-sizes = 30px): nothing scrolls
m.events.clear()
for _ in range(10):
    t += DT
    c.update(peace_hand(320, 255), FRAME, t)   # 15px below origin
check("dead zone: resting hand scrolls nothing", not wheels(m),
      f"wheels={wheels(m)[:3]}")

# hold 1 hand-size below the origin -> steady scroll down while stationary
m.events.clear()
for _ in range(30):                          # hold still for 1s
    t += DT
    c.update(peace_hand(320, 340), FRAME, t)
w = wheels(m)
near_sum = sum(e[1] for e in w)
check("held offset keeps scrolling (constant speed)", len(w) >= 10,
      f"n={len(w)}")
# default gain 30, offset 1 hand-size: 30 x (1.0-0.3)^1.5 = 17.6 notches/s
check("hand below origin scrolls down", -2400 <= near_sum <= -1800,
      f"sum={near_sum}")
check("cursor frozen in scroll", not moves(m))
check("no buttons in scroll", all(e[0] in ("wheel", "hwheel") for e in m.events))

# hold farther out -> faster constant scroll
m.events.clear()
for _ in range(30):                          # 2 hand-sizes below origin, 1s
    t += DT
    c.update(peace_hand(320, 440), FRAME, t)
far_sum = sum(e[1] for e in wheels(m))
check("farther from origin = faster scroll", far_sum <= 2.5 * near_sum,
      f"near={near_sum} far={far_sum}")

# come back to the middle -> scrolling stops (no momentum, no coasting)
m.events.clear()
for _ in range(15):
    t += DT
    c.update(peace_hand(320, 245), FRAME, t)   # back near the origin
late = [e for e in wheels(m)]
check("return to middle stops the scroll", not late, f"wheels={late[:3]}")

# hand above the origin scrolls the other way
m.events.clear()
for _ in range(15):
    t += DT
    c.update(peace_hand(320, 150), FRAME, t)   # 90px above origin
up_sum = sum(e[1] for e in wheels(m))
check("hand above origin scrolls up", up_sum > 0, f"sum={up_sum}")

# open palm = back to POINT after the deliberate-exit hold (~0.2 s of
# sustained not-peace — a couple of noisy frames must NOT exit, so a real
# exit intentionally takes a beat); wheel silent once committed
for _ in range(8):
    t += DT
    info = c.update(synthetic_hand(320, 150), FRAME, t)
m.events.clear()
for _ in range(3):
    t += DT
    info = c.update(synthetic_hand(320, 150), FRAME, t)
check("open palm exits scroll", info["mode"] == "POINT"
      and not wheels(m), f"mode={info['mode']} wheels={wheels(m)[:3]}")

# re-entering scroll re-locks a fresh origin at the new hand position
for _ in range(3):
    t += DT
    info = c.update(peace_hand(400, 300), FRAME, t)
so = info["scroll_origin"]
check("re-entry locks a new origin", so is not None
      and abs(so[0] - 400) < 30 and abs(so[1] - 300) < 30, f"origin={so}")

# --- pose noise: two misread frames must NOT move the origin (v3.3) ----------
# This was the field bug: is_scroll_pose flickering for 2 frames used to
# exit+re-enter scroll and silently re-lock the zero at the hand's position.
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 2000.0)
for _ in range(3):
    t += DT
    info = c.update(peace_hand(320, 240), FRAME, t)
o0 = info["scroll_origin"]
for _ in range(5):                       # scrolling down at 1 hand-size
    t += DT
    c.update(peace_hand(320, 340), FRAME, t)
m.events.clear()
for _ in range(2):                       # classifier misreads: "open hand"
    t += DT
    info = c.update(synthetic_hand(320, 340), FRAME, t)
for _ in range(5):                       # ...and recovers
    t += DT
    info = c.update(peace_hand(320, 340), FRAME, t)
check("2 misread pose frames stay in SCROLL", info["state"] == SCROLL,
      f"state={info['state']}")
check("2 misread pose frames do not move the origin",
      info["scroll_origin"] == o0, f"origin={info['scroll_origin']} vs {o0}")
check("scroll continues through the flicker",
      wheels(m) and all(e[1] < 0 for e in wheels(m)),
      f"wheels={wheels(m)[:4]}")

# --- recenter-on-reversal: lift after a deep scroll = up, immediately --------
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 2100.0)
for _ in range(3):
    t += DT
    c.update(peace_hand(320, 240), FRAME, t)          # zero at y=240
for _ in range(10):
    t += DT
    c.update(peace_hand(320, 440), FRAME, t)          # deep: 2 hand-sizes down
m.events.clear()
ys = [440 - 20 * i for i in range(1, 9)]              # deliberate lift, 600px/s
for y in ys:
    t += DT
    info = c.update(peace_hand(320, y), FRAME, t)
up_units = sum(e[1] for e in wheels(m) if e[1] > 0)
check("reversal scrolls UP without trekking back across the old zero",
      up_units > 0 and min(ys) > 270,
      f"up={up_units} min_y={min(ys)} origin={info['scroll_origin']}")

# slow drift back toward the zero must NOT recenter (that's "slowing down")
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 2200.0)
for _ in range(3):
    t += DT
    c.update(peace_hand(320, 240), FRAME, t)
for _ in range(3):
    t += DT
    info = c.update(peace_hand(320, 340), FRAME, t)
for i in range(1, 16):                                # 2px/frame = 60px/s drift
    t += DT
    info = c.update(peace_hand(320, 340 - 2 * i), FRAME, t)
check("slow drift toward the zero does not recenter",
      info["scroll_origin"] == (320.0, 240.0),
      f"origin={info['scroll_origin']}")

# --- frozen normaliser: hand-size wobble cannot change the speed -------------
def scaled_peace(px, py, s):
    return [(px + (x - px) * s, py + (y - py) * s) for (x, y) in peace_hand(px, py)]


m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 2300.0)
for _ in range(3):
    t += DT
    c.update(peace_hand(320, 240), FRAME, t)
for _ in range(3):
    t += DT
    c.update(peace_hand(320, 340), FRAME, t)
m.events.clear()
for _ in range(15):                                   # stable hand size
    t += DT
    c.update(peace_hand(320, 340), FRAME, t)
stable = sum(e[1] for e in wheels(m))
m.events.clear()
for i in range(15):                                   # size wobbles +-10%
    t += DT
    c.update(scaled_peace(320, 340, 0.9 if i % 2 else 1.1), FRAME, t)
wobble = sum(e[1] for e in wheels(m))
check("hand-size wobble does not change scroll speed",
      abs(stable - wobble) <= 6, f"stable={stable} wobble={wobble}")

# --- exiting scroll can never leave a stuck armed fist freezing the cursor ---
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 2400.0)
for _ in range(4):                                    # clench: arm the fist
    t += DT
    c.update(synthetic_hand(320, 240, open_palm=False), FRAME, t)
assert c._fist.is_down
for _ in range(4):                                    # peace: into scroll
    t += DT
    info = c.update(peace_hand(320, 240), FRAME, t)
assert info["state"] == SCROLL, info["state"]
for _ in range(10):                                   # open palm: exit scroll
    t += DT
    info = c.update(synthetic_hand(320, 240), FRAME, t)
m.events.clear()
for i in range(1, 10):
    t += DT
    info = c.update(synthetic_hand(320 + 5 * i, 240), FRAME, t)
check("scroll exit leaves no stuck armed fist", not info["fist_armed"]
      and moves(m), f"armed={info['fist_armed']} moves={moves(m)[:2]}")

# --- scroll gain is INDEPENDENT of cursor sensitivity ------------------------
# The whole point of the separate knob: tuning one must not move the other.
def scroll_run(gain, sensitivity=1.0, offset_px=100, frames=20, t0=0.0):
    """Engage, form the peace sign, hold `offset_px` below the origin.
    Returns (total wheel units, total cursor dx)."""
    mm = NullMouse()
    cc = ctrl_raw(mm, scroll_gain_notches_s=gain, sensitivity=sensitivity)
    tt = engage(cc, t0)
    for _ in range(3):
        tt += DT
        cc.update(peace_hand(320, 240), FRAME, tt)
    mm.events.clear()
    for _ in range(frames):
        tt += DT
        cc.update(peace_hand(320, 240 + offset_px), FRAME, tt)
    return (sum(e[1] for e in mm.events if e[0] == "wheel"),
            sum(e[1] for e in mm.events if e[0] == "move"))


w_low, _ = scroll_run(gain=10.0, t0=500.0)
w_high, _ = scroll_run(gain=20.0, t0=600.0)
check("doubling scroll gain doubles wheel output",
      abs(w_high - 2 * w_low) <= abs(w_low) * 0.02 + 130,
      f"gain10={w_low} gain20={w_high}")

# changing CURSOR sensitivity leaves scroll output identical
w_s1, _ = scroll_run(gain=10.0, sensitivity=1.0, t0=700.0)
w_s9, _ = scroll_run(gain=10.0, sensitivity=9.0, t0=800.0)
check("cursor sensitivity does not affect scroll", w_s1 == w_s9,
      f"sens1={w_s1} sens9={w_s9}")

# ...and scrolling never emits cursor motion at any gain
_, mv_low = scroll_run(gain=10.0, t0=900.0)
_, mv_high = scroll_run(gain=90.0, t0=1000.0)
check("scroll gain never moves the cursor", mv_low == 0 and mv_high == 0,
      f"moves={mv_low}/{mv_high}")

# the reverse: cursor motion is unaffected by the scroll gain
def cursor_dx(gain, t0):
    mm = NullMouse()
    cc = ctrl_raw(mm, scroll_gain_notches_s=gain, sensitivity=2.0)
    tt = engage(cc, t0)
    mm.events.clear()
    for i in range(1, 11):
        tt += DT
        cc.update(synthetic_hand(320 + 5 * i, 240), FRAME, tt)
    return sum(e[1] for e in mm.events if e[0] == "move")


check("scroll gain does not affect cursor motion",
      cursor_dx(10.0, 1100.0) == cursor_dx(90.0, 1200.0),
      f"dx={cursor_dx(10.0, 1300.0)} vs {cursor_dx(90.0, 1400.0)}")

# the shipped default is genuinely fast: 1 hand-size off center should move a
# real page (>= ~15 notches/s), not crawl like the old 10-gain setting
w_default, _ = scroll_run(gain=30.0, offset_px=100, frames=30, t0=1500.0)
notches_per_s = abs(w_default) / 120.0 / (30 * DT)
check("default gain scrolls a page-worth per second", notches_per_s >= 15.0,
      f"{notches_per_s:.1f} notches/s at 1 hand-size")

# --- scroll pose lost with hand -> clean idle, nothing stuck -----------------
m = NullMouse()
c = ctrl_raw(m)
t = engage(c, 400.0)
for _ in range(3):
    t += DT
    c.update(peace_hand(320, 240), FRAME, t)
for _ in range(5):
    t += DT
    c.update(peace_hand(320, 340), FRAME, t)   # actively scrolling
m.events.clear()
for _ in range(12):
    t += DT
    info = c.update(None, FRAME, t)
check("hand lost during scroll -> IDLE, wheel stops", info["state"] == IDLE
      and not wheels(m), f"events={m.events[:4]}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

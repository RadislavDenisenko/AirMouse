"""Tests: One-Euro filter behavior + dead zone (Stage 3)."""
import os
import random
import statistics
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from one_euro import OneEuro
from gestures import GestureController, TRACKING, IDLE
from mouse_input import NullMouse
from test_gestures import synthetic_hand, FRAME, DT

random.seed(42)
failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


# --- Filter test 1: jitter attenuation when still ---------------------------
f = OneEuro(min_cutoff=0.5, beta=0.007)   # controller default
raw, out = [], []
t = 0.0
for i in range(300):  # 10 s at 30 fps
    t += DT
    x = 100.0 + random.gauss(0, 2.0)   # 2 px sensor noise around a still hand
    raw.append(x)
    out.append(f.filter(x, t))
raw_sd = statistics.stdev(raw[60:])
out_sd = statistics.stdev(out[60:])
check("still-hand jitter crushed", out_sd < raw_sd / 4,
      f"raw sd={raw_sd:.2f}px -> filtered sd={out_sd:.2f}px")

# --- Filter test 2: low lag during fast movement ----------------------------
f = OneEuro(min_cutoff=0.5, beta=0.007)
t = 0.0
lag_px = 0.0
for i in range(90):  # 3 s moving at 600 px/s
    t += DT
    x = 600.0 * t
    y = f.filter(x, t)
    lag_px = x - y
check("fast-move lag small", lag_px < 25, f"lag at 600px/s = {lag_px:.1f}px")
check("lag under ~40ms", lag_px / 600.0 < 0.040,
      f"= {1000 * lag_px / 600.0:.0f}ms behind")

# --- Dead zone test: resting-hand noise emits nothing -----------------------
mouse = NullMouse()
ctrl = GestureController(mouse, sensitivity=5.0, dead_zone_px=5.0,
                         engage_hold_s=0.25)
t = 0.0
for i in range(12):  # engage
    t += DT
    ctrl.update(synthetic_hand(320, 240), FRAME, t)
assert ctrl.state == TRACKING
mouse.events.clear()
for i in range(150):  # 5 s of a "resting" jittery hand (±1.5 px)
    t += DT
    jx = 320 + random.gauss(0, 1.5)
    jy = 240 + random.gauss(0, 1.5)
    ctrl.update(synthetic_hand(jx, jy), FRAME, t)
check("dead zone: resting hand -> zero cursor drift", not mouse.events,
      f"events={len(mouse.events)}")

# --- Leaving dead zone flows smoothly, no jump ------------------------------
for i in range(40):
    t += DT
    ctrl.update(synthetic_hand(320 + 4 * i, 240), FRAME, t)
moves = [e for e in mouse.events if e[0] == "move"]
biggest = max((abs(e[1]) for e in moves), default=0)
total = sum(e[1] for e in moves)
check("movement flows after dead zone", total > 300, f"total dx={total}")
check("no jump at dead-zone exit", biggest <= 40, f"max single dx={biggest}")

# --- Filter resets across hand loss (no spike on reappear) ------------------
mouse.events.clear()
for i in range(20):
    t += DT
    ctrl.update(None, FRAME, t)
assert ctrl.state == IDLE
for i in range(12):  # re-engage far away
    t += DT
    ctrl.update(synthetic_hand(100, 400), FRAME, t)
check("clean re-engage far away, no events", ctrl.state == TRACKING
      and not mouse.events, f"events={mouse.events[:3]}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

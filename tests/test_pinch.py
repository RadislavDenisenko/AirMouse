"""Tests: pinch detection, hysteresis, debounce, click vs drag, freeze,
stuck-button guard (Stage 4)."""
import math
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from gestures import (GestureController, PinchDetector, pinch_ratio,
                      TRACKING, PINCHED, IDLE)
from mouse_input import NullMouse
from test_gestures import synthetic_hand, hand, FRAME, DT

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def scaled(pts, s):
    return [(x * s, y * s) for (x, y) in pts]


# --- ratio & scale invariance ------------------------------------------------
r1 = pinch_ratio(hand(320, 240, 20))
r2 = pinch_ratio(scaled(hand(320, 240, 20), 2.0))
check("ratio normalized by hand size", abs(r1 - 0.2) < 1e-6, f"r={r1:.3f}")
check("scale invariant", abs(r1 - r2) < 1e-9, f"1x={r1:.3f} 2x={r2:.3f}")

# --- PinchDetector: debounce + hysteresis -----------------------------------
det = PinchDetector(0.28, 0.38, 2)
det.update(hand(0, 0, 60))                       # arm (0.6 > 0.38)
e1 = det.update(hand(0, 0, 20))                  # 1st closed frame
e2 = det.update(hand(0, 0, 20))                  # 2nd -> commit
check("debounce: no edge on 1st frame", e1 is None)
check("down on 2nd consecutive frame", e2 == "down")

# oscillation inside hysteresis band (0.29-0.37): no release
edges = [det.update(hand(0, 0, d)) for d in (30, 36, 33, 37, 29, 35)]
check("hysteresis band holds pinch", all(e is None for e in edges)
      and det.is_down, f"edges={edges}")

# one noisy open frame then closed again: debounce swallows it
e1 = det.update(hand(0, 0, 50))
e2 = det.update(hand(0, 0, 20))
e3 = det.update(hand(0, 0, 20))
check("single noisy frame swallowed", e1 is None and e2 is None and e3 is None
      and det.is_down, f"{e1},{e2},{e3}")

# clean release for 2 frames
e1 = det.update(hand(0, 0, 50))
e2 = det.update(hand(0, 0, 50))
check("release after 2 open frames", e1 is None and e2 == "up"
      and not det.is_down)

# --- arming: hand appearing pinched can't ghost-click -----------------------
det2 = PinchDetector(0.28, 0.38, 2)
edges = [det2.update(hand(0, 0, 20)) for _ in range(10)]
check("unarmed pinch ignored", all(e is None for e in edges))
det2.update(hand(0, 0, 60))                      # opens once -> armed
det2.update(hand(0, 0, 20))
e = det2.update(hand(0, 0, 20))
check("arms after opening", e == "down")


# --- controller integration --------------------------------------------------
def engage(ctrl, t, px=320, py=240):
    """Engage with the flat spread palm (thumb splayed), like a real user."""
    for _ in range(12):
        t += DT
        ctrl.update(synthetic_hand(px, py), FRAME, t)
    assert ctrl.state == TRACKING, ctrl.state
    return t


# quick pinch-and-release = click (down then up, < 0.3 s)
mouse = NullMouse()
ctrl = GestureController(mouse, sensitivity=5.0, engage_hold_s=0.25)
t = engage(ctrl, 0.0)
t_down = None
for i in range(4):   # ~0.13 s pinched
    t += DT
    info = ctrl.update(hand(320, 240, 20), FRAME, t)
    if info["pinch_down"] and t_down is None:
        t_down = t
for i in range(3):
    t += DT
    info = ctrl.update(hand(320, 240, 55), FRAME, t)
seq = [e[0] for e in mouse.events]
check("quick pinch = click", seq == ["down", "up"], f"seq={seq}")
check("click duration < 0.3s", t - t_down < 0.3, f"{t - t_down:.2f}s")

# --- freeze on pinch: hand shove during first 150ms is swallowed -------------
mouse.events.clear()
for i in range(2):  # close pinch (debounce commits on 2nd frame)
    t += DT
    ctrl.update(hand(320, 240, 20), FRAME, t)
info = None
for i in range(4):  # 133ms: move hand hard while frozen
    t += DT
    info = ctrl.update(hand(320 + 15 * (i + 1), 240, 20), FRAME, t)
moves_during_freeze = [e for e in mouse.events if e[0] == "move"]
check("cursor frozen right after pinch", not moves_during_freeze,
      f"moves={moves_during_freeze[:3]}")
check("PINCHED display state", info["state"] == PINCHED)

# --- drag: after freeze, moving while pinched emits motion (no jump) --------
for i in range(15):
    t += DT
    ctrl.update(hand(380 + 6 * i, 240, 20), FRAME, t)
moves = [e for e in mouse.events if e[0] == "move"]
total_dx = sum(e[1] for e in moves)
biggest = max((abs(e[1]) for e in moves), default=0)
downs = [e for e in mouse.events if e[0] == "down"]
check("drag emits motion while held", total_dx > 200, f"dx={total_dx}")
check("no catch-up jump after freeze", biggest <= 60, f"max={biggest}")
check("still just one down (no re-click)", len(downs) == 1)

# release ends drag
for i in range(3):
    t += DT
    ctrl.update(hand(470, 240, 55), FRAME, t)
check("release ends drag", mouse.events[-1][0] == "up"
      and ctrl.state == TRACKING)

# --- stuck-button guard: hand lost mid-drag -> button released --------------
mouse2 = NullMouse()
ctrl2 = GestureController(mouse2, sensitivity=5.0, engage_hold_s=0.25)
t = engage(ctrl2, 100.0)
for i in range(3):
    t += DT
    ctrl2.update(hand(320, 240, 20), FRAME, t)
assert any(e[0] == "down" for e in mouse2.events), "no down registered"
for i in range(12):  # hand vanishes past grace
    t += DT
    ctrl2.update(None, FRAME, t)
check("hand lost mid-drag -> button released", ctrl2.state == IDLE
      and mouse2.events[-1][0] == "up", f"last={mouse2.events[-1]}")

# during grace (before IDLE), button must stay held (drag survives a blink)
mouse3 = NullMouse()
ctrl3 = GestureController(mouse3, sensitivity=5.0, engage_hold_s=0.25)
t = engage(ctrl3, 200.0)
for i in range(3):
    t += DT
    ctrl3.update(hand(320, 240, 20), FRAME, t)
mouse3.events.clear()
for i in range(2):   # 66ms gap, inside 250ms grace
    t += DT
    ctrl3.update(None, FRAME, t)
for i in range(2):   # hand back, still pinched
    t += DT
    info = ctrl3.update(hand(322, 240, 20), FRAME, t)
check("drag survives 2-frame dropout", not mouse3.events
      and info["state"] == PINCHED, f"events={mouse3.events}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

"""Tests: cursor magnetism. The motion maths is pure, so all of the feel
rules are verified headlessly; the Win32 target hunt gets a live shape check
at the end."""
import math
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, _APP_DIR)

from magnet import (MIN_SCALE, MagnetMouse, apply_magnet, caption_button_at,
                    dist_to_rect, slowdown_factor)
from mouse_input import NullMouse

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


RECT = (100, 100, 140, 120)          # a 40x20 target, centre (120, 110)
CENTRE = (120, 110)


# ============================== distance =====================================
check("inside the rect is distance 0", dist_to_rect(120, 110, RECT) == 0)
check("straight out to the side measures the gap",
      dist_to_rect(160, 110, RECT) == 20)
check("diagonal corner distance is euclidean",
      abs(dist_to_rect(143, 124, RECT) - 5.0) < 1e-6,
      dist_to_rect(143, 124, RECT))

# ============================ no target / off ================================
check("no target leaves motion untouched",
      apply_magnet(7, -3, (0, 0), None, 100, 40) == (7.0, -3.0))
check("strength 0 leaves motion untouched",
      apply_magnet(7, -3, (130, 110), RECT, 0, 40) == (7.0, -3.0))
check("beyond reach leaves motion untouched",
      apply_magnet(7, -3, (400, 400), RECT, 100, 40) == (7.0, -3.0))

# THE invariant the whole app keeps: a still hand never moves the cursor,
# so magnetism must never generate motion on its own.
check("a still hand is never pulled", apply_magnet(0, 0, (125, 112), RECT,
                                                   100, 40) == (0.0, 0.0))

# ============================== slowdown =====================================
far = apply_magnet(10, 0, (175, 110), RECT, 80, 40)     # 35px out, in reach
near = apply_magnet(10, 0, (145, 110), RECT, 80, 40)    # 5px out
inside = apply_magnet(10, 0, (120, 110), RECT, 80, 40)  # dead centre
check("motion slows more the closer you get",
      abs(far[0]) > abs(near[0]) > abs(inside[0]),
      f"far={far[0]:.2f} near={near[0]:.2f} inside={inside[0]:.2f}")
check("stronger setting slows more",
      abs(apply_magnet(10, 0, CENTRE, RECT, 100, 40)[0])
      < abs(apply_magnet(10, 0, CENTRE, RECT, 40, 40)[0]))
check("even at full strength the cursor still creeps",
      abs(apply_magnet(10, 0, CENTRE, RECT, 100, 40)[0]) >= 10 * MIN_SCALE
      - 1e-9,
      f"got={apply_magnet(10, 0, CENTRE, RECT, 100, 40)[0]:.3f}")

# ================================ pull =======================================
# approaching from the right, moving left: should also drift toward centre y
ox, oy = apply_magnet(-10, 0, (150, 130), RECT, 80, 40, pull=0.5)
check("approaching motion is drawn toward the centre", oy < 0,
      f"dy={oy:.3f} (target centre is above)")
check("pull never reverses your direction", ox < 0, f"dx={ox:.3f}")
check("pull=0 gives slowdown only",
      abs(apply_magnet(-10, 0, (150, 130), RECT, 80, 40, pull=0.0)[1]) < 1e-9)

# the pull may never overshoot the centre
ox2, oy2 = apply_magnet(-1, 0, (121, 110), RECT, 100, 40, pull=5.0)
check("pull is capped at the distance to the centre", abs(ox2) <= 2.0,
      f"dx={ox2:.3f}")

# =============================== leaving =====================================
leave = apply_magnet(10, 0, (130, 110), RECT, 90, 40)   # inside, heading out
enter = apply_magnet(-10, 0, (130, 110), RECT, 90, 40)  # inside, heading in
check("leaving is easier than arriving", abs(leave[0]) > abs(enter[0]),
      f"leaving={leave[0]:.2f} arriving={enter[0]:.2f}")

# push-through: sustained motion in one direction always escapes
x, y = 100.0, 110.0
for _ in range(200):
    dx, dy = apply_magnet(4, 0, (x, y), RECT, 100, 40)
    x += dx
    y += dy
check("sustained motion always escapes the target", x > RECT[2] + 40,
      f"ended at x={x:.1f}")

# and it escapes in a sane number of frames, not hundreds
x, frames = 100.0, 0
while x <= RECT[2] + 40 and frames < 200:
    x += apply_magnet(4, 0, (x, 110), RECT, 100, 40)[0]
    frames += 1
check("escaping takes a reasonable number of frames", frames <= 60,
      f"frames={frames}")

# ============================ debug readout ==================================
check("slowdown factor is 1.0 with no target",
      slowdown_factor((0, 0), None, 100, 40) == 1.0)
check("slowdown factor drops toward the centre",
      slowdown_factor(CENTRE, RECT, 100, 40)
      < slowdown_factor((170, 110), RECT, 100, 40) <= 1.0)

# ============================== MagnetMouse ==================================
class FakeFinder:
    def __init__(self, target):
        self.target = target

    def current(self, max_age=0.5):
        return self.target


rec = NullMouse()
mm = MagnetMouse(rec, FakeFinder(None), strength=100, reach_px=40)
mm.move(9, 4)
check("no target: the wrapper passes motion straight through",
      rec.events == [("move", 9, 4)], f"events={rec.events}")

rec2 = NullMouse()
mm2 = MagnetMouse(rec2, FakeFinder((RECT, "close button")), strength=100,
                  reach_px=40)
mm2.enabled = False
mm2.move(9, 4)
check("disabled is a straight pass-through",
      rec2.events == [("move", 9, 4)], f"events={rec2.events}")

# a slow drag must still move: sub-pixel remainders are carried, never lost
rec3 = NullMouse()
mm3 = MagnetMouse(rec3, FakeFinder((RECT, "button")), strength=100,
                  reach_px=40)
for _ in range(40):
    mm3.move(1, 0)
moved = sum(e[1] for e in rec3.events if e[0] == "move")
check("a slow drag near a target still moves the cursor", moved != 0,
      f"total dx={moved}")

# dragging (button held) suspends magnetism entirely
rec4 = NullMouse()
mm4 = MagnetMouse(rec4, FakeFinder((RECT, "close button")), strength=100,
                  reach_px=40)
mm4.left_down()
rec4.events.clear()
mm4.move(9, 4)
check("magnetism is suspended while dragging",
      rec4.events == [("move", 9, 4)], f"events={rec4.events}")
mm4.left_up()
check("drag state clears on release", mm4._dragging is False)

# non-motion calls are delegated untouched
rec5 = NullMouse()
mm5 = MagnetMouse(rec5, FakeFinder((RECT, "button")))
mm5.wheel(120)
mm5.right_down()
mm5.right_up()
mm5.volume(2)
mm5.minimize_window()
check("everything except movement is delegated unchanged",
      rec5.events == [("wheel", 120), ("rdown",), ("rup",), ("volume", 2),
                      ("minimize",)], f"events={rec5.events}")

# ========================= live target hunt (shape only) =====================
# Runs against this machine: assert it never raises and returns a sane shape.
try:
    hit = caption_button_at(5, 5)          # a corner, almost certainly empty
    ok_shape = hit is None or (len(hit) == 2 and len(hit[0]) == 4)
    check("caption probe returns a sane shape and never raises", ok_shape,
          f"hit={hit}")
except Exception as e:
    check("caption probe returns a sane shape and never raises", False,
          f"raised {e!r}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

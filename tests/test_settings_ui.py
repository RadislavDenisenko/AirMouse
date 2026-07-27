"""Tests: settings UI kit pure logic — highlight markup, wrap layout, font
resolution, color blending, easings and the magnet geometry. All headless
(no Tk window), so they run in the normal suite."""
import math
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))

from settings_ui import (ACCENTS, DEFAULT_ACCENT, DISPLAY_STACK, BODY_STACK,
                         _rgb, ease_in, ease_out, ease_out_back, hex_lerp,
                         layout_runs, magnet_path, parse_markup,
                         resolve_family, round_pts)

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


# ============================ highlight markup ===============================
runs = parse_markup("Buttons *grab your cursor* — like a TV remote.")
check("markup splits into body/chip runs",
      runs == [("Buttons ", False), ("grab your cursor", True),
               (" — like a TV remote.", False)], f"runs={runs}")

check("plain text is one body run",
      parse_markup("no markup here") == [("no markup here", False)])

check("two chips in one caption",
      [r[1] for r in parse_markup("*a* mid *b*")] == [True, False, True])

# an unclosed marker must degrade to text, never raise
check("unclosed marker does not raise",
      parse_markup("half *open") == [("half ", False), ("open", True)])

check("empty string yields no runs", parse_markup("") == [])

# ============================== wrap layout ==================================
# fake metrics: every char 6px wide, space 4px, line 16px
def measure(word, is_chip):
    return len(word) * (7 if is_chip else 6)


# 4 words of 18px + 3 spaces of 4px = 84px: fits one line at 100px wide...
lines, h = layout_runs(parse_markup("aaa bbb ccc ddd"), 100, measure, 4, 16)
check("no premature wrap when the text fits", len(lines) == 1 and h == 16,
      f"lines={len(lines)} h={h}")
# ...and wraps to two lines once the box is too narrow for all of it
lines, h = layout_runs(parse_markup("aaa bbb ccc ddd"), 60, measure, 4, 16)
check("wraps at the given width", len(lines) == 2 and h == 32,
      f"lines={len(lines)} h={h}")
check("first word of every line sits at x=0",
      all(line[0][0] == 0 for line in lines))
check("no line exceeds the width",
      all(line[-1][0] + line[-1][3] <= 60 for line in lines),
      f"ends={[l[-1][0] + l[-1][3] for l in lines]}")

# chip flags survive tokenisation, and chip words measure with the chip font
lines2, _ = layout_runs(parse_markup("hi *big chip* bye"), 400, measure, 4, 16)
flat = [(w, c) for line in lines2 for (_x, w, c, _ww) in line]
check("chip words keep their flag",
      flat == [("hi", False), ("big", True), ("chip", True), ("bye", False)],
      f"flat={flat}")
widths = {w: ww for line in lines2 for (_x, w, _c, ww) in line}
check("chip words use the chip metric",
      widths["big"] == 21 and widths["hi"] == 12,
      f"big={widths['big']} hi={widths['hi']}")

# a chip long enough to wrap splits across lines without losing words
lines3, _ = layout_runs(parse_markup("*one two three four five*"), 60,
                        measure, 4, 16)
words3 = [w for line in lines3 for (_x, w, _c, _ww) in line]
check("a long chip wraps and keeps every word",
      words3 == ["one", "two", "three", "four", "five"] and len(lines3) > 1,
      f"lines={len(lines3)} words={words3}")
check("every wrapped chip word stays a chip",
      all(c for line in lines3 for (_x, _w, c, _ww) in line))

# a single word wider than the box still gets placed (no infinite loop)
lines4, _ = layout_runs(parse_markup("enormousunbreakableword"), 20,
                        measure, 4, 16)
check("over-long word is placed, not dropped", len(lines4) == 1)

# ============================ font resolution ================================
fams = frozenset({"Candara", "Bahnschrift SemiBold", "Segoe UI"})
check("display stack prefers Bahnschrift SemiBold",
      resolve_family(DISPLAY_STACK, fams) == "Bahnschrift SemiBold")
check("body stack prefers Candara",
      resolve_family(BODY_STACK, fams) == "Candara")
check("falls through to the next available family",
      resolve_family(("Nope Sans", "Candara"), fams) == "Candara")
check("no match -> Tk default (never crashes)",
      resolve_family(("Nope", "Nada"), frozenset()) == "TkDefaultFont")
# the type system must never fall back to a generic system UI face
check("stacks contain no generic system UI fonts",
      not ({f.lower() for f in DISPLAY_STACK + BODY_STACK}
           & {"segoe ui", "arial", "calibri", "tahoma", "times new roman",
              "verdana", "comic sans ms"}))

# ================================ color ======================================
check("lerp endpoints exact", hex_lerp("#000000", "#ffffff", 0.0) == "#000000"
      and hex_lerp("#000000", "#ffffff", 1.0) == "#ffffff")
check("lerp midpoint blends", hex_lerp("#000000", "#ffffff", 0.5) == "#808080",
      hex_lerp("#000000", "#ffffff", 0.5))
check("lerp clamps out-of-range t",
      hex_lerp("#204060", "#ffffff", -3) == "#204060"
      and hex_lerp("#204060", "#ffffff", 9) == "#ffffff")
check("_rgb parses hex to 0-255 triples (PIL glow compositing)",
      _rgb("#c25e4a") == (194, 94, 74) and _rgb("#000000") == (0, 0, 0))

# brick is the shipped default accent
check("default accent is the middle swatch",
      DEFAULT_ACCENT == 1 and ACCENTS[DEFAULT_ACCENT][0] == "brick",
      f"default={ACCENTS[DEFAULT_ACCENT][0]}")
check("three accents offered for comparison", len(ACCENTS) == 3)

# ================================ easing =====================================
for name, fn in (("ease_out", ease_out), ("ease_in", ease_in)):
    check(f"{name} spans 0..1 exactly", abs(fn(0.0)) < 1e-9
          and abs(fn(1.0) - 1.0) < 1e-9)
check("ease_out decelerates (front-loaded)", ease_out(0.5) > 0.5)
check("ease_in accelerates (back-loaded)", ease_in(0.5) < 0.5)
check("ease_out_back overshoots then lands",
      max(ease_out_back(i / 100) for i in range(101)) > 1.0
      and abs(ease_out_back(1.0) - 1.0) < 1e-9)
check("a bigger back constant overshoots harder (the fling)",
      max(ease_out_back(i / 100, 2.0) for i in range(101))
      > max(ease_out_back(i / 100, 1.2) for i in range(101)))

# ============================== geometry =====================================
pts = round_pts(0, 0, 100, 40, 10)
check("round_pts returns 12 xy pairs", len(pts) == 24)
check("round_pts stays inside its box",
      min(pts[0::2]) >= 0 and max(pts[0::2]) <= 100
      and min(pts[1::2]) >= 0 and max(pts[1::2]) <= 40)
check("radius is clamped to half the shortest side",
      round_pts(0, 0, 10, 10, 999) == round_pts(0, 0, 10, 10, 5))

path, north, south = magnet_path(50, 20, 0.0)
check("magnet path has the full U outline", len(path) >= 20)
check("magnet poles point left (they attach to the switch's right edge)",
      north[2] < north[0] and south[2] < south[0],
      f"n={north} s={south}")
check("north pole sits above south", north[1] < south[1])
# rotating the magnet must move the poles but keep it the same size
p0, n0, _ = magnet_path(50, 20, 0.0)
p1, n1, _ = magnet_path(50, 20, math.pi / 2)
span = lambda p: (max(p[0::2]) - min(p[0::2]), max(p[1::2]) - min(p[1::2]))
w0, h0 = span(p0)
w1, h1 = span(p1)
check("rotation swaps the bounding box, preserving size",
      abs(w0 - h1) < 1e-6 and abs(h0 - w1) < 1e-6,
      f"{w0:.1f}x{h0:.1f} -> {w1:.1f}x{h1:.1f}")
check("rotation actually moves the pole", abs(n0[1] - n1[1]) > 1.0)

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

"""The first-run walkthrough — its step data and the contract it relies on.

The window itself needs a camera and a screen, so none of that is exercised
here. What is worth testing is the part that silently rots: the walkthrough
detects gestures by reading a real GestureController's info dict, so if a
key is ever renamed the steps would stop passing with no error anywhere.
These checks fail loudly instead.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

import airmouse
import settings_store as store
import tutorial
from config_defaults import DEFAULT_CONFIG
from gestures import GestureController
from mouse_input import NullMouse
from test_gestures import FRAME, DT, brake_hand, synthetic_hand

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


# ================================ step data =================================
check("there are steps to show", len(tutorial.STEPS) >= 5,
      f"{len(tutorial.STEPS)} steps")

REQUIRED = ("key", "big", "sub", "pose", "lit", "stage", "done")
missing = [(s.get("key", "?"), f) for s in tutorial.STEPS
           for f in REQUIRED if f not in s]
check("every step is fully filled in", not missing, f"missing={missing}")

keys = [s["key"] for s in tutorial.STEPS]
check("step keys are unique", len(keys) == len(set(keys)), f"keys={keys}")

bad_pose = [s["key"] for s in tutorial.STEPS if s["pose"] not in tutorial.POSES]
check("every step points at a pose the diagram can draw", not bad_pose,
      f"unknown={bad_pose}")

bad_lit = [(s["key"], f) for s in tutorial.STEPS for f in s["lit"]
           if f not in tutorial.FINGERS + ("thumb",)]
check("every highlighted finger is a real finger", not bad_lit,
      f"unknown={bad_lit}")

# the drawn hand interpolates these, so anything outside 0..1 would render
# a finger poking out through the back of the palm
out_of_range = [(name, e) for name, p in tutorial.POSES.items()
                for e in p["ext"] if not 0.0 <= e <= 1.0]
check("pose extensions stay within 0..1", not out_of_range,
      f"bad={out_of_range}")

bad_thumb = [(n, p.get("thumb")) for n, p in tutorial.POSES.items()
             if p.get("thumb", "out") not in ("out", "in") + tutorial.FINGERS]
check("every pose reaches for a thumb target that exists", not bad_thumb,
      f"bad={bad_thumb}")

# the engage step has to come first — nothing else works before it
check("the walkthrough starts by engaging", tutorial.STEPS[0]["key"] == "engage")

# Every step drives a little demo picture, and each one must be a mode the
# stage can actually draw — a typo would silently show a blank panel.
STAGE_MODES = {n[len("_draw_"):] for n in dir(tutorial.DemoStage)
               if n.startswith("_draw_")} | {"none"}
bad_stage = [(s["key"], s.get("stage")) for s in tutorial.STEPS
             if s.get("stage", "none") not in STAGE_MODES]
check("every step's demo picture is one the stage can draw", not bad_stage,
      f"unknown={bad_stage}  known={sorted(STAGE_MODES)}")

check("the gesture steps all say what they are demonstrating",
      all(s.get("stage_label") for s in tutorial.STEPS
          if s.get("stage", "none") != "none"),
      f"unlabelled={[s['key'] for s in tutorial.STEPS if s.get('stage','none') != 'none' and not s.get('stage_label')]}")

# the pinch bubble is drawn for exactly the pinch steps, plus a thumb-index
# fallback so every other step still shows the pinch that clicks Next
pinch_steps = {s["key"] for s in tutorial.STEPS
               if s["pose"] in ("index", "middle", "ring")}
check("a bubble is drawn for every pinch step",
      pinch_steps <= set(tutorial.PINCH_TIPS),
      f"bubbles={sorted(tutorial.PINCH_TIPS)} pinches={sorted(pinch_steps)}")
check("...and there is a thumb-index fallback for clicking Next",
      tutorial.PINCH_TIPS.get("next") == (tutorial.THUMB_TIP,
                                          tutorial.INDEX_TIP))

# THE INSTRUCTION IS THE THING PEOPLE READ. Long copy under the panels went
# unread in real use — eyes stay on the moving hand — so the headline has to
# stay short enough to take in at a glance.
long_big = [(s["key"], len(s["big"])) for s in tutorial.STEPS
            if len(s["big"]) > 32]
check("every headline is short enough to read at a glance", not long_big,
      f"too long={long_big}")
# Headlines are sentence case, not shouting: at 32pt bold they already
# dominate the screen, and full caps at that size reads as an error message.
# They ARE hard-wrapped, so the break lands where it was designed to.
unwrapped = [s["key"] for s in tutorial.STEPS if "\n" not in s["big"]]
check("every headline controls its own line break", not unwrapped,
      f"unwrapped={unwrapped}")
long_line = [(s["key"], len(ln)) for s in tutorial.STEPS
             for ln in s["big"].split("\n") if len(ln) > 22]
check("no headline line overruns the column", not long_line,
      f"too long={long_line}")
long_sub = [(s["key"], len(ln)) for s in tutorial.STEPS
            for ln in s["sub"].split("\n") if len(ln) > 52]
check("every subtitle line fits the column", not long_sub,
      f"too long={long_sub}")

# ============ NOTHING ADVANCES ON ITS OWN — the reported complaint ==========
# Performing a gesture used to jump straight to the next step, leaving no
# time to notice what had happened ("I changed the volume and it had already
# moved on"). The frame loop may now only MARK a step satisfied; moving on
# belongs to the Next button alone. Checking the names the frame loop can
# reach catches a re-introduction of the old behaviour.
tick_calls = set(tutorial.Tutorial._tick.__code__.co_names)
check("the frame loop cannot advance the step by itself",
      "_next" not in tick_calls,
      f"_tick reaches: {sorted(n for n in tick_calls if n.startswith('_'))}")
check("...it only marks the step satisfied", "satisfied" in tick_calls)
check("advancing lives on the Next button",
      "_next" in set(tutorial.Tutorial._click_at.__code__.co_names))

# Two steps DO advance on a timer, and only because being stuck on them is
# worse: step one cannot be left without knowing how to click, and the
# back/forward pair is one gesture taught in two halves. Everything else
# must still wait for the button.
auto = [s["key"] for s in tutorial.STEPS if s.get("auto_after")]
check("only the steps that must, advance on their own",
      auto == ["engage", "swipe_back"], f"auto={auto}")
check("the click step never auto-advances — it is how you learn to press Next",
      not next(s for s in tutorial.STEPS if s["key"] == "click").get("auto_after"))
check("auto-advance is gated on the step opting in",
      "auto_after" in set(tutorial.Tutorial._maybe_auto.__code__.co_consts
                          + tuple(tutorial.Tutorial._maybe_auto.__code__.co_names)))

# THE WALKTHROUGH MUST RUN THE APP'S OWN SETTINGS. Building the controller
# bare gave it the constructor defaults — a cursor at a third of the real
# speed, the old 1.2s high-five engage, a longer swipe — so it taught
# gestures that felt nothing like the app, and every tuning change silently
# missed it. It has to go through make_controller(cfg) like the tracker does.
init_names = set(tutorial.Tutorial.__init__.__code__.co_names)
check("the walkthrough builds its controller from config",
      "make_controller" in init_names,
      f"__init__ reaches: {sorted(n for n in init_names if 'controller' in n.lower() or 'Gesture' in n)}")

# the settings that must agree, and what they'd silently be if it regressed
bare = GestureController(NullMouse())
live = airmouse.make_controller(NullMouse(), DEFAULT_CONFIG)
drift = [(f, getattr(bare, f), getattr(live, f))
         for f in ("sensitivity", "engage_hold_s", "engage_spread_ratio")
         if getattr(bare, f) != getattr(live, f)]
check("...because the bare defaults genuinely disagree with the shipped ones",
      bool(drift), f"drift={drift}")

# going BACK is allowed, going forward is not
click_src = set(tutorial.Tutorial._click_at.__code__.co_names)
check("you can click a finished step to revisit it",
      "_rail_hit" in click_src and "_show_step" in click_src)

# ===================== the fake cursor and its buttons =======================
check("Next sits inside the window",
      0 < tutorial.NEXT_BTN[0] and
      tutorial.NEXT_BTN[0] + tutorial.NEXT_BTN[2] <= tutorial.W and
      tutorial.NEXT_BTN[1] + tutorial.NEXT_BTN[3] <= tutorial.H)
check("Next is big enough to hit with a gesture-driven pointer",
      tutorial.NEXT_BTN[2] >= 150 and tutorial.NEXT_BTN[3] >= 44,
      f"size={tutorial.NEXT_BTN[2]}x{tutorial.NEXT_BTN[3]}")
check("Next is in the bottom-right corner",
      tutorial.NEXT_BTN[0] > tutorial.W * 0.6
      and tutorial.NEXT_BTN[1] > tutorial.H * 0.8)

nx, ny, nw, nh = tutorial.NEXT_BTN
sx, sy, sw, sh = tutorial.SKIP_BTN
check("Next and Skip do not overlap", nx >= sx + sw or sx >= nx + nw,
      f"next={tutorial.NEXT_BTN} skip={tutorial.SKIP_BTN}")
check("a point inside Next hits it",
      tutorial._inside(tutorial.NEXT_BTN, nx + nw / 2, ny + nh / 2))
check("a point outside Next misses it",
      not tutorial._inside(tutorial.NEXT_BTN, nx - 30, ny + nh / 2))
check("the panels do not sit on top of the buttons",
      tutorial.STAGE[1] + tutorial.STAGE[3] <= ny,
      f"stage ends {tutorial.STAGE[1] + tutorial.STAGE[3]}, next starts {ny}")
check("the hand and camera panels do not overlap",
      tutorial.DIAG[0] + tutorial.DIAG[2] <= tutorial.CAM[0],
      f"diagram={tutorial.DIAG} camera={tutorial.CAM}")

# ============================ the done-predicates ============================
FULL = {"engaged": True, "clicks": 1, "rclicks": 1, "volume": 2,
        "brake": 0.9, "armed": True, "scrolling": True, "wheel": 3, "nav": 1,
        "back": 1, "forward": 1, "minimised": 1}
EMPTY = {"engaged": False, "clicks": 0, "rclicks": 0, "volume": 0,
         "brake": 0.0, "armed": False, "scrolling": False, "wheel": 0,
         "nav": 0, "back": 0, "forward": 0, "minimised": 0}

check("every step passes when its gesture is performed",
      all(s["done"](FULL) for s in tutorial.STEPS),
      f"stuck={[s['key'] for s in tutorial.STEPS if not s['done'](FULL)]}")
check("no step passes on an idle hand",
      not any(s["done"](EMPTY) for s in tutorial.STEPS),
      f"fired={[s['key'] for s in tutorial.STEPS if s['done'](EMPTY)]}")

# each step must key off its OWN gesture: flipping one gesture's flags
# should satisfy exactly one step, or the walkthrough would skip ahead on
# the wrong pose. Scroll takes TWO flags by design — the pose AND a wheel
# notch — because holding a peace sign dead still enters scroll mode in
# 0.06s, and "you are scrolling" must mean the page actually moved.
for flags, expect in ((("clicks",), "click"), (("rclicks",), "rclick"),
                      (("volume",), "volume"), (("brake",), "brake"),
                      (("back",), "swipe_back"), (("forward",), "swipe_fwd"),
                      (("minimised",), "minimize"),
                      (("scrolling", "wheel"), "scroll")):
    state = dict(EMPTY)
    for flag in flags:
        state[flag] = FULL[flag]
    fired = [s["key"] for s in tutorial.STEPS if s["done"](state)]
    check(f"only the {expect} step reacts to {'+'.join(flags)}",
          fired == [expect], f"fired={fired}")

# A MODERATE squeeze satisfies the brake step. The demo bar visibly slows
# well before a deep curl, so the step must acknowledge the squeeze around
# the depth the screen first rewards — demanding brake > 0.5 made a correct
# medium squeeze watch the bar move while "Done" never came.
state = dict(EMPTY)
state["brake"] = 0.3
fired = [s["key"] for s in tutorial.STEPS if s["done"](state)]
check("a moderate squeeze is enough for the brake step", fired == ["brake"],
      f"fired={fired}")

# the scroll pose held motionless satisfies NOTHING — that is the point
state = dict(EMPTY)
state["scrolling"] = True
check("the scroll pose alone, hand still, satisfies no step",
      not any(s["done"](state) for s in tutorial.STEPS),
      f"fired={[s['key'] for s in tutorial.STEPS if s['done'](state)]}")

# ===================== the contract with GestureController ===================
# The walkthrough reads these straight off a live info dict. A rename in
# gestures.py would otherwise leave every step silently unpassable.
ctrl = GestureController(NullMouse(), dead_zone_px=0.0,
                         filter_min_cutoff=None, engage_hold_s=0.25)
t = 0.0
for _ in range(12):
    t += DT
    info = ctrl.update(synthetic_hand(320, 240), FRAME, t)

for key in ("state", "mode", "brake", "fist_armed"):
    check(f"the controller still reports '{key}'", key in info,
          f"keys={sorted(info)[:6]}...")

# and the events the walkthrough counts are the ones NullMouse records
m = NullMouse()
c2 = GestureController(m, dead_zone_px=0.0, filter_min_cutoff=None,
                       engage_hold_s=0.25)
t = 0.0
for _ in range(12):
    t += DT
    c2.update(synthetic_hand(320, 240), FRAME, t)
for _ in range(20):
    t += DT
    c2.update(brake_hand(320, 240), FRAME, t)
check("a squeeze moves the same 'brake' value the walkthrough watches",
      c2.update(brake_hand(320, 240), FRAME, t + DT)["brake"] > 0.5)

# ======================= the settings switch that replays it =================
cfg = dict(DEFAULT_CONFIG)
check("tutorial_done ships False so a first run shows it",
      DEFAULT_CONFIG.get("tutorial_done") is False)

cfg["tutorial_done"] = False
check("the switch reads ON while the walkthrough is still pending",
      store.read_derived(cfg, "_show_tutorial") is True)
cfg["tutorial_done"] = True
check("...and OFF once it has been completed",
      store.read_derived(cfg, "_show_tutorial") is False)
check("turning it on queues the walkthrough again",
      store.write_derived("_show_tutorial", True) == {"tutorial_done": False})
check("turning it off marks it done",
      store.write_derived("_show_tutorial", False) == {"tutorial_done": True})

# ===================== the bubble drawn on the camera picture ================
# It runs on every frame of a pinch step, so it must cope with whatever the
# tracker hands it — including a hand that has left the frame.
import numpy as np


class _Fake:
    """Just enough of a Tutorial for the drawing method to run."""
    def __init__(self, i):
        self.i = i


bubble = tutorial.Tutorial._pinch_bubble
frame = np.zeros((480, 640, 3), dtype=np.uint8)
pinch_idx = [n for n, s in enumerate(tutorial.STEPS)
             if s["key"] in tutorial.PINCH_TIPS]

ok = True
for idx in pinch_idx:
    for pts in (synthetic_hand(320, 240), brake_hand(320, 240)):
        for info in ({"left_owner": "pinch"}, {"right_down": True},
                     {"volume_down": True}, {}):
            try:
                bubble(_Fake(idx), frame.copy(), pts, info)
            except Exception as e:
                print(f"   raised on step {idx}: {e!r}")
                ok = False
check("the pinch bubble draws for every pinch step and state", ok)

no_raise = True
for idx in range(len(tutorial.STEPS)):
    try:
        bubble(_Fake(idx), frame.copy(), None, {})       # hand left the frame
    except Exception as e:
        print(f"   raised with no hand on step {idx}: {e!r}")
        no_raise = False
check("...and does nothing when there is no hand to draw on", no_raise)

# a hand squashed to a single point must not divide by zero
degenerate = [(100, 100)] * 21
try:
    bubble(_Fake(pinch_idx[0]), frame.copy(), degenerate, {})
    check("a degenerate hand cannot divide by zero", True)
except Exception as e:
    check("a degenerate hand cannot divide by zero", False, f"raised {e!r}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

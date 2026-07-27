"""Tests: settings data layer — dotted paths, atomic config writes that
preserve unknown keys, the control manifest, and the derived controls."""
import json
import os
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, _APP_DIR)

from config_defaults import DEFAULT_CONFIG
from settings_store import (SECTIONS, all_controls, default_for, get_in,
                            is_live, load, looseness_to_thresholds,
                            read_derived, save_updates, set_in,
                            thresholds_to_looseness, write_derived)

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


# ============================== dotted paths =================================
cfg = {"a": {"b": {"c": 1}}, "top": 2}
check("get_in reads nested", get_in(cfg, "a.b.c") == 1)
check("get_in reads top level", get_in(cfg, "top") == 2)
check("get_in returns the default when missing",
      get_in(cfg, "a.b.nope", "dflt") == "dflt"
      and get_in(cfg, "nope.deep.path", 7) == 7)
check("get_in survives a non-dict midway",
      get_in({"a": 5}, "a.b.c", "safe") == "safe")

set_in(cfg, "a.b.c", 9)
check("set_in overwrites", cfg["a"]["b"]["c"] == 9)
set_in(cfg, "x.y.z", "new")
check("set_in creates intermediate dicts", cfg["x"]["y"]["z"] == "new")
set_in(cfg, "top.now.nested", 3)
check("set_in replaces a scalar with a dict when needed",
      cfg["top"] == {"now": {"nested": 3}})

# ============================ load / save round trip =========================
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "config.json")

check("load of a missing file yields defaults, writes nothing",
      load(p) == DEFAULT_CONFIG and not os.path.exists(p))

with open(p, "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("load of a corrupt file yields defaults", load(p) == DEFAULT_CONFIG)

# partial user file merges over defaults, nested
with open(p, "w", encoding="utf-8") as f:
    json.dump({"sensitivity": 12.5, "scroll": {"gain_notches_s": 44.0}}, f)
merged = load(p)
check("load merges user values over defaults",
      merged["sensitivity"] == 12.5
      and merged["scroll"]["gain_notches_s"] == 44.0
      and merged["scroll"]["curve"] == DEFAULT_CONFIG["scroll"]["curve"],
      f"curve={merged['scroll']['curve']}")

# --- the important one: writes must not destroy what they don't know about ---
with open(p, "w", encoding="utf-8") as f:
    json.dump({"sensitivity": 9.0,
               "attention": {"neutral_yaw": 12.5, "neutral_pitch": -3.0},
               "scroll": {"gain_notches_s": 30.0},
               "some_future_key": {"deep": [1, 2, 3]}}, f)
ok = save_updates({"scroll.gain_notches_s": 55.0, "sensitivity": 11.0}, p)
with open(p, encoding="utf-8") as f:
    raw = json.load(f)
check("save_updates reports success", ok is True)
check("save_updates applies the requested paths",
      raw["scroll"]["gain_notches_s"] == 55.0 and raw["sensitivity"] == 11.0)
check("save_updates preserves the tracker's calibration",
      raw["attention"]["neutral_yaw"] == 12.5
      and raw["attention"]["neutral_pitch"] == -3.0,
      f"attention={raw.get('attention')}")
check("save_updates preserves keys it has never heard of",
      raw["some_future_key"] == {"deep": [1, 2, 3]})
check("save_updates does not inject every default into the file",
      "pinch" not in raw, f"keys={sorted(raw)}")

# writing into a nested block that isn't in the file yet
save_updates({"magnet.strength": 77.0}, p)
with open(p, encoding="utf-8") as f:
    raw = json.load(f)
check("save_updates creates a missing block", raw["magnet"]["strength"] == 77.0)

# a corrupt file must not stop settings from being saved
with open(p, "w", encoding="utf-8") as f:
    f.write("garbage{")
save_updates({"sensitivity": 3.0}, p)
with open(p, encoding="utf-8") as f:
    raw = json.load(f)
check("save over a corrupt file still yields valid json",
      raw == {"sensitivity": 3.0}, f"raw={raw}")

# no temp files left behind
check("no .cfg temp files left in the directory",
      not [f for f in os.listdir(tmp) if f.startswith(".cfg")],
      f"stray={[f for f in os.listdir(tmp) if f.startswith('.cfg')]}")

# the file the app actually writes stays loadable by the tracker's own loader
real = os.path.join(_APP_DIR, "config.json")
if os.path.exists(real):
    with open(real, encoding="utf-8") as f:
        check("the project's real config.json is valid json",
              isinstance(json.load(f), dict))

# ============================ control manifest ===============================
controls = all_controls()
check("every section has a name, intro and controls",
      all(s.get("name") and s.get("intro") and s.get("controls")
          for s in SECTIONS), f"n={len(SECTIONS)}")
check("all seven sections are present",
      [s["name"] for s in SECTIONS] == ["Pointer", "Scrolling", "Gestures",
                                        "Magnet", "Launcher", "Attention",
                                        "Camera"],
      f"{[s['name'] for s in SECTIONS]}")

# THE guard rail: the UI can never offer a setting that isn't a real key
phantom = [(sec, c["key"]) for sec, c in controls
           if c.get("key") and not c.get("derived") and default_for(c["key"]) is None]
check("no control binds a key missing from the defaults", not phantom,
      f"phantom={phantom}")

# every slider's default must sit inside the range it offers
bad_range = []
for sec, c in controls:
    if c["kind"] != "slider" or c.get("derived"):
        continue
    d = default_for(c["key"])
    if d is None or not (c["lo"] <= float(d) <= c["hi"]):
        bad_range.append((sec, c["key"], d, c["lo"], c["hi"]))
check("every slider's default is inside its range", not bad_range,
      f"bad={bad_range}")

check("toggles bind boolean defaults",
      all(isinstance(default_for(c["key"]), bool) for _s, c in controls
          if c["kind"] == "toggle" and not c.get("derived")))

check("every control has a label and a caption",
      all(c.get("label") and c.get("caption") for _s, c in controls))

# captions must actually use the highlight markup (that's the whole point)
unmarked = [c["label"] for _s, c in controls if "*" not in c["caption"]]
check("every caption highlights a phrase", not unmarked,
      f"unmarked={unmarked}")

check("markup markers are balanced in every caption",
      all(c["caption"].count("*") % 2 == 0 for _s, c in controls),
      f"odd={[c['label'] for _s, c in controls if c['caption'].count('*') % 2]}")

# live vs restart-only, so the UI tells the truth about what applies now
check("launcher keys are live", is_live("launcher.commands")
      and is_live("launcher.hold_s"))
check("motion keys need a restart", not is_live("sensitivity")
      and not is_live("scroll.gain_notches_s"))

# ============================ derived controls ===============================
lo_y, lo_p = looseness_to_thresholds(0)
hi_y, hi_p = looseness_to_thresholds(100)
check("looseness 0 is the tightest window", lo_y < hi_y and lo_p < hi_p)
check("looseness maps monotonically",
      looseness_to_thresholds(50)[0] > lo_y
      and looseness_to_thresholds(50)[0] < hi_y)
check("looseness clamps out-of-range input",
      looseness_to_thresholds(-40) == looseness_to_thresholds(0)
      and looseness_to_thresholds(400) == looseness_to_thresholds(100))
for pct in (0, 25, 50, 75, 100):
    y, pi = looseness_to_thresholds(pct)
    back = thresholds_to_looseness(y, pi)
    if abs(back - pct) > 0.51:
        check(f"looseness round-trips at {pct}", False, f"got {back:.1f}")
        break
else:
    check("looseness round-trips at every step", True)

# the shipped defaults (35/25) should land mid-slider, not pinned at an end
mid = thresholds_to_looseness(DEFAULT_CONFIG["attention"]["yaw_thresh_deg"],
                              DEFAULT_CONFIG["attention"]["pitch_thresh_deg"])
check("shipped attention default sits mid-slider", 25.0 < mid < 75.0,
      f"looseness={mid:.0f}")

check("looseness writes both threshold keys",
      set(write_derived("_attn_looseness", 50)) ==
      {"attention.yaw_thresh_deg", "attention.pitch_thresh_deg"})

base = load(os.path.join(tmp, "missing.json"))
check("left-handed reads False by default",
      read_derived(base, "_dominant_left") is False)
check("left-handed writes the dominant_hand string",
      write_derived("_dominant_left", True) == {"dominant_hand": "left"}
      and write_derived("_dominant_left", False) == {"dominant_hand": "right"})
base["dominant_hand"] = "left"
check("left-handed reads back True",
      read_derived(base, "_dominant_left") is True)
check("read_derived returns None for an unknown derived key",
      read_derived(base, "_nope") is None)
check("write_derived returns nothing for an unknown key",
      write_derived("_nope", 1) == {})

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

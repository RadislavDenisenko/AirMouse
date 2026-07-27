"""Tests: config loading and merging (Stage 5)."""
import json
import os
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, _APP_DIR)

from airmouse import load_config, make_controller, DEFAULT_CONFIG
from mouse_input import NullMouse

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


tmp = tempfile.mkdtemp()

# missing file -> defaults created on disk
p = os.path.join(tmp, "config.json")
cfg = load_config(p)
check("missing file -> defaults + file created",
      cfg == DEFAULT_CONFIG and os.path.exists(p))

# partial override merges over defaults (nested too)
with open(p, "w") as f:
    json.dump({"sensitivity": 8.5, "pinch": {"down_ratio": 0.30}}, f)
cfg = load_config(p)
check("partial override", cfg["sensitivity"] == 8.5
      and cfg["pinch"]["down_ratio"] == 0.30
      and cfg["pinch"]["up_ratio"] == DEFAULT_CONFIG["pinch"]["up_ratio"]
      and cfg["smoothing"]["min_cutoff"] == DEFAULT_CONFIG["smoothing"]["min_cutoff"])

# corrupt file -> defaults, no crash
with open(p, "w") as f:
    f.write("{not valid json")
cfg = load_config(p)
check("corrupt file -> defaults", cfg == DEFAULT_CONFIG)

# controller built from config honors values
ctrl = make_controller(NullMouse(), {**DEFAULT_CONFIG, "sensitivity": 9.9})
check("controller from config", ctrl.sensitivity == 9.9)

# project config.json parses and has all top-level keys
real = load_config(os.path.join(_APP_DIR, "config.json"))
check("project config.json has all keys",
      set(real.keys()) == set(DEFAULT_CONFIG.keys()))

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

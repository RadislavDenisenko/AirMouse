"""Tests: v3.2 launcher binding — Start Menu app discovery, search ranking,
friendly labels, and the config round-trip the settings panel performs.

No Tk here: the panel's UI is thin, but the data layer under it (app_index +
save/load) is what silently corrupts a config if it's wrong, so that's what
gets tested.
"""
import json
import os
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, _APP_DIR)

import app_index

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


# ===================== Start Menu scan (synthetic trees) =====================
tmp = tempfile.mkdtemp()
user_dir = os.path.join(tmp, "user", "Programs")
all_dir = os.path.join(tmp, "all", "Programs")
os.makedirs(os.path.join(user_dir, "Games"))
os.makedirs(os.path.join(all_dir, "Steam"))


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("")


touch(os.path.join(user_dir, "Games", "Minecraft.lnk"))
touch(os.path.join(user_dir, "Discord.lnk"))
touch(os.path.join(all_dir, "Steam", "Steam.lnk"))
touch(os.path.join(all_dir, "Steam", "Uninstall Steam.lnk"))   # noise
touch(os.path.join(all_dir, "Steam", "Steam Support Center.url"))  # noise
touch(os.path.join(all_dir, "Discord.lnk"))                    # duplicate
touch(os.path.join(all_dir, "notes.txt"))                      # not a shortcut

apps = app_index.scan_start_menu([user_dir, all_dir])
names = [a["name"] for a in apps]
check("scans BOTH start-menu trees",
      "Minecraft" in names and "Steam" in names, f"names={names}")
check("recurses into subfolders", "Minecraft" in names)
check("drops uninstallers and support links",
      not any("Uninstall" in n or "Support" in n for n in names),
      f"names={names}")
check("ignores non-shortcut files", "notes" not in names)
check("de-duplicates across trees", names.count("Discord") == 1,
      f"names={names}")
check("per-user shortcut wins the duplicate",
      next(a for a in apps if a["name"] == "Discord")["path"].startswith(user_dir))
check("sorted by name", names == sorted(names, key=str.lower), f"names={names}")

# a missing / unreadable directory must not raise — the panel has to open
# even on a locked-down machine
safe = app_index.scan_start_menu([os.path.join(tmp, "nope"), "", user_dir])
check("missing directory is skipped, not fatal",
      [a["name"] for a in safe] == ["Discord", "Minecraft"],
      f"got={[a['name'] for a in safe]}")
check("empty scan returns a list", app_index.scan_start_menu([]) == [])

# ============================== search ranking ===============================
res = app_index.search(apps, "st")
check("search finds Steam", any(a["name"] == "Steam" for a in res))
check("prefix match ranks first", res[0]["name"] == "Steam",
      f"first={res[0]['name']}")
check("empty query returns everything", len(app_index.search(apps, "")) == len(apps))
check("search is case-insensitive",
      [a["name"] for a in app_index.search(apps, "STEAM")] == ["Steam"])
check("no match returns empty", app_index.search(apps, "zzzz") == [])

# a contains-match must still appear, just below the prefix matches
apps2 = apps + [{"name": "My Steam Tool", "path": "x", "source": ""}]
res2 = app_index.search(apps2, "steam")
check("prefix beats contains",
      [a["name"] for a in res2] == ["Steam", "My Steam Tool"],
      f"order={[a['name'] for a in res2]}")

# ============================ friendly labels ================================
cases = [
    ("steam://open/games", "Steam"),
    ("spotify:", "Spotify"),
    ("ms-settings:", "Ms-settings"),
    (r"C:\Program Files\Steam\Steam.lnk", "Steam"),
    (r"C:\Users\me\Downloads", "Downloads"),
    ("https://www.youtube.com/feed", "youtube.com"),
    ("https://github.com", "github.com"),
    ("", ""),
]
for cmd, want in cases:
    got = app_index.friendly_label(cmd)
    check(f"label {cmd!r} -> {want!r}", got == want, f"got={got!r}")

# =============================== presets =====================================
labels = [p[0] for p in app_index.PRESETS]
cmds = [p[1] for p in app_index.PRESETS]
check("Steam library preset uses the URI that works when Steam is running",
      "steam://open/games" in cmds)
check("every preset has a label and a command",
      all(p[0] and p[1] for p in app_index.PRESETS))
check("preset labels are unique", len(labels) == len(set(labels)))

# ========================= config round-trip =================================
# The panel writes commands+labels; airmouse reads them back. Neither may
# disturb the rest of config.json.
import settings_store as store
from settings_store import parse_drop_paths
from airmouse import read_launcher_commands, read_launcher_labels

cfg_path = os.path.join(tmp, "config.json")
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump({"sensitivity": 7.5,
               "launcher": {"hold_s": 0.3, "commands": ["", "", "", ""]},
               "attention": {"yaw_thresh_deg": 35.0}}, f)

store.save_slots(
    ["steam://open/games", r"C:\x\Discord.lnk", "", "https://youtube.com"],
    ["Steam - Library", "Discord", "", "YouTube"], cfg_path)

with open(cfg_path, encoding="utf-8") as f:
    written = json.load(f)
check("binding writes the exact command",
      written["launcher"]["commands"][0] == "steam://open/games",
      f"got={written['launcher']['commands'][0]}")
check("binding writes the friendly label",
      written["launcher"]["labels"][0] == "Steam - Library")
check("unrelated config keys survive a save",
      written["sensitivity"] == 7.5
      and written["attention"]["yaw_thresh_deg"] == 35.0
      and written["launcher"]["hold_s"] == 0.3, f"got={written}")

check("airmouse reads the commands back",
      read_launcher_commands(cfg_path)
      == ["steam://open/games", r"C:\x\Discord.lnk", "", "https://youtube.com"])
check("airmouse reads the labels back",
      read_launcher_labels(cfg_path) == ["Steam - Library", "Discord", "",
                                         "YouTube"])

# load fills to 4 slots even from a short/absent list
store.save_slots(["only-one"], [], cfg_path)
cmds4, labels4 = store.load_slots(cfg_path)
check("short list pads to 4 slots",
      len(cmds4) == 4 and len(labels4) == 4 and cmds4[0] == "only-one",
      f"cmds={cmds4}")

# a corrupt config must not crash the panel — it opens with empty slots
with open(cfg_path, "w", encoding="utf-8") as f:
    f.write("{broken json")
cmds_bad, labels_bad = store.load_slots(cfg_path)
check("corrupt config -> 4 empty slots, no crash",
      cmds_bad == ["", "", "", ""] and labels_bad == ["", "", "", ""])

# ===================== drag-and-drop payload parsing =========================
# tkdnd hands over a Tcl-list-ish string; a slot takes the first path.
check("a bare dropped path parses",
      parse_drop_paths(r"C:\Games\game.exe") == [r"C:\Games\game.exe"])
check("a brace-wrapped path with spaces parses",
      parse_drop_paths(r"{C:\Program Files\My App\app.lnk}")
      == [r"C:\Program Files\My App\app.lnk"])
check("several dropped files keep their order",
      parse_drop_paths(r"{C:\a b.lnk} C:\c.exe")
      == [r"C:\a b.lnk", r"C:\c.exe"])
check("an unterminated brace still yields the path",
      parse_drop_paths(r"{C:\half open.lnk") == [r"C:\half open.lnk"])
check("empty / whitespace payloads yield nothing",
      parse_drop_paths("") == [] and parse_drop_paths("   ") == []
      and parse_drop_paths(None) == [])

# slots round-trip through the real store
rt = os.path.join(tmp, "slots.json")
store.save_slots(["a", "b", "c", "d"], ["A", "B", "C", "D"], rt)
check("slot round trip",
      store.load_slots(rt) == (["a", "b", "c", "d"], ["A", "B", "C", "D"]))
store.save_slots(["x"] * 9, ["X"] * 9, rt)
check("over-long slot lists are truncated to four",
      store.load_slots(rt) == (["x"] * 4, ["X"] * 4))
check("a missing file reads as four empty slots",
      store.load_slots(os.path.join(tmp, "none.json"))
      == (["", "", "", ""], ["", "", "", ""]))

# reading a missing file is also safe
check("missing config reads as empty",
      read_launcher_commands(os.path.join(tmp, "nope.json")) == []
      and read_launcher_labels(os.path.join(tmp, "nope.json")) == [])

# ===================== launching a bound command =============================
# os.startfile is what makes URIs work at all (subprocess would just fail on
# "steam://open/games"). Stub it rather than really launching Steam.
import airmouse

calls = []
real_startfile = os.startfile
os.startfile = lambda cmd: calls.append(cmd)
try:
    ok_uri = airmouse.run_launch_command("steam://open/games")
    ok_path = airmouse.run_launch_command(r"C:\x\Discord.lnk")
    ok_empty = airmouse.run_launch_command("")
finally:
    os.startfile = real_startfile
check("steam:// URI is launched via os.startfile",
      ok_uri and calls[0] == "steam://open/games", f"calls={calls}")
check("a plain path is launched too", ok_path and calls[1] == r"C:\x\Discord.lnk")
check("an unbound slot launches nothing",
      ok_empty is False and len(calls) == 2, f"calls={calls}")

# a command that startfile rejects must not raise into the capture loop
def _boom(_cmd):
    raise OSError("no association")


os.startfile = _boom
try:
    result = airmouse.run_launch_command("::::nonsense::::")
finally:
    os.startfile = real_startfile
check("a broken command never raises into the capture loop",
      result in (True, False), f"result={result!r}")

# ======================= running-window picker ===============================
# Real call against this machine; assert the shape, not the contents.
wins = app_index.list_open_windows()
check("running-window picker returns a list", isinstance(wins, list))
# Never echo a real window title: they carry emoji and non-Latin text that
# the Windows console's cp1252 codec cannot encode, which would fail this
# suite based on nothing but which browser tab happens to be open.
check("each running entry has a name and an .exe path",
      all(w.get("name") and w.get("path", "").lower().endswith(".exe")
          for w in wins), f"n={len(wins)}")

print()
print("ALL PASS" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)

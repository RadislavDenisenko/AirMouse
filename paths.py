"""Where the app's files live, running from source or as a packaged build.

PyInstaller unpacks bundled data into a private temp folder and leaves the
.exe somewhere else, so "next to the code" and "next to the thing the user
double-clicked" stop being the same place. Read-only data (the MediaPipe
models) comes from the bundle; config.json belongs beside the .exe where
someone can actually find and delete it.

If the app is unpacked somewhere unwritable — Program Files, a network share,
straight out of a zip viewer — config falls back to %LOCALAPPDATA%\\AirMouse
rather than crashing on first save.
"""

import os
import sys

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # _MEIPASS is the unpacked bundle; sys.executable is the .exe itself.
    DATA_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _pref_dir = os.path.dirname(os.path.abspath(sys.executable))
else:
    DATA_DIR = _pref_dir = os.path.dirname(os.path.abspath(__file__))


def _writable(directory: str) -> bool:
    probe = os.path.join(directory, ".airmouse-write-test")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def _fallback_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AirMouse")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return _pref_dir       # nothing better to offer; the caller will report
    return d


USER_DIR = _pref_dir if _writable(_pref_dir) else _fallback_dir()

HAND_MODEL = os.path.join(DATA_DIR, "hand_landmarker.task")
FACE_MODEL = os.path.join(DATA_DIR, "face_landmarker.task")
CONFIG_PATH = os.path.join(USER_DIR, "config.json")
SCREENSHOT_PATH = os.path.join(USER_DIR, "screenshot.png")

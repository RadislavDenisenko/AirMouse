"""Build the distributable Windows package.

    venv\\Scripts\\python.exe build_release.py

PyInstaller wipes dist/ on every run, so anything that belongs beside the .exe
has to be put there by this script rather than dropped in by hand. A release
shipped once with no instructions file because it was placed manually and the
next rebuild deleted it — hence the manifest check below, which fails the
build rather than producing a quietly incomplete zip.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "AirMouse"

sys.path.insert(0, str(ROOT))
from config_defaults import APP_VERSION  # noqa: E402

ZIP = DIST / f"AirMouse-{APP_VERSION}-windows-x64.zip"

# Everything a downloaded copy must contain, relative to the app folder.
REQUIRED = [
    "AirMouse.exe",
    "START HERE.txt",
    "_internal/hand_landmarker.task",
    "_internal/face_landmarker.task",
    "_internal/base_library.zip",
]

# Written by a test run; must never reach a release. config.json in particular
# would ship the builder's own launcher paths and calibration.
JUNK = ["config.json", "screenshot.png"]


def run_pyinstaller():
    print("== PyInstaller ==")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "AirMouse.spec", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")],
        check=True, cwd=str(ROOT),
    )


def assemble():
    print("== assembling ==")
    shutil.copy2(ROOT / "packaging" / "START HERE.txt", APP / "START HERE.txt")
    for name in JUNK:
        path = APP / name
        if path.exists():
            path.unlink()
            print(f"   removed stray {name}")


def verify():
    print("== verifying ==")
    missing = [rel for rel in REQUIRED if not (APP / rel).exists()]
    if missing:
        sys.exit("BUILD INCOMPLETE, missing:\n  " + "\n  ".join(missing))
    for rel in REQUIRED:
        size = (APP / rel).stat().st_size
        print(f"   ok  {rel}  ({size / 1024:,.0f} KB)")


def package():
    print("== zipping ==")
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(APP.rglob("*")):
            if path.is_file():
                zf.write(path, Path("AirMouse") / path.relative_to(APP))
    mb = ZIP.stat().st_size / (1024 * 1024)
    print(f"\n{ZIP.name}  ({mb:,.1f} MB)")
    print(f"   {ZIP}")


if __name__ == "__main__":
    run_pyinstaller()
    assemble()
    verify()
    package()

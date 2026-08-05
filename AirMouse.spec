# PyInstaller build spec — produces a self-contained AirMouse folder.
#
#   venv\Scripts\python.exe -m PyInstaller AirMouse.spec --noconfirm
#
# One-folder rather than one-file: a one-file build re-extracts ~250 MB to a
# temp directory on every launch, which makes startup feel broken.
#
# No console. It used to be left on so that a busy camera or a missing model
# had somewhere to say so — a silent window that never appears is worse than
# an ugly one. applog.py takes that job over now: everything printed goes to
# airmouse.log, and anything that stops startup raises a message box.

from PyInstaller.utils.hooks import collect_all

datas = [
    ("hand_landmarker.task", "."),
    ("face_landmarker.task", "."),
]
binaries = []
hiddenimports = [
    # imported inside functions, so static analysis can miss them
    "settings_app",
    "settings_ui",
    "settings_store",
    "app_index",
    "attention",
    "face_tracker",
    # the first-run walkthrough, imported lazily so that its Tk and camera
    # setup cost nothing on an ordinary launch
    "tutorial",
    "applog",
    # the walkthrough shows the camera inside a Tk window; ImageTk is a
    # separate extension module and is easy to miss
    "PIL.ImageTk",
    "PIL.Image",
]

# mediapipe ships .tflite graphs and native libs as package data; without
# collect_all the Tasks API fails at runtime rather than at build time.
for package in ("mediapipe", "tkinterdnd2"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Present in the dev venv for unrelated tooling; none of it ships.
#
# matplotlib is NOT excludable despite us never drawing a chart:
# mediapipe.tasks.python.vision imports drawing_styles -> drawing_utils ->
# matplotlib at module scope, so dropping it (or its own dependencies —
# contourpy, fonttools, kiwisolver, cycler, dateutil) makes the packaged app
# die on import. Verified the hard way.
excludes = [
    "pytest", "docx", "lxml", "pypdfium2", "sounddevice",
    "IPython", "jupyter", "pandas", "scipy", "PyInstaller",
]

a = Analysis(
    ["airmouse.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AirMouse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed exes trip antivirus heuristics far more often
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AirMouse",
)

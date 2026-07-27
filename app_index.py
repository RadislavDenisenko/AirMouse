"""Find the apps installed on this PC, so binding a launcher slot is a click
on a name instead of a hunt through Program Files.

Windows keeps a de-facto list of "things the user thinks of as apps" in the
Start Menu: one folder per user, one shared by all users. Every entry is a
.lnk (or .url) shortcut whose filename is already the friendly name — exactly
what we want to show. We read those two trees, drop the obvious noise
(uninstallers, docs, control-panel bits), and de-duplicate by name, since an
app installed for all users often appears in both trees.

Also here: PRESETS — hand-written entries for things that are better launched
by URI than by .exe. Steam is the motivating case: running steam.exe directly
works, but `steam://open/games` opens the library even when Steam is already
running in the tray. Nothing here launches anything; resolving a name to a
command string is all this module does.
"""

import os

# (label, command, note) — the command is what os.startfile() receives.
PRESETS = (
    ("Steam - Library", "steam://open/games",
     "opens your games list (works if Steam is already running)"),
    ("Steam - Big Picture", "steam://open/bigpicture",
     "couch/TV mode"),
    ("Steam - Friends", "steam://open/friends", "friends list"),
    ("Discord", "discord://", "jumps to Discord if installed"),
    ("Spotify", "spotify:", "opens the Spotify app"),
    ("Windows Settings", "ms-settings:", "the Settings app"),
    ("Downloads folder", os.path.expanduser(r"~\Downloads"), "file explorer"),
    ("Desktop folder", os.path.expanduser(r"~\Desktop"), "file explorer"),
    ("YouTube", "https://youtube.com", "opens in your default browser"),
    ("ChatGPT", "https://chat.openai.com", "opens in your default browser"),
)

# Start Menu roots: per-user first so a user's own shortcut wins on a tie.
_START_MENU_DIRS = (
    os.path.join(os.environ.get("APPDATA", ""),
                 r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("PROGRAMDATA", ""),
                 r"Microsoft\Windows\Start Menu\Programs"),
)

_SKIP_WORDS = ("uninstall", "readme", "help", "documentation", "release notes",
               "license", "eula", "changelog", "manual", "support",
               "report a", "repair", "modify", "website", "web site",
               "command prompt", "powershell", "control panel", "odbc",
               "configuration editor", "debug", "crash")

_EXTS = (".lnk", ".url")


def _looks_like_noise(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in _SKIP_WORDS)


def scan_start_menu(dirs=None):
    """All Start Menu shortcuts as [{'name','path','source'}], sorted by name
    and de-duplicated case-insensitively (per-user beats all-users).

    Never raises: an unreadable directory is simply skipped, because this
    feeds a UI that must open even on a locked-down machine."""
    dirs = _START_MENU_DIRS if dirs is None else dirs
    found = {}
    for root_dir in dirs:
        if not root_dir or not os.path.isdir(root_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fn in filenames:
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in _EXTS or _looks_like_noise(stem):
                    continue
                key = stem.lower()
                if key in found:          # first writer wins (per-user first)
                    continue
                found[key] = {"name": stem,
                              "path": os.path.join(dirpath, fn),
                              "source": root_dir}
    return sorted(found.values(), key=lambda a: a["name"].lower())


def search(apps, query: str):
    """Filter apps by a typed query. Names that START with the query rank
    above ones that merely contain it, so typing "st" puts Steam near the top
    instead of burying it under "Adobe Substance"."""
    q = (query or "").strip().lower()
    if not q:
        return list(apps)
    starts = [a for a in apps if a["name"].lower().startswith(q)]
    contains = [a for a in apps
                if q in a["name"].lower() and not a["name"].lower().startswith(q)]
    return starts + contains


def friendly_label(command: str) -> str:
    """A short human name for an already-bound command, for the HUD/panel:
    'steam://open/games' -> 'Steam', 'C:\\...\\Steam.lnk' -> 'Steam'."""
    if not command:
        return ""
    cmd = command.strip()
    if "://" in cmd or (cmd.endswith(":") and " " not in cmd):
        scheme = cmd.split(":", 1)[0]
        if scheme in ("http", "https"):
            host = cmd.split("://", 1)[-1].split("/")[0]
            return host[4:] if host.startswith("www.") else host
        return scheme.capitalize()
    return os.path.splitext(os.path.basename(cmd.rstrip("\\/")))[0] or cmd


def list_open_windows():
    """Visible top-level windows as [{'name','path'}] — 'bind what's already
    running'. The bound command is the owning process's .exe path (a window
    title isn't launchable). Windows-only; returns [] if anything is
    unavailable, so callers can just hide the button."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    results = {}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)

    def _each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or title in ("Program Manager", "Windows Input Experience"):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 pid.value)
        if not h:
            return True
        try:
            size = wintypes.DWORD(1024)
            path_buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(h, 0, path_buf,
                                                   ctypes.byref(size)):
                exe = path_buf.value
                if exe and exe.lower() not in results:
                    results[exe.lower()] = {
                        "name": f"{os.path.splitext(os.path.basename(exe))[0]}"
                                f"  -  {title[:40]}",
                        "path": exe}
        finally:
            kernel32.CloseHandle(h)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_each), 0)
    except OSError:
        return []
    return sorted(results.values(), key=lambda a: a["name"].lower())

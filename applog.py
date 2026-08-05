"""Where output goes once there is no console to print to.

The app used to run with a console window attached, which was ugly on every
launch but did one important job: when the camera was busy or a model file
was missing, the reason was right there on screen. Hiding the console means
taking that job over deliberately, because a window that silently never
appears is a worse experience than an ugly one.

So: everything printed goes to a log file, and anything that stops the app
from starting also raises a normal Windows dialog saying so in plain words.
The log lives beside config.json — the same folder the user can actually
find, and one that stays writable even when the app is unpacked somewhere
read-only.
"""

import ctypes
import datetime
import os
import sys
import traceback

from paths import USER_DIR

LOG_PATH = os.path.join(USER_DIR, "airmouse.log")
MAX_BYTES = 512 * 1024          # roll once it gets silly; keep one old copy

MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_SYSTEMMODAL = 0x1000

# Plain-language explanations for the failures that actually happen. Anything
# not listed falls back to the exception text plus a pointer to the log.
FRIENDLY = {
    "camera": ("AirMouse can't open your webcam.\n\n"
               "Another app is probably using it — Discord, Zoom, Teams, or "
               "a browser tab. Close that and start AirMouse again."),
    "model": ("AirMouse is missing one of its tracking model files.\n\n"
              "The download may not have unzipped completely. Try unzipping "
              "it again, keeping every file together in one folder."),
}


class _Tee:
    """Writes to the log file, and to the real stream if there still is one.

    Running under pythonw (or a windowed build) leaves sys.stdout as None, so
    every print would otherwise raise. This swallows that case rather than
    making logging itself a source of crashes.
    """

    def __init__(self, stream, handle):
        self.stream = stream
        self.handle = handle

    def write(self, text):
        try:
            self.handle.write(text)
            self.handle.flush()
        except Exception:
            pass
        if self.stream is not None:
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                pass

    def flush(self):
        for target in (self.handle, self.stream):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass


_handle = None


def _roll():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_BYTES:
            old = LOG_PATH + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.replace(LOG_PATH, old)
    except OSError:
        pass


def start(app_version=""):
    """Send stdout and stderr to the log file. Safe to call twice."""
    global _handle
    if _handle is not None:
        return LOG_PATH
    _roll()
    try:
        _handle = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    except OSError:
        return None                      # nowhere to log; carry on regardless
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _handle.write(f"\n===== AirMouse {app_version} started {stamp} =====\n")
    _handle.flush()
    sys.stdout = _Tee(sys.__stdout__, _handle)
    sys.stderr = _Tee(sys.__stderr__, _handle)
    return LOG_PATH


def dialog(message, title="AirMouse", warning=False):
    """A normal Windows message box. Never raises — if even this fails, the
    log is all we have left, and a crash here would hide the real problem."""
    try:
        icon = MB_ICONWARNING if warning else MB_ICONERROR
        ctypes.windll.user32.MessageBoxW(
            None, str(message), str(title), icon | MB_SYSTEMMODAL)
        return True
    except Exception:
        return False


def explain(kind):
    """The plain-language text for a known failure, or None."""
    return FRIENDLY.get(kind)


def fatal(kind=None, detail="", exc=None):
    """Log a startup failure and tell the user about it in plain words."""
    body = explain(kind) or ""
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        if not body:
            body = f"AirMouse hit an unexpected problem:\n\n{exc}"
    elif detail:
        print(detail)
        if not body:
            body = detail
    if not body:
        body = "AirMouse could not start."
    if _handle is not None:
        body += f"\n\nDetails were written to:\n{LOG_PATH}"
    dialog(body)
    return body

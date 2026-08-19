"""Data layer for the AirMouse settings app.

config.json is the API between the settings app and the running tracker, so
all the fiddly parts live here — behind plain functions with tests, not
buried in UI callbacks:

  * dotted-path get/set ("scroll.gain_notches_s")
  * atomic writes that PRESERVE keys this app doesn't know about (the tracker
    writes calibration + slider state into the same file while running)
  * SECTIONS: the control manifest — every slider/toggle the app shows, with
    its real config key, range, default and caption. A test asserts every
    key resolves against config_defaults, so the UI can never offer a
    setting that doesn't exist.
  * which keys the tracker re-reads live vs. only at startup, so the UI can
    say "after restart" instead of lying.

The tracker re-reads the launcher and magnet blocks while running, so those
apply without a restart; everything else is read once at startup.
"""

import copy
import json
import os
import tempfile

from config_defaults import DEFAULT_CONFIG
from paths import CONFIG_PATH, USER_DIR  # noqa: F401 (CONFIG_PATH re-exported)

APP_DIR = USER_DIR

# Keys the running tracker picks up without a restart.
LIVE_PREFIXES = ("launcher.", "magnet.", "brake.", "lens.")


# ------------------------------------------------------------ dotted paths ---
def get_in(cfg, path, default=None):
    """Value at a dotted path, or `default` if any step is missing."""
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_in(cfg, path, value):
    """Set a dotted path, creating intermediate dicts. Returns cfg."""
    parts = path.split(".")
    cur = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return cfg


def default_for(path):
    """The shipped default for a dotted path (None if unknown)."""
    return get_in(DEFAULT_CONFIG, path)


def is_live(path):
    """True if the running tracker picks this up without a restart."""
    return any(path.startswith(p) for p in LIVE_PREFIXES)


# ------------------------------------------------------------------- files ---
def load(path=CONFIG_PATH):
    """Defaults deep-merged with whatever is on disk. Never writes, never
    raises — a missing or corrupt file just yields the defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, json.JSONDecodeError):
        return cfg
    if not isinstance(user, dict):
        return cfg
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    return cfg


def save_updates(updates, path=CONFIG_PATH):
    """Apply {dotted_path: value} to the file on disk and write it back
    atomically.

    Reads the RAW file (not the merged view) and edits only the given paths,
    so unknown keys and anything the tracker wrote meanwhile survive. The
    write goes to a temp file in the same directory and is then replaced into
    place, so a crash mid-write can't leave a truncated config."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    for dotted, value in updates.items():
        set_in(raw, dotted, value)
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cfg", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


# ------------------------------------------------------------ launcher slots ---
SLOT_COUNT = 4


def load_slots(path=CONFIG_PATH):
    """(commands, labels), always exactly SLOT_COUNT long. A short list, a
    missing file or a corrupt one all read as empty slots rather than
    raising — the settings app must always open."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        block = data.get("launcher", {}) if isinstance(data, dict) else {}
        cmds = list(block.get("commands") or [])
        labels = list(block.get("labels") or [])
    except (OSError, json.JSONDecodeError, AttributeError):
        cmds, labels = [], []
    cmds = [c if isinstance(c, str) else "" for c in cmds]
    labels = [l if isinstance(l, str) else "" for l in labels]
    while len(cmds) < SLOT_COUNT:
        cmds.append("")
    while len(labels) < SLOT_COUNT:
        labels.append("")
    return cmds[:SLOT_COUNT], labels[:SLOT_COUNT]


def save_slots(cmds, labels, path=CONFIG_PATH):
    """Persist both slot lists, padded to SLOT_COUNT, atomically."""
    cmds = list(cmds) + [""] * SLOT_COUNT
    labels = list(labels) + [""] * SLOT_COUNT
    return save_updates({"launcher.commands": cmds[:SLOT_COUNT],
                         "launcher.labels": labels[:SLOT_COUNT]}, path)


def parse_drop_paths(raw):
    """Paths out of a tkdnd <<Drop>> payload.

    The payload is Tcl-list-ish: paths containing spaces arrive brace-wrapped
    ('{C:\\a b.lnk} C:\\c.exe'), bare ones space-separated. Returns them in
    order; a slot holds one command, so callers take the first."""
    out, buf, i = [], "", 0
    raw = (raw or "").strip()
    while i < len(raw):
        ch = raw[i]
        if ch == "{":
            end = raw.find("}", i)
            if end == -1:
                out.append(raw[i + 1:].strip())
                break
            out.append(raw[i + 1:end])
            i = end + 1
        elif ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
            i += 1
        else:
            buf += ch
            i += 1
    if buf:
        out.append(buf)
    return [p for p in (s.strip() for s in out) if p]


# --------------------------------------------------------- control manifest ---
# kind: "slider" | "toggle" | "stepper" | "note" | "slots"
# Captions use *asterisk markup* — the marked phrase is the glanceable answer
# to "what does this do?" (see DESIGN.md §3).
SECTIONS = (
    {
        "name": "Pointer",
        "intro": "How the cursor follows your right hand.",
        "controls": (
            {"kind": "slider", "label": "Speed", "key": "sensitivity",
             "lo": 0.5, "hi": 30.0, "fmt": "{:.1f}",
             "caption": "*How far the cursor travels* for the same hand "
                        "movement."},
            {"kind": "slider", "label": "Edge boost", "key": "edge_multiplier",
             "lo": 1.0, "hi": 8.0, "fmt": "{:.1f}",
             "caption": "*Faster near the circle's edge* — precise in the "
                        "middle, quick at the rim."},
            {"kind": "slider", "label": "Circle size", "key": "radius_scale",
             "lo": 0.5, "hi": 8.0, "fmt": "{:.1f}",
             "caption": "*How far your hand can roam* before the circle "
                        "starts following it."},
            {"kind": "slider", "label": "Steadiness",
             "key": "smoothing.min_cutoff", "lo": 0.05, "hi": 3.0,
             "fmt": "{:.2f}", "advanced": True,
             "caption": "*Lower is smoother* but adds a little lag."},
            {"kind": "slider", "label": "Dead zone", "key": "dead_zone_px",
             "lo": 0.0, "hi": 30.0, "fmt": "{:.0f}", "advanced": True,
             "caption": "*Ignores tiny tremors* while your hand rests."},
            {"kind": "slider", "label": "Hold to engage",
             "key": "engage_hold_s", "lo": 0.2, "hi": 3.0, "fmt": "{:.1f}s",
             "advanced": True,
             "caption": "*How long to hold the open palm* before the cursor "
                        "locks on."},
        ),
    },
    {
        "name": "Scrolling",
        "intro": "The peace sign sets a zero point. Hold away from it to "
                 "scroll — move back toward it and the zero jumps to your "
                 "hand, so reversing is instant.",
        "controls": (
            {"kind": "slider", "label": "Scroll speed",
             "key": "scroll.gain_notches_s", "lo": 1.0, "hi": 100.0,
             "fmt": "{:.0f}",
             "caption": "*Speed at one hand-width off centre* — the main "
                        "scrolling feel."},
            {"kind": "slider", "label": "Top speed",
             "key": "scroll.max_notches_s", "lo": 10.0, "hi": 300.0,
             "fmt": "{:.0f}",
             "caption": "*Fastest it will ever scroll*, however far you "
                        "reach."},
            {"kind": "slider", "label": "Stop zone",
             "key": "scroll.dead_zone_hs", "lo": 0.05, "hi": 0.8,
             "fmt": "{:.2f}",
             "caption": "*How close to the zero counts as stopped.*"},
            {"kind": "slider", "label": "Reverse sensitivity",
             "key": "scroll.recenter_speed_hs", "lo": 0.3, "hi": 3.0,
             "fmt": "{:.1f}", "advanced": True,
             "caption": "*How deliberate a direction change must be* to move "
                        "the zero to your hand."},
            {"kind": "toggle", "label": "Natural direction",
             "key": "scroll.natural",
             "caption": "*Flips which way the page moves* when you raise "
                        "your hand."},
        ),
    },
    {
        "name": "Gestures",
        "intro": "Your thumb picks the action; a grab arms navigation.",
        "controls": (
            {"kind": "note", "label": "The pinch ladder",
             "caption": "Thumb to index clicks, to middle right-clicks, to "
                        "ring is *volume*. A closed fist *arms navigation*."},
            {"kind": "note", "label": "Armed navigation",
             "caption": "Grab first, then *swipe left or right* for "
                        "forward/back, *push down* to minimise, or *tug "
                        "up* to bring the last minimised window back."},
            {"kind": "slider", "label": "Grab looseness",
             "key": "fist.down_ratio", "lo": 1.0, "hi": 1.6, "fmt": "{:.2f}",
             "caption": "*Higher = a lazier grab counts.* Only a closed hand "
                        "can arm, so this can be generous."},
            {"kind": "note", "label": "The precision brake",
             "caption": "Curl your *middle, ring and pinky* while pointing "
                        "and the cursor slows down — *squeeze harder to go "
                        "slower*. Your index and thumb stay free, so you can "
                        "still click without letting go."},
            {"kind": "toggle", "label": "Precision brake",
             "key": "brake.enabled",
             "caption": "*Squeeze three fingers to slow the cursor* for small "
                        "targets."},
            {"kind": "slider", "label": "Slowest speed",
             "key": "brake.min_scale", "lo": 0.1, "hi": 0.9, "fmt": "{:.2f}",
             "caption": "Speed at a *full squeeze*, as a share of normal. "
                        "*Lower = finer control.*"},
            {"kind": "slider", "label": "Brake takes hold at",
             "key": "brake.onset", "lo": -0.2, "hi": 0.6, "fmt": "{:+.2f}",
             "advanced": True,
             "caption": "*How much curl before it starts.* Raise it if "
                        "the cursor slows while you are just pointing."},
            {"kind": "slider", "label": "Full squeeze at",
             "key": "brake.full", "lo": 0.3, "hi": 1.2, "fmt": "{:.2f}",
             "advanced": True,
             "caption": "Curl depth that reaches the *slowest speed*. Lower "
                        "it if you can't squeeze far enough to bottom out."},
            {"kind": "slider", "label": "Brake smoothing",
             "key": "brake.smoothing", "lo": 0.1, "hi": 1.0, "fmt": "{:.2f}",
             "advanced": True,
             "caption": "*Lower = smoother but laggier.* Smoothing stops the "
                        "speed shimmering while you hold a squeeze."},
            {"kind": "slider", "label": "Swipe distance",
             "key": "swipe.hand_widths", "lo": 0.6, "hi": 3.5,
             "fmt": "{:.1f}",
             "caption": "*How far to swipe* for forward/back."},
            {"kind": "slider", "label": "Swipe speed",
             "key": "swipe.min_speed_hw_s", "lo": 1.5, "hi": 12.0,
             "fmt": "{:.1f}",
             "caption": "*How briskly* — slower than this is just moving "
                        "your hand."},
            {"kind": "slider", "label": "Push distance",
             "key": "flick_down.hand_widths", "lo": 0.3, "hi": 2.5,
             "fmt": "{:.1f}",
             "caption": "*How far to push down* to minimise a window."},
            {"kind": "slider", "label": "Push speed",
             "key": "flick_down.min_speed_hw_s", "lo": 1.5, "hi": 12.0,
             "fmt": "{:.1f}",
             "caption": "*How briskly to push* — a slow drop is ignored."},
            {"kind": "toggle", "label": "Reset to centre",
             "key": "recenter.enabled",
             "caption": "*The shaka sign* — thumb and pinky out, the rest "
                        "folded, held a beat — *jumps the cursor to the "
                        "middle* of the screen."},
            {"kind": "slider", "label": "Reset hold time",
             "key": "recenter.hold_s", "lo": 0.15, "hi": 1.0, "fmt": "{:.2f}",
             "advanced": True,
             "caption": "*How long to hold the horns* before the cursor "
                        "jumps home."},
            {"kind": "toggle", "label": "Volume pinch",
             "key": "volume.enabled",
             "caption": "*Thumb to ring finger sets the volume* — pull up "
                        "for louder, down for quieter."},
            {"kind": "slider", "label": "Volume sensitivity",
             "key": "volume.steps_per_hs", "lo": 2.0, "hi": 24.0,
             "fmt": "{:.0f}",
             "caption": "*Notches per hand-width moved* — higher changes the "
                        "volume faster."},
            {"kind": "toggle", "label": "Swap forward and back",
             "key": "swipe.invert",
             "caption": "Use this if *left/right feel backwards*."},
            {"kind": "toggle", "label": "Push down to minimise",
             "key": "flick_down.enabled",
             "caption": "*Turns the minimise gesture off* entirely."},
            {"kind": "toggle", "label": "Tug up to restore",
             "key": "flick_up.enabled",
             "caption": "*Brings back the window you last minimised* — "
                        "grab and pull up."},
            {"kind": "slider", "label": "Clench first",
             "key": "flick_down.arm_age_s", "lo": 0.0, "hi": 0.5,
             "fmt": "{:.2f}s", "advanced": True,
             "caption": "*How long the fist must exist before the pull "
                        "starts.* Raise it if lowering your arm still "
                        "minimises things."},
        ),
    },
    {
        "name": "Magnet",
        "intro": "Sticky targets, like a TV remote.",
        "controls": (
            {"kind": "magnet_toggle", "label": "Magnetism",
             "key": "magnet.enabled",
             "caption": "*Buttons grab your cursor* — like a TV remote. "
                        "*Push through* to break free."},
            {"kind": "toggle", "label": "Custom tuning",
             "key": "magnet.custom_tuning",
             "caption": "*Off means full strength* with nothing to set. Turn "
                        "it on to use the sliders below."},
            {"kind": "slider", "label": "Grab range",
             "key": "magnet.capture_radius_px", "lo": 10.0, "hi": 90.0,
             "fmt": "{:.0f}", "advanced": True,
             "caption": "*How near a pass has to be* for the cursor to hook "
                        "on. Needs custom tuning."},
            {"kind": "slider", "label": "Escape effort",
             "key": "magnet.escape_px", "lo": 15.0, "hi": 90.0,
             "fmt": "{:.0f}", "advanced": True,
             "caption": "*How far to move to break free* of a hooked "
                        "target. Needs custom tuning."},
            {"kind": "slider", "label": "Pull strength",
             "key": "magnet.strength", "lo": 0.0, "hi": 100.0, "fmt": "{:.0f}",
             "advanced": True,
             "caption": "*How much it slows you on approach*, before it "
                        "hooks. Needs custom tuning."},
            {"kind": "slider", "label": "Reach", "key": "magnet.reach_px",
             "lo": 8.0, "hi": 160.0, "fmt": "{:.0f}", "advanced": True,
             "caption": "*How far out it starts to bite.* Needs custom "
                        "tuning."},
            {"kind": "slider", "label": "Centring pull", "key": "magnet.pull",
             "lo": 0.0, "hi": 1.0, "fmt": "{:.2f}", "advanced": True,
             "caption": "*How much it steers toward the middle* of a target. "
                        "Zero means slowdown only."},
            {"kind": "toggle", "label": "Reach into apps",
             "key": "magnet.use_msaa", "advanced": True,
             "caption": "*Finds buttons inside apps*, not just window "
                        "buttons. Turn off if any app misbehaves."},
        ),
    },
    {
        "name": "Lens",
        "intro": "A magnifier follows your cursor, so you can read what "
                 "you're clicking from across the room. Slow down and it "
                 "appears; move fast and it gets out of the way.",
        "controls": (
            {"kind": "toggle", "label": "Zoom lens",
             "key": "lens.enabled",
             "caption": "*Magnifies what's under the cursor* while your "
                        "hand is driving it. Squeeze the precision brake "
                        "to bring it up instantly."},
            {"kind": "slider", "label": "Magnification", "key": "lens.zoom",
             "lo": 1.5, "hi": 4.0, "fmt": "{:.2f}x",
             "caption": "*How much bigger* things look inside the lens."},
            {"kind": "slider", "label": "Lens size", "key": "lens.size_frac",
             "lo": 0.12, "hi": 0.50, "fmt": "{:.0%}",
             "caption": "*How big the lens is* — its height, as a share of "
                        "your screen. At the top it fills half the "
                        "screen."},
            {"kind": "slider", "label": "Appears below",
             "key": "lens.aim_speed", "lo": 30.0, "hi": 300.0,
             "fmt": "{:.0f}", "advanced": True,
             "caption": "*Cursor speed that counts as aiming.* Raise it if "
                        "the lens is shy; lower it if it pops up while "
                        "you're still moving."},
            {"kind": "slider", "label": "Gone above",
             "key": "lens.travel_speed", "lo": 300.0, "hi": 1500.0,
             "fmt": "{:.0f}", "advanced": True,
             "caption": "*Cursor speed that counts as traveling* — past "
                        "this the lens is fully hidden."},
        ),
    },
    {
        "name": "Launcher",
        "intro": "Hold 1-4 fingers up on your LEFT hand to launch something. "
                 "It is the COUNT that matters, not which fingers.",
        "controls": (
            {"kind": "slots", "label": "Your four slots",
             "caption": "*Both hands must be visible* — a lone hand always "
                        "drives the cursor."},
            {"kind": "slider", "label": "Hold time", "key": "launcher.hold_s",
             "lo": 0.1, "hi": 1.5, "fmt": "{:.2f}s",
             "caption": "*How long to hold the count* before it fires."},
            {"kind": "slider", "label": "Re-fire lockout",
             "key": "launcher.cooldown_s", "lo": 0.2, "hi": 5.0,
             "fmt": "{:.1f}s",
             "caption": "*Stops a double launch* right after one fires."},
        ),
    },
    {
        "name": "Attention",
        "intro": "Nothing moves unless you're looking at the screen.",
        "controls": (
            {"kind": "toggle", "label": "Attention gate",
             "key": "attention.enabled",
             "caption": "*Pauses control when you look away.* Turn it off to "
                        "always control."},
            {"kind": "slider", "label": "Looseness", "key": "_attn_looseness",
             "lo": 0.0, "hi": 100.0, "fmt": "{:.0f}", "derived": True,
             "caption": "*How far you can turn* and still count as looking. "
                        "Higher is more forgiving."},
            {"kind": "slider", "label": "Patience",
             "key": "attention.off_grace_s", "lo": 0.1, "hi": 2.0,
             "fmt": "{:.2f}s", "advanced": True,
             "caption": "*How long you must look away* before it pauses."},
            {"kind": "slider", "label": "Let go after",
             "key": "attention.drop_after_s", "lo": 0.5, "hi": 6.0,
             "fmt": "{:.1f}s", "advanced": True,
             "caption": "*Fully releases a held drag* after this long away."},
            {"kind": "note", "label": "Calibrate",
             "caption": "Look at the centre of your screen and *press C in "
                        "the camera window* to teach it your neutral pose."},
        ),
    },
    {
        "name": "Camera",
        "intro": "Which camera, and how it copes with dim rooms.",
        "controls": (
            {"kind": "stepper", "label": "Camera", "key": "camera_index",
             "lo": 0, "hi": 5,
             "caption": "*Which webcam to use* if you have more than one."},
            {"kind": "toggle", "label": "Open the preview window",
             "key": "show_preview",
             "caption": "Off starts the camera preview *minimised to the "
                        "taskbar* — the app runs the same, it just doesn't "
                        "open a window over your work."},
            {"kind": "toggle", "label": "Low-light rescue",
             "key": "low_light_auto",
             "caption": "*Keeps 30fps in a dim room* by fixing the exposure."},
            {"kind": "toggle", "label": "Left-handed",
             "key": "_dominant_left", "derived": True,
             "caption": "*Swaps the roles* — left hand drives the cursor."},
            {"kind": "toggle", "label": "Show the walkthrough",
             "key": "_show_tutorial", "derived": True,
             "caption": "*Replays the gesture walkthrough* the next time "
                        "AirMouse starts."},
        ),
    },
)

# Attention "Looseness" maps one slider onto the two threshold keys.
ATTN_YAW = (20.0, 55.0)
ATTN_PITCH = (14.0, 40.0)


def looseness_to_thresholds(pct):
    """0..100 -> (yaw_deg, pitch_deg), proportional across both."""
    t = max(0.0, min(100.0, pct)) / 100.0
    return (ATTN_YAW[0] + (ATTN_YAW[1] - ATTN_YAW[0]) * t,
            ATTN_PITCH[0] + (ATTN_PITCH[1] - ATTN_PITCH[0]) * t)


def thresholds_to_looseness(yaw, pitch):
    """Inverse of the above, averaging the two axes."""
    ty = (yaw - ATTN_YAW[0]) / (ATTN_YAW[1] - ATTN_YAW[0])
    tp = (pitch - ATTN_PITCH[0]) / (ATTN_PITCH[1] - ATTN_PITCH[0])
    return max(0.0, min(100.0, (ty + tp) / 2.0 * 100.0))


def read_derived(cfg, key):
    """Value for a control that isn't a plain config key."""
    if key == "_attn_looseness":
        return thresholds_to_looseness(
            get_in(cfg, "attention.yaw_thresh_deg", 35.0),
            get_in(cfg, "attention.pitch_thresh_deg", 25.0))
    if key == "_dominant_left":
        return get_in(cfg, "dominant_hand", "right") == "left"
    if key == "_show_tutorial":
        # Stored the other way round: the config records that the
        # walkthrough is FINISHED, the switch offers to show it again.
        return not get_in(cfg, "tutorial_done", False)
    return None


def write_derived(key, value):
    """{dotted: value} updates a derived control expands into."""
    if key == "_attn_looseness":
        yaw, pitch = looseness_to_thresholds(value)
        return {"attention.yaw_thresh_deg": round(yaw, 1),
                "attention.pitch_thresh_deg": round(pitch, 1)}
    if key == "_dominant_left":
        return {"dominant_hand": "left" if value else "right"}
    if key == "_show_tutorial":
        return {"tutorial_done": not value}
    return {}


def all_controls():
    """Every control across every section, flattened."""
    return [(s["name"], c) for s in SECTIONS for c in s["controls"]]

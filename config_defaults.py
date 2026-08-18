"""Default configuration for AirMouse — the single source of truth.

Lives in its own tiny module (no cv2/mediapipe imports) so the settings app
can read the defaults, and validate that every control it offers binds to a
real key, without dragging in the whole vision stack.
"""

APP_NAME = "AirMouse"
# Beta: the gesture set and settings app are usable day to day, but the
# thresholds are still being tuned against real hands and cursor magnetism
# is unfinished. Bump to 1.0 when the feature list in the README is closed
# out and the defaults have stopped moving.
APP_VERSION = "3.8"
APP_STAGE = "beta"
APP_TITLE = f"{APP_NAME} {APP_VERSION} {APP_STAGE}"

DEFAULT_CONFIG = {
    "camera_index": 0,
    # False starts the preview minimised to the taskbar instead of popping a
    # window over your work. It still exists — click it in the taskbar for
    # the keys (Q quit, B settings, D debug) — it just doesn't open in your
    # face. Settings can also always be opened with run_settings.bat.
    "show_preview": True,
    # The first-run walkthrough. Set back to False (or press Replay in
    # Settings) to see it again — it owns the webcam while it runs, so the
    # tracker only starts once it has closed.
    "tutorial_done": False,
    "dominant_hand": "right",
    # Pointer speed. The old 9.0 meant crossing a wide screen took a full
    # sweep of the arm, which read as the app being sluggish rather than as
    # a setting being low. The precision brake covers the fine end now, so
    # the default can sit where it feels immediate.
    "sensitivity": 14.0,
    # Cursor speed is uniform across the circle by default. The edge boost
    # (up to this many times faster at the rim) is a real help on a wide
    # screen, but it makes the pointer feel like it accelerates away from
    # you, and the precision brake now covers the "be careful here" case far
    # more directly. 1.0 = off; raise it if you want the reach back.
    "edge_multiplier": 1.0,
    "radius_scale": 2.2,
    "min_radius_px": 40.0,
    "dead_zone_px": 5.0,
    # Raise an open hand and it connects. The hold used to be 1.2s with a
    # splayed "high five" required, which made the very first thing anyone
    # did feel like being ignored. What is left is just long enough that a
    # hand crossing the frame on its way somewhere else cannot take the
    # cursor. Set engage_spread_ratio above 0 to demand the wide pose again.
    "engage_hold_s": 0.2,
    "engage_spread_ratio": 0.0,
    "lose_grace_s": 0.25,
    "smoothing": {"min_cutoff": 0.5, "beta": 0.007},
    "pinch": {"down_ratio": 0.28, "up_ratio": 0.38, "debounce_frames": 2,
              "pinch_freeze_s": 0.15, "release_freeze_s": 0.10},
    "right_pinch": {"down_ratio": 0.28, "up_ratio": 0.38, "debounce_frames": 2},
    # Arming needs a REAL fist. 1.30 let a relaxed half-curl arm navigation,
    # which read as the app firing on its own; a deliberate clench measures
    # ~1.0 and comfortably clears 1.10, a resting hand does not. Reopening
    # past up_ratio releases, so the wide gap keeps a held fist stable
    # through motion blur.
    "fist": {"down_ratio": 1.10, "up_ratio": 1.50, "debounce_frames": 2},
    # Precision brake: squeeze the middle, ring and pinky to slow the cursor
    # for small targets. Proportional, not a switch — `onset` is the curl
    # depth (in hand-size units) where slowing begins and `full` where it
    # bottoms out at `min_scale` of normal speed. The gap between them is
    # deliberate headroom: fingers rest half-curled while pointing, and that
    # must never brake on its own. `smoothing` eases the amount so a steady
    # squeeze gives a steady speed instead of shimmering.
    # Tuned hotter after live use: the old curve wanted most of a full
    # squeeze before it felt like anything. Braking starts almost as soon as
    # the fingers begin to fold and bottoms out at about half squeeze. The
    # floor sat at 0.15 for a while, but that made a firm squeeze a crawl —
    # too slow to still steer with. 0.30 keeps a full squeeze clearly braked
    # while leaving enough speed to reach the target. The shape gate (index
    # out, middle folded) is what keeps a resting hand from braking, so the
    # onset can afford to be this eager.
    "brake": {"enabled": True, "onset": 0.08, "full": 0.55,
              "min_scale": 0.30, "smoothing": 0.35},
    # Scrolling is rate-based, so `gain` is how fast a small offset already
    # moves the page. It was tuned low enough that people leaned further and
    # further out of the dead zone waiting for something to happen.
    "scroll": {"natural": False, "dead_zone_hs": 0.25,
               "gain_notches_s": 48.0, "max_notches_s": 170.0,
               "curve": 1.5,
               "enter_score": 0.10, "exit_score": 0.0,
               "enter_hold_s": 0.06, "exit_hold_s": 0.20,
               "restore_s": 0.5, "restore_dist_hs": 0.5,
               "recenter_speed_hs": 1.0, "recenter_hold_s": 0.1},
    # Swipe distance is in hand-widths, so it holds at any camera distance.
    # 1.8 was most of an arm's sweep — long enough that a normal flick
    # registered as nothing and the gesture felt broken rather than picky.
    # `invert` swaps back/forward if you prefer the opposite convention;
    # as shipped, moving your hand right goes BACK, matching a trackpad.
    "swipe": {"enabled": True, "hand_widths": 0.9, "window_s": 0.7,
              "min_speed_hw_s": 3.5, "refractory_s": 0.8,
              "invert": False, "armed_frac": 0.6},
    # Grab and pull down to minimise. `require_landing` False means it fires
    # on the pull itself rather than waiting for the hand to stop, which is
    # what made it feel unreliable; the closed fist is what keeps a dropped
    # arm from triggering it. Distance and speed are lowered to match — a
    # short, definite tug is enough.
    "flick_down": {"enabled": True, "hand_widths": 0.6, "window_s": 0.40,
                   "min_speed_hw_s": 3.0, "refractory_s": 1.0,
                   "armed_frac": 0.6, "require_landing": False,
                   "settle_speed_hw_s": 1.5, "settle_timeout_s": 0.4},
    "volume": {"enabled": True, "steps_per_hs": 8.0, "down_ratio": 0.28,
               "up_ratio": 0.38, "debounce_frames": 2},
    # Rock-and-roll horns (index + pinky up, middle + ring curled) held for
    # `hold_s` teleports the cursor to the screen centre — the reset for a
    # relative mouse that has drifted your arm somewhere uncomfortable.
    "recenter": {"enabled": True, "hold_s": 0.3, "refractory_s": 1.0},
    "roles": {"launcher_cooldown_s": 1.0},
    # Cursor magnetism: clickable things get sticky as the cursor nears them.
    # Simple mode (custom_tuning False) ignores the tuning values below and
    # uses magnet.PRESET, so one toggle gives a strong hook with no
    # configuration; the rest only apply once custom tuning is on. `pull` is
    # how hard it draws toward a target's centre, as a share of the motion
    # you are already making. `use_msaa` and `use_uia` are the two
    # accessibility tiers that find controls drawing themselves (most modern
    # UI, including the close button on each Chrome tab) on top of the cheap
    # Win32 ones — escape hatches, in case some app misbehaves under them.
    # Starts OFF: the precision brake covers the same "stop on a small
    # target" problem by hand, and magnetism is the more intrusive of the
    # two, so it is opted into rather than out of. The setting persists
    # normally once changed.
    "magnet": {"enabled": False, "custom_tuning": False,
               "strength": 80.0, "reach_px": 90.0, "pull": 0.45,
               "capture_radius_px": 40.0, "escape_px": 40.0,
               "refractory_s": 0.3, "include_text_fields": False,
               "use_msaa": True, "use_uia": True, "poll_hz": 12.0},
    # Zoom lens: a rounded 4:3 window that follows the cursor and
    # magnifies what is under it, for driving the pointer from across the
    # room. Speed decides presence — slow down (you are aiming) and it
    # blooms, flick (you are traveling) and it fades out of the way;
    # squeezing the precision brake brings it on regardless. Zoom is how
    # much bigger, size_frac the lens HEIGHT as a share of the monitor's
    # short side (so a rotated monitor feels identical), and the two
    # speeds (px/s on a 1440-short-side screen, scaled to the monitor)
    # are where the bloom starts and where the lens is fully gone. Only
    # shows while a hand is actually aiming the cursor — a real mouse,
    # an armed fist or a volume pinch never see it.
    # aim_speed started at 90, which treated the small corrective moves of
    # actually using the lens as "traveling" — barely nudging the cursor
    # dismissed the zoom. 180 keeps it up through gentle steering, and the
    # wider gap to travel_speed makes the fade start later and softer.
    "lens": {"enabled": True, "zoom": 2.5, "size_frac": 0.22,
             "aim_speed": 180.0, "travel_speed": 950.0},
    "launcher": {"hold_s": 0.3, "cooldown_s": 1.0,
                 "commands": ["", "", "", ""],
                 "labels": ["", "", "", ""]},
    "attention": {
        "enabled": True,
        "yaw_thresh_deg": 35.0,
        "pitch_thresh_deg": 25.0,
        "exit_margin_deg": 10.0,
        "smooth_alpha": 0.4,
        "require_eyes_open": False,
        "eyes_closed_s": 0.8,
        "blink_thresh": 0.55,
        "on_grace_s": 0.15,
        "off_grace_s": 0.60,
        "drop_after_s": 2.0,
        "detect_every_n": 2,
        "neutral_yaw": 0.0,
        "neutral_pitch": 0.0,
    },
    "low_light_auto": True,
}

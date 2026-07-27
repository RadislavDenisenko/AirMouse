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
APP_VERSION = "0.9.0"
APP_STAGE = "beta"
APP_TITLE = f"{APP_NAME} {APP_VERSION} {APP_STAGE}"

DEFAULT_CONFIG = {
    "camera_index": 0,
    "dominant_hand": "right",
    "sensitivity": 9.0,
    "edge_multiplier": 3.0,
    "radius_scale": 2.2,
    "min_radius_px": 40.0,
    "dead_zone_px": 5.0,
    "engage_hold_s": 1.2,
    "engage_spread_ratio": 1.7,
    "lose_grace_s": 0.25,
    "smoothing": {"min_cutoff": 0.5, "beta": 0.007},
    "pinch": {"down_ratio": 0.28, "up_ratio": 0.38, "debounce_frames": 2,
              "pinch_freeze_s": 0.15, "release_freeze_s": 0.10},
    "right_pinch": {"down_ratio": 0.28, "up_ratio": 0.38, "debounce_frames": 2},
    "fist": {"down_ratio": 1.30, "up_ratio": 1.55, "debounce_frames": 2},
    "scroll": {"natural": False, "dead_zone_hs": 0.3,
               "gain_notches_s": 30.0, "max_notches_s": 120.0,
               "curve": 1.5,
               "enter_score": 0.10, "exit_score": 0.0,
               "enter_hold_s": 0.06, "exit_hold_s": 0.20,
               "restore_s": 0.5, "restore_dist_hs": 0.5,
               "recenter_speed_hs": 1.0, "recenter_hold_s": 0.1},
    "swipe": {"enabled": True, "hand_widths": 1.8, "window_s": 0.7,
              "min_speed_hw_s": 5.0, "refractory_s": 0.8,
              "invert": False, "armed_frac": 0.6},
    "flick_down": {"enabled": True, "hand_widths": 0.9, "window_s": 0.40,
                   "min_speed_hw_s": 4.5, "refractory_s": 1.0,
                   "armed_frac": 0.6,
                   "settle_speed_hw_s": 1.5, "settle_timeout_s": 0.4},
    "volume": {"enabled": True, "steps_per_hs": 8.0, "down_ratio": 0.28,
               "up_ratio": 0.38, "debounce_frames": 2},
    "roles": {"launcher_cooldown_s": 1.0},
    # Cursor magnetism: clickable things get sticky as the cursor nears
    # them. `pull` is how hard it draws toward a target's centre, as a share
    # of the motion you are already making. `use_msaa` adds accessibility
    # queries for controls that draw themselves (most modern UI) on top of
    # the cheap Win32 tiers.
    "magnet": {"enabled": True, "strength": 70.0, "reach_px": 60.0,
               "pull": 0.35, "use_msaa": True, "poll_hz": 12.0},
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

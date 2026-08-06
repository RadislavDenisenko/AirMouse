"""AirMouse — control the mouse with hand gestures via webcam.

Raise an open palm to engage (an anchor circle locks in), move your hand to
move the cursor, pinch thumb+index to click or drag. Q quits, P pauses.
"""

import argparse
import copy
import json
import os
import sys
import time

# OpenCV's Media Foundation backend logs a warning for every failed frame
# grab. A webcam held by another app fails thousands of them, burying the one
# line that tells the user what to actually do about it. Must be set before
# cv2 is imported.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "0")

import cv2  # noqa: E402 (import order is deliberate; see above)

import applog  # noqa: E402
from config_defaults import (APP_TITLE, APP_VERSION,  # noqa: F401
                             DEFAULT_CONFIG)
from hand_tracker import (HandTracker, draw_landmarks, to_pixel_points,
                          WRIST, THUMB_TIP, INDEX_TIP, MIDDLE_TIP)
from gestures import (GestureController, IDLE, ENGAGING, TRACKING, PINCHED,
                      RCLICK, ARMED, SCROLL, VOLUME, BRAKING, POINT,
                      hand_raised)
from hand_roles import RoleAssigner
from launcher import FingerLauncher, SLOT_LABELS
from magnet import MagnetMouse, TargetFinder, resolve_params
from mouse_input import Mouse, NullMouse, get_cursor_pos

from paths import (CONFIG_PATH, FACE_MODEL as FACE_MODEL_PATH, FROZEN,
                   HAND_MODEL as MODEL_PATH, SCREENSHOT_PATH, USER_DIR)

APP_DIR = USER_DIR
WINDOW = APP_TITLE

STATE_COLORS = {IDLE: (90, 90, 255), ENGAGING: (0, 200, 255),
                TRACKING: (0, 220, 0), PINCHED: (0, 170, 255),
                RCLICK: (255, 0, 255), ARMED: (0, 90, 255),
                SCROLL: (255, 255, 0), VOLUME: (0, 210, 210),
                BRAKING: (150, 220, 120)}

def load_config(path: str = CONFIG_PATH) -> dict:
    """Load config.json, filling any missing keys with defaults.
    A broken or absent file falls back to defaults (and says so)."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except FileNotFoundError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        print(f"created default {os.path.basename(path)}")
        return cfg
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read config ({e}); using defaults")
        return cfg
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    return cfg


def make_controller(mouse, cfg: dict) -> GestureController:
    sw = cfg.get("swipe", {})
    fl = cfg.get("flick_down", {})
    return GestureController(
        mouse,
        sensitivity=cfg["sensitivity"],
        edge_multiplier=cfg.get("edge_multiplier", 3.0),
        radius_scale=cfg.get("radius_scale", 2.2),
        min_radius_px=cfg.get("min_radius_px", 40.0),
        engage_hold_s=cfg["engage_hold_s"],
        engage_spread_ratio=cfg.get("engage_spread_ratio", 1.9),
        lose_grace_s=cfg["lose_grace_s"],
        dead_zone_px=cfg["dead_zone_px"],
        filter_min_cutoff=cfg["smoothing"]["min_cutoff"],
        filter_beta=cfg["smoothing"]["beta"],
        pinch_down_ratio=cfg["pinch"]["down_ratio"],
        pinch_up_ratio=cfg["pinch"]["up_ratio"],
        pinch_debounce_frames=cfg["pinch"]["debounce_frames"],
        pinch_freeze_s=cfg["pinch"]["pinch_freeze_s"],
        release_freeze_s=cfg["pinch"]["release_freeze_s"],
        right_down_ratio=cfg["right_pinch"]["down_ratio"],
        right_up_ratio=cfg["right_pinch"]["up_ratio"],
        right_debounce_frames=cfg["right_pinch"]["debounce_frames"],
        volume_enabled=cfg.get("volume", {}).get("enabled", True),
        volume_down_ratio=cfg.get("volume", {}).get("down_ratio", 0.28),
        volume_up_ratio=cfg.get("volume", {}).get("up_ratio", 0.38),
        volume_debounce_frames=cfg.get("volume", {}).get("debounce_frames", 2),
        volume_steps_per_hs=cfg.get("volume", {}).get("steps_per_hs", 8.0),
        fist_down_ratio=cfg["fist"]["down_ratio"],
        fist_up_ratio=cfg["fist"]["up_ratio"],
        fist_debounce_frames=cfg["fist"]["debounce_frames"],
        recenter_enabled=cfg.get("recenter", {}).get("enabled", True),
        recenter_hold_s=cfg.get("recenter", {}).get("hold_s", 0.3),
        recenter_refractory_s=cfg.get("recenter", {}).get("refractory_s", 1.0),
        brake_enabled=cfg.get("brake", {}).get("enabled", True),
        brake_onset=cfg.get("brake", {}).get("onset", 0.15),
        brake_full=cfg.get("brake", {}).get("full", 0.75),
        brake_min_scale=cfg.get("brake", {}).get("min_scale", 0.25),
        brake_smoothing=cfg.get("brake", {}).get("smoothing", 0.35),
        scroll_natural=cfg["scroll"].get("natural", False),
        scroll_dead_zone_hs=cfg["scroll"].get("dead_zone_hs", 0.3),
        scroll_gain_notches_s=cfg["scroll"].get("gain_notches_s", 30.0),
        scroll_max_notches_s=cfg["scroll"].get("max_notches_s", 120.0),
        scroll_curve=cfg["scroll"].get("curve", 1.5),
        scroll_enter_score=cfg["scroll"].get("enter_score", 0.10),
        scroll_exit_score=cfg["scroll"].get("exit_score", 0.0),
        scroll_enter_hold_s=cfg["scroll"].get("enter_hold_s", 0.06),
        scroll_exit_hold_s=cfg["scroll"].get("exit_hold_s", 0.20),
        scroll_restore_s=cfg["scroll"].get("restore_s", 0.5),
        scroll_restore_dist_hs=cfg["scroll"].get("restore_dist_hs", 0.5),
        scroll_recenter_speed_hs=cfg["scroll"].get("recenter_speed_hs", 1.0),
        scroll_recenter_hold_s=cfg["scroll"].get("recenter_hold_s", 0.1),
        attention_drop_s=cfg["attention"]["drop_after_s"],
        swipe_enabled=sw.get("enabled", True),
        swipe_hand_widths=sw.get("hand_widths", 1.8),
        swipe_window_s=sw.get("window_s", 0.7),
        swipe_min_speed_hw_s=sw.get("min_speed_hw_s", 5.0),
        swipe_refractory_s=sw.get("refractory_s", 0.8),
        swipe_invert=sw.get("invert", False),
        swipe_armed_frac=sw.get("armed_frac", 0.6),
        flick_down_enabled=fl.get("enabled", True),
        flick_down_hand_widths=fl.get("hand_widths", 0.9),
        flick_down_window_s=fl.get("window_s", 0.40),
        flick_down_min_speed_hw_s=fl.get("min_speed_hw_s", 4.5),
        flick_down_refractory_s=fl.get("refractory_s", 1.0),
        flick_down_armed_frac=fl.get("armed_frac", 0.6),
        flick_down_require_landing=fl.get("require_landing", False),
        flick_down_settle_speed_hw_s=fl.get("settle_speed_hw_s", 1.5),
        flick_down_settle_timeout_s=fl.get("settle_timeout_s", 0.4),
    )


def run_launch_command(cmd: str) -> bool:
    """Open a program/file/URL for the finger launcher. Best-effort; never
    raises into the capture loop."""
    if not cmd:
        return False
    import subprocess
    try:
        os.startfile(cmd)   # handles .exe, files, and steam:// style URLs
        return True
    except (OSError, ValueError):
        try:
            subprocess.Popen(cmd, shell=True)
            return True
        except OSError as e:
            print(f"launcher: could not run {cmd!r} ({e})")
            return False


def make_attention(cfg: dict):
    """Build the attention gate from config (or None if disabled)."""
    a = cfg["attention"]
    if not a.get("enabled", True):
        return None
    from attention import AttentionGate
    return AttentionGate(
        yaw_thresh_deg=a["yaw_thresh_deg"],
        pitch_thresh_deg=a["pitch_thresh_deg"],
        exit_margin_deg=a.get("exit_margin_deg", 8.0),
        smooth_alpha=a.get("smooth_alpha", 0.4),
        require_eyes_open=a["require_eyes_open"],
        eyes_closed_s=a["eyes_closed_s"],
        blink_thresh=a["blink_thresh"],
        on_grace_s=a["on_grace_s"],
        off_grace_s=a["off_grace_s"],
        neutral_yaw=a["neutral_yaw"],
        neutral_pitch=a["neutral_pitch"],
    )


def save_neutral(path: str, yaw: float, pitch: float):
    """Persist a freshly calibrated neutral pose back into config.json so it
    survives restarts. Best-effort — a write failure just isn't fatal."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("attention", {})
    data["attention"]["neutral_yaw"] = round(yaw, 2)
    data["attention"]["neutral_pitch"] = round(pitch, 2)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False


def open_camera(index: int, width: int = 640, height: int = 480, fps: int = 30):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
    # When another app holds the webcam, Media Foundation still reports the
    # device as open but every property write then blocks for ~15s apiece,
    # which reads as a hang before the user ever sees an explanation. One
    # probe read costs nothing and tells us whether configuring is worth it;
    # the caller's frame-rate check reports the failure either way.
    if cap.isOpened():
        ok, probe = cap.read()
        if not (ok and probe is not None):
            return cap
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


def _measure_fps(cap, n: int = 20) -> float:
    """Measured delivery rate of good frames; 0.0 if the camera isn't
    producing frames at all."""
    # A camera held by another app fails every read, and each failure can
    # block for seconds under Media Foundation. Give up during the warm-up
    # rather than grinding through the full run to reach the same answer.
    misses = 0
    for _ in range(5):
        ok, _warm = cap.read()
        if not (ok and _warm is not None):
            misses += 1
            if misses >= 3:
                return 0.0
    good = 0
    t0 = time.perf_counter()
    for _ in range(n):
        ok, frame = cap.read()
        if ok and frame is not None:
            good += 1
    dt = time.perf_counter() - t0
    if good < n // 2:
        return 0.0
    return good / dt if dt > 0 else 0.0


def ensure_framerate(cap, min_fps: float = 20.0, want_fps: float = 30.0):
    """In dim light auto-exposure can stretch to >100ms/frame, tanking the
    camera to ~8fps (and adding motion blur). If that happens, force a short
    manual exposure (1/32s) + max gain so tracking stays at 30fps.
    Returns (measured_fps, low_light_mode).

    A camera pinned below 30 by its own control panel is a separate problem
    and used to pass silently: anything over min_fps was accepted, so a
    Logitech left at 24 fps just felt jagged with nothing said about it.
    Every frame is a chance to move the pointer, so the shortfall is felt as
    stutter rather than seen as a number. We re-ask for 30 once, and the
    caller reports it if the camera still refuses."""
    fps = _measure_fps(cap)
    if fps >= min_fps:
        if fps < want_fps - 2.0:
            # Re-request the fast path. Some drivers only offer 30 under
            # MJPG and quietly fall back to a slower YUY2 mode.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FPS, want_fps)
            fps = max(fps, _measure_fps(cap))
        return fps, False
    for _ in range(2):  # occasionally the driver renegotiates badly; retry once
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        cap.set(cv2.CAP_PROP_EXPOSURE, -5)
        cap.set(cv2.CAP_PROP_GAIN, 255)
        cap.set(cv2.CAP_PROP_FPS, 30)
        fps = _measure_fps(cap)
        if fps >= min_fps:
            break
    return fps, True


_wndproc_refs = []      # the subclass callback must outlive the window


def lock_preview_window(title):
    """Make the preview window impossible to drag by its title bar.

    Gesture-clicking the preview's caption used to freeze the whole app:
    Windows enters a modal move loop the moment a caption drag starts, and
    that loop blocks this process's only thread — so the capture loop, the
    tracker and the cursor all stopped until the pinch released. The window
    is subclassed and caption-drag messages are swallowed; every other
    message (the close button, the sliders, keys) passes straight through.
    The window stays where it opened, which for an always-on-top preview is
    no real loss.
    """
    import ctypes
    from ctypes import wintypes
    WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK, HTCAPTION = 0x00A1, 0x00A3, 2
    GWLP_WNDPROC = -4
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            return False
        proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                   ctypes.c_uint, wintypes.WPARAM,
                                   wintypes.LPARAM)
        u32.CallWindowProcW.restype = ctypes.c_ssize_t
        u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                        ctypes.c_uint, wintypes.WPARAM,
                                        wintypes.LPARAM]
        u32.SetWindowLongPtrW.restype = ctypes.c_void_p
        u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                          ctypes.c_void_p]
        old = {"proc": None}

        @proto
        def wndproc(h, msg, wp, lp):
            if msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) \
                    and wp == HTCAPTION:
                return 0
            return u32.CallWindowProcW(old["proc"], h, msg, wp, lp)

        old["proc"] = u32.SetWindowLongPtrW(
            hwnd, GWLP_WNDPROC, ctypes.cast(wndproc, ctypes.c_void_p))
        if not old["proc"]:
            return False
        _wndproc_refs.append((wndproc, old))
        return True
    except Exception:
        return False        # cosmetic guard; never worth failing startup for


def minimize_window_by_title(title):
    """Send a window to the taskbar without destroying it."""
    import ctypes
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = u32.FindWindowW(None, title)
        if hwnd:
            u32.ShowWindow(hwnd, 6)          # SW_MINIMIZE
            return True
    except Exception:
        pass
    return False


def draw_attention(frame, attn):
    """Top-right attention readout + a dim veil while control is suspended."""
    h, w = frame.shape[:2]
    looking = attn.attending
    status = attn.status()
    color = (0, 220, 0) if looking else (0, 90, 255)

    # small filled dot + status word, upper-right
    cx = w - 16
    cv2.circle(frame, (cx, 78), 7, color, -1, cv2.LINE_AA)
    (tw, _), _ = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    cv2.putText(frame, status, (cx - 16 - tw, 84),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    if attn.yaw is not None:
        yp = f"yaw {attn.yaw - attn.neutral_yaw:+5.0f}  pit {attn.pitch - attn.neutral_pitch:+5.0f}"
        (tw2, _), _ = cv2.getTextSize(yp, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(frame, yp, (w - 12 - tw2, 106),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 220, 0) if attn.facing else (120, 120, 200), 1, cv2.LINE_AA)

    if not looking:
        # subtle darkening veil so it's obvious control is asleep
        veil = frame.copy()
        cv2.rectangle(veil, (0, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(veil, 0.28, frame, 0.72, 0, frame)
        msg = "NOT LOOKING - control paused"
        (tw3, _), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.putText(frame, msg, ((w - tw3) // 2, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)


def draw_overlay(frame, info, fps, low_light=False, paused=False, attn=None):
    h, w = frame.shape[:2]
    state = info["state"]
    color = STATE_COLORS[state]

    if attn is not None:
        draw_attention(frame, attn)

    cv2.putText(frame, f"FPS {fps:5.1f}", (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, state, (10, 54),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    if paused:
        cv2.putText(frame, "PAUSED (P to resume)", (10, 82),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 120, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, "Q quit  P pause  B settings  D debug  S shot", (w - 340, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1, cv2.LINE_AA)
    # top row, beside the FPS counter: the bottom-left corner already holds
    # the launcher legend and the low-light warning
    cv2.putText(frame, f"v{APP_VERSION} beta", (122, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (130, 130, 130), 1,
                cv2.LINE_AA)
    if info.get("pinch_ratio") is not None:
        cv2.putText(frame, f"pinch {info['pinch_ratio']:.2f}", (w - 130, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 170, 255) if info.get("pinch_down") else (180, 180, 180),
                    1, cv2.LINE_AA)
    if low_light:
        cv2.putText(frame, "LOW LIGHT - add light for best tracking",
                    (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 200, 255), 1, cv2.LINE_AA)

    palm = info["palm"]
    anchor = info["anchor"]

    # Fist armed: swipe navigation is hot — say so right at the hand
    if info.get("fist_armed") and palm is not None and not paused:
        p = (int(palm[0]), int(palm[1]))
        cv2.circle(frame, p, 22, (0, 90, 255), 2, cv2.LINE_AA)
        cv2.putText(frame, "ARMED: swipe = back/fwd, snap down = minimize",
                    (max(4, p[0] - 170), p[1] - 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 90, 255), 1, cv2.LINE_AA)

    # Precision brake: show how much slowdown the squeeze is buying, right
    # at the hand. It is proportional, so a number alone would be useless —
    # the bar is what tells you whether to squeeze harder.
    brake = info.get("brake", 0.0)
    if brake > 0.02 and palm is not None and not paused:
        p = (int(palm[0]), int(palm[1]))
        bx, by = max(4, p[0] - 40), p[1] + 40
        cv2.rectangle(frame, (bx, by), (bx + 80, by + 7), (70, 70, 70), -1)
        cv2.rectangle(frame, (bx, by), (bx + int(80 * brake), by + 7),
                      (150, 220, 120), -1)
        cv2.putText(frame, f"BRAKE  speed x{info.get('brake_scale', 1.0):.2f}",
                    (bx - 4, by - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (150, 220, 120), 1, cv2.LINE_AA)

    # Engage progress ring around the palm while holding the open palm
    if state == ENGAGING and palm is not None:
        p = (int(palm[0]), int(palm[1]))
        cv2.circle(frame, p, 28, (120, 120, 120), 2, cv2.LINE_AA)
        cv2.ellipse(frame, p, (28, 28), -90, 0, 360 * info["engage_progress"],
                    (0, 200, 255), 3, cv2.LINE_AA)

    # Self-scaling anchor circle + line to current palm while tracking. The
    # ring is the "edge" — sensitivity ramps up toward it and the circle
    # trails the hand once you cross it.
    if state in (TRACKING, PINCHED, RCLICK, ARMED, SCROLL,
                 BRAKING) and anchor is not None:
        a = (int(anchor[0]), int(anchor[1]))
        radius = max(8, int(info.get("radius", 40)))
        cv2.circle(frame, a, radius, (0, 220, 0), 2, cv2.LINE_AA)
        cv2.circle(frame, a, max(2, int(info.get("dead_zone", 5))),
                   (0, 160, 0), 1, cv2.LINE_AA)
        if palm is not None:
            p = (int(palm[0]), int(palm[1]))
            cv2.line(frame, a, p, (0, 220, 0), 1, cv2.LINE_AA)
            cv2.circle(frame, p, 6, (255, 200, 0), -1, cv2.LINE_AA)
    if state == SCROLL:
        # joystick scroll: mark the neutral point + dead zone; the offset
        # from it sets the constant scroll speed
        so = info.get("scroll_origin")
        if so is not None:
            o = (int(so[0]), int(so[1]))
            dz = max(4, int(info.get("scroll_dead_px", 0)))
            cv2.circle(frame, o, dz, (255, 255, 0), 1, cv2.LINE_AA)
            cv2.drawMarker(frame, o, (255, 255, 0), cv2.MARKER_CROSS, 14, 1)
            if palm is not None:
                p = (int(palm[0]), int(palm[1]))
                cv2.line(frame, o, p, (255, 255, 0), 1, cv2.LINE_AA)
        if palm is not None:
            p = (int(palm[0]), int(palm[1]))
            cv2.putText(frame, "hold away = scroll (farther = faster), middle = stop",
                        (max(4, p[0] - 150), p[1] + 44),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)


def draw_magnet(frame, mouse):
    """Show when the cursor is caught by something, and how hard."""
    got = getattr(mouse, "last_target", None)
    if not got:
        return
    rect, kind = got
    state = getattr(mouse, "state", "")
    scale = getattr(mouse, "last_scale", 1.0)
    if state == "approach" and scale >= 0.995:
        return          # a target is known but too far to affect anything
    h, w = frame.shape[:2]
    if state == "captured":
        label = f"HOOKED  {kind}"
    elif state == "escaping":
        label = "RELEASING"
    else:
        label = f"MAGNET  {kind}  {int(round((1.0 - scale) * 100))}%"
    (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    x0 = (w - tw) // 2
    cv2.putText(frame, label, (x0, h - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (80, 220, 255), 2, cv2.LINE_AA)
    # a little bar showing how much the cursor is being slowed
    bx, by, bw = x0, h - 38, tw
    # while hooked the bar shows how close you are to breaking free
    frac = (getattr(mouse, "escape_frac", 0.0) if state == "captured"
            else min(1.0, 1.0 - scale))
    cv2.rectangle(frame, (bx, by), (bx + bw, by + 6), (60, 60, 60), -1)
    cv2.rectangle(frame, (bx, by), (bx + int(bw * frac), by + 6),
                  (80, 220, 255), -1)


def draw_magnet_debug(frame, mouse, finder):
    """D readout for magnetism: what is captured, how far, how much grip."""
    h, w = frame.shape[:2]
    F, GO, NO, HDR = cv2.FONT_HERSHEY_SIMPLEX, (120, 240, 120), (150, 150, 150), (0, 220, 255)
    got = getattr(mouse, "last_target", None)
    scale = getattr(mouse, "last_scale", 1.0)
    on = getattr(mouse, "enabled", False)
    rows = [("MAGNET", None)]
    rows.append((f"enabled {'yes' if on else 'no'}", bool(on)))
    if finder is not None:
        rows.append((f"finder errors {finder.errors}", finder.errors == 0))
        rows.append((f"poll {finder.probe_ms:.1f}ms", finder.probe_ms < 30.0))
    if got:
        rect, kind = got
        cur = get_cursor_pos()
        from magnet import dist_to_rect
        label = kind
        if finder is not None and finder.last_name:
            label += f" '{finder.last_name[:18]}'"
        if finder is not None and finder.last_tier:
            label += f" via {finder.last_tier}"
        rows.append((f"target {label}", True))
        rows.append((f"rect {rect[0]},{rect[1]} {rect[2] - rect[0]}x"
                     f"{rect[3] - rect[1]}", True))
        rows.append((f"distance {dist_to_rect(cur[0], cur[1], rect):.0f}px", True))
        rows.append((f"state {getattr(mouse, 'state', '?')}",
                     getattr(mouse, "state", "") == "captured"))
        rows.append((f"escape {getattr(mouse, 'escape_frac', 0.0) * 100:.0f}%"
                     f" of {getattr(mouse.hook, 'escape_px', 0):.0f}px",
                     True))
        rows.append((f"grip {(1.0 - scale) * 100:.0f}%  (speed x{scale:.2f})",
                     scale < 1.0))
    else:
        rows.append(("no target near the cursor", False))
    # Lazily-built accessibility trees (Chrome's tab strip) only appear once
    # they have been nudged awake, so show how that is going — and list what
    # the finder has actually seen, which is the difference between "the
    # magnet won't grab this" and "the magnet never found it".
    if finder is not None:
        awake, pending = finder.waker.stats()
        rows.append((f"a11y woken {awake}, retrying {pending}", awake > 0))
        rows.append(("SEEN RECENTLY", None))
        if finder.recent:
            for kind, name, rw, rh, tier in list(finder.recent)[-6:]:
                text = f"{kind} {rw}x{rh} {tier}"
                if name:
                    text += f" '{name[:16]}'"
                rows.append((text, True))
        else:
            rows.append(("nothing yet", False))
    y = 124
    for text, ok in rows:
        cv2.putText(frame, text, (w - 300, y), F, 0.45 if ok is None else 0.42,
                    HDR if ok is None else (GO if ok else NO), 1, cv2.LINE_AA)
        y += 16


def draw_gesture_debug(frame, info, cfg):
    """Press D: a live readout of every gate the fist-armed gestures must
    pass, so a gesture that won't fire tells you WHY instead of leaving you
    to guess. Green = satisfied right now, grey = this is what's blocking."""
    fi = cfg.get("fist", {})
    sw = cfg.get("swipe", {})
    fl = cfg.get("flick_down", {})
    curl = info.get("fist_curl")
    n_ext = info.get("fist_ext", 0)
    sm = info.get("swipe_m") or {}
    fm = info.get("flick_m") or {}
    F = cv2.FONT_HERSHEY_SIMPLEX
    GO, NO, HDR = (120, 240, 120), (150, 150, 150), (0, 220, 255)

    br = cfg.get("brake", {})
    bm = info.get("brake_m") or {}
    rows = [
        # The brake is proportional, so its two thresholds are the ones most
        # worth reading off a real hand: onset is where slowing starts, full
        # is where it bottoms out.
        ("BRAKE  (squeeze M+R+P)", None),
        (f"pose {'held' if bm.get('posed') else 'no'} "
         f"(index out, middle in)", bool(bm.get("posed"))),
        (f"curl {bm.get('curl', 0.0):+.2f}  onset {br.get('onset', 0.15)}"
         f"  full {br.get('full', 0.75)}",
         bm.get("curl", 0.0) >= br.get("onset", 0.15)),
        (f"amount {bm.get('amount', 0.0) * 100:.0f}%  ->  speed x"
         f"{bm.get('scale', 1.0):.2f}", bm.get("amount", 0.0) > 0.02),
        ("GRAB  (D hides this)", None),
        (f"curl {curl:.2f} < {fi.get('down_ratio', 1.30)}" if curl is not None
         else "curl --",
         curl is not None and curl < fi.get("down_ratio", 1.30)),
        (f"fingers up {n_ext} = 0", n_ext == 0),
        (f"ARMED {'YES' if info.get('fist_armed') else 'no'}",
         bool(info.get("fist_armed"))),
        ("SWIPE left/right needs", None),
        (f"travel {sm.get('travel', 0):.2f} >= {sw.get('hand_widths', 1.8)}",
         sm.get("travel", 0) >= sw.get("hand_widths", 1.8)),
        (f"speed {sm.get('speed', 0):.1f} >= {sw.get('min_speed_hw_s', 5.0)}",
         sm.get("speed", 0) >= sw.get("min_speed_hw_s", 5.0)),
        (f"armed {sm.get('armed', 0):.2f} >= {sw.get('armed_frac', 0.6)}",
         sm.get("armed", 0) >= sw.get("armed_frac", 0.6)),
        ("PUSH DOWN needs", None),
        (f"travel {fm.get('travel', 0):.2f} >= {fl.get('hand_widths', 0.9)}",
         fm.get("travel", 0) >= fl.get("hand_widths", 0.9)),
        (f"speed {fm.get('speed', 0):.1f} >= {fl.get('min_speed_hw_s', 4.5)}",
         fm.get("speed", 0) >= fl.get("min_speed_hw_s", 4.5)),
        (f"armed {fm.get('armed', 0):.2f} >= {fl.get('armed_frac', 0.6)}",
         fm.get("armed", 0) >= fl.get("armed_frac", 0.6)),
        ("LANDING - settle to fire" if info.get("flick_landing")
         else "landing --", bool(info.get("flick_landing"))),
    ]
    y = 124
    for text, ok in rows:
        cv2.putText(frame, text, (12, y), F, 0.45 if ok is None else 0.42,
                    HDR if ok is None else (GO if ok else NO), 1, cv2.LINE_AA)
        y += 16


def draw_offhand_hud(frame, launcher, commands, labels=()):
    """Bottom-left legend of the 4 left-hand launcher slots + hold progress."""
    h = frame.shape[0]
    x0, y0 = 12, h - 92
    cv2.putText(frame, "L-hand launcher", (x0, y0),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
    for i, name in enumerate(SLOT_LABELS):
        yy = y0 + 17 + i * 15
        held = launcher.current == i
        cmd = commands[i] if i < len(commands) else ""
        nice = labels[i] if i < len(labels) and labels[i] else ""
        label = nice or (os.path.basename(cmd) if cmd else "(unset)")
        color = (0, 220, 0) if held else (140, 140, 140)
        cv2.putText(frame, f"{name}: {label}", (x0, yy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    if launcher.current is not None and 0.0 < launcher.progress < 1.0:
        cv2.rectangle(frame, (x0 + 118, y0 - 8), (x0 + 118 + int(80 * launcher.progress),
                      y0 - 2), (0, 220, 0), -1)


def draw_swipe_flash(frame, info, now):
    """Brief on-screen confirmation after a swipe / down-flick fires."""
    ls = info.get("last_swipe")
    if not ls or now - ls[1] > 0.6:
        return
    w = frame.shape[1]
    txt = {"forward": "<<  FORWARD", "back": "BACK  >>",
           "minimize": "MINIMIZE  v"}.get(ls[0], ls[0])
    (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
    cv2.putText(frame, txt, ((w - tw) // 2, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 220, 0), 2, cv2.LINE_AA)


def read_launcher_commands(path: str = CONFIG_PATH):
    """Read the 4 launcher commands fresh from disk (so settings-panel edits
    take effect without a restart)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cmds = data.get("launcher", {}).get("commands", [])
        return cmds if isinstance(cmds, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def read_launcher_labels(path: str = CONFIG_PATH):
    """Friendly slot names ("Steam") written by the settings panel, so the
    HUD shows the app instead of a raw path or URI."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        labels = data.get("launcher", {}).get("labels", [])
        return labels if isinstance(labels, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_motion_settings(path, sensitivity, edge_multiplier, radius_scale,
                         scroll_gain=None):
    """Persist the live-tuned sensitivity sliders back to config.json."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data["sensitivity"] = round(sensitivity, 2)
    data["edge_multiplier"] = round(edge_multiplier, 2)
    data["radius_scale"] = round(radius_scale, 2)
    if scroll_gain is not None:
        data.setdefault("scroll", {})
        data["scroll"]["gain_notches_s"] = round(scroll_gain, 1)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description="AirMouse gesture mouse")
    ap.add_argument("--camera", type=int, default=None,
                    help="camera index (overrides config.json)")
    ap.add_argument("--no-mouse", action="store_true",
                    help="run without sending real mouse input")
    ap.add_argument("--selftest", type=float, default=0,
                    help="run N seconds, save a screenshot, then exit (for testing)")
    ap.add_argument("--shot", default=SCREENSHOT_PATH,
                    help="screenshot path for S key / selftest")
    ap.add_argument("--settings", action="store_true",
                    help="open the settings window instead of the tracker")
    ap.add_argument("--tutorial", action="store_true",
                    help="replay the first-run walkthrough, then exit")
    ap.add_argument("--no-tutorial", action="store_true",
                    help="skip the walkthrough even on a first run")
    args = ap.parse_args()

    if args.settings:
        import settings_app
        return settings_app.main() or 0

    cfg = load_config()

    if args.tutorial:
        import settings_store
        import tutorial
        if tutorial.run(cfg):
            settings_store.save_updates({"tutorial_done": True})
        return 0
    cam_index = args.camera if args.camera is not None else cfg["camera_index"]

    # First run: teach the gestures before anything else. The walkthrough
    # holds the webcam while it is open — only one program can — so it has to
    # finish and release it before the tracker opens the camera below.
    if not cfg.get("tutorial_done", False) and not args.no_tutorial:
        try:
            import settings_store
            import tutorial
            if tutorial.run(cfg):
                settings_store.save_updates({"tutorial_done": True})
        except Exception as exc:                  # never block startup on it
            print(f"WARNING: walkthrough failed to open ({exc})")
        cfg = load_config()

    cap = open_camera(cam_index)
    if not cap.isOpened():
        applog.fatal("camera", f"ERROR: cannot open camera index {cam_index}")
        return 1

    low_light = False
    if cfg["low_light_auto"]:
        print("calibrating camera...")
        cam_fps, low_light = ensure_framerate(cap)
        if cam_fps <= 0:
            # camera opened but produces no frames (often: another app is
            # using it, or the driver wedged) — reopen once before giving up
            print("camera produced no frames; reopening...")
            cap.release()
            time.sleep(1.0)
            cap = open_camera(cam_index)
            cam_fps, low_light = ensure_framerate(cap)
            if cam_fps <= 0:
                applog.fatal("camera", "ERROR: camera is not delivering "
                             "frames. Is another app (Zoom/Teams/browser) "
                             "using the webcam?")
                cap.release()
                return 1
        print(f"camera delivering {cam_fps:.1f} fps"
              + (" (low-light mode: manual exposure)" if low_light else ""))
        # A camera capped below 30 by its own software is felt as a jagged,
        # laggy pointer rather than recognised as a frame-rate problem, and
        # the fix is in the webcam's control panel where nobody would think
        # to look. Say so plainly, once, and carry on running.
        if 0 < cam_fps < 27.0:
            applog.dialog(
                f"Your webcam is only delivering {cam_fps:.0f} frames per "
                "second.\n\nAirMouse needs 30 for smooth pointer control — "
                "below that the cursor feels jagged and gestures are harder "
                "to land.\n\nOpen your webcam's own software (Logitech G HUB, "
                "Camera Settings, etc.) and set the frame rate to 30 fps.",
                title="AirMouse — camera is running slow", warning=True)

    tracker = HandTracker(MODEL_PATH)
    real_mouse = NullMouse() if args.no_mouse else Mouse()
    null_mouse = NullMouse()

    # Cursor magnetism wraps the mouse: clickable things get sticky as the
    # cursor nears them. The target hunt runs on its own thread so a Win32
    # or accessibility query can never stall the 30fps capture loop.
    mag_cfg = cfg.get("magnet", {})
    mp_ = resolve_params(mag_cfg)
    magnet_finder = TargetFinder(
        hz=mag_cfg.get("poll_hz", 12.0), reach_px=mp_["reach_px"],
        use_msaa=mag_cfg.get("use_msaa", True),
        use_uia=mag_cfg.get("use_uia", True),
        include_text_fields=mp_["include_text_fields"]).start()
    real_mouse = MagnetMouse(
        real_mouse, magnet_finder, enabled=mp_["enabled"],
        strength=mp_["strength"], reach_px=mp_["reach_px"], pull=mp_["pull"],
        capture_radius_px=mp_["capture_radius_px"],
        escape_px=mp_["escape_px"], refractory_s=mp_["refractory_s"])
    magnet_mouse = real_mouse
    print("magnetism " + ("ON " + ("(custom tuning)" if mp_["custom"]
                                   else "(full strength)")
                          if mp_["enabled"] else "off"))
    controller = make_controller(real_mouse, cfg)
    paused = False

    # Hand routing: cursor (dominant) hand -> controller; off hand ->
    # launcher. The off hand NEVER drives the pointer, so it works on its own,
    # and a lone hand is identified by a sustained handedness vote rather than
    # by one frame's label. Until that vote is sure the hand does nothing.
    assigner = RoleAssigner(
        dominant=cfg.get("dominant_hand", "right"),
        launcher_cooldown_s=cfg.get("roles", {}).get("launcher_cooldown_s", 1.0))
    lcfg = cfg.get("launcher", {})
    launcher = FingerLauncher(hold_s=lcfg.get("hold_s", 0.3),
                              cooldown_s=lcfg.get("cooldown_s", 1.0))
    launcher_cmds = read_launcher_commands()
    launcher_labels = read_launcher_labels()

    # Attention gate: only control the mouse while looking at the screen.
    attention = make_attention(cfg)
    face_tracker = None
    if attention is not None:
        if os.path.exists(FACE_MODEL_PATH):
            from face_tracker import FaceTracker
            face_tracker = FaceTracker(FACE_MODEL_PATH)
            print("attention gate ON (look at the screen to control; "
                  "C recalibrates neutral, A toggles the gate)")
        else:
            print(f"WARNING: {os.path.basename(FACE_MODEL_PATH)} missing; "
                  "attention gate disabled (control always on)")
            attention = None
    detect_every_n = max(1, int(cfg["attention"].get("detect_every_n", 2)))
    frame_i = 0
    _paused_attention = None   # holds the gate while toggled off with 'A'

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
    lock_preview_window(WINDOW)
    if not cfg.get("show_preview", True):
        # Minimised, not absent: the window still carries the keyboard
        # commands and the tuning sliders, it just doesn't open over your
        # work. The taskbar brings it back when wanted.
        minimize_window_by_title(WINDOW)
    # Live sensitivity sliders (x10 so they carry one decimal). cv2 trackbars
    # start at 0, so values are clamped when read.
    cv2.createTrackbar("base x10", WINDOW, int(controller.sensitivity * 10), 300, lambda v: None)
    cv2.createTrackbar("edge x10", WINDOW, int(controller.edge_multiplier * 10), 80, lambda v: None)
    cv2.createTrackbar("radius x10", WINDOW, int(controller.radius_scale * 10), 80, lambda v: None)
    # scroll gain is its own knob (notches/s per hand-size) and NEVER touches
    # cursor sensitivity — scroll and pointer tune independently
    cv2.createTrackbar("scroll", WINDOW, int(controller.scroll_gain_notches_s), 100, lambda v: None)

    show_debug = False          # D toggles the gesture-gate readout
    fps_avg = 0.0
    t_prev = time.perf_counter()
    t_start = t_prev
    last_state = None

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("Camera read failed")
            break

        frame = cv2.flip(frame, 1)  # mirror so it behaves like a mirror
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        tracker.detect_async(rgb)

        now = time.perf_counter()

        # Attention gate — head pose says whether the user faces the screen.
        # Face pose moves slowly, so we only sample every Nth frame.
        attending = True
        if attention is not None and face_tracker is not None:
            if frame_i % detect_every_n == 0:
                face_tracker.detect_async(rgb)
            attending = attention.update(face_tracker.latest(), now)
        frame_i += 1

        # live sensitivity sliders
        controller.sensitivity = max(0.5, cv2.getTrackbarPos("base x10", WINDOW) / 10.0)
        controller.edge_multiplier = max(1.0, cv2.getTrackbarPos("edge x10", WINDOW) / 10.0)
        controller.radius_scale = max(0.5, cv2.getTrackbarPos("radius x10", WINDOW) / 10.0)
        controller.scroll_gain_notches_s = float(max(1, cv2.getTrackbarPos("scroll", WINDOW)))

        # detect both hands, split into cursor (right) + off (left) hand
        hand_list = []
        for hd in tracker.latest_hands():
            hpts = to_pixel_points(hd["landmarks"], w, h)
            hand_list.append({"pts": hpts, "label": hd["label"], "score": hd["score"]})
        roles = assigner.assign(hand_list, w, now)
        cursor_pts, off_pts = roles["cursor"], roles["off"]
        for hd in hand_list:
            is_cursor = hd["pts"] is cursor_pts
            # '?' = seen but not yet identified; it controls nothing until the
            # handedness vote settles, which takes about a fifth of a second.
            tag = ("R" if hd["pts"] is roles["right"]
                   else "L" if hd["pts"] is roles["left"] else "?")
            colour = ((0, 200, 0) if is_cursor
                      else (255, 160, 0) if tag != "?" else (140, 140, 140))
            draw_landmarks(frame, hd["pts"], color=colour)
            wx, wy = hd["pts"][WRIST]
            cv2.putText(frame, tag, (wx + 10, wy + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA)

        # A hand is up but it isn't the cursor hand: freeze the pointer and
        # keep it engaged rather than letting it time out, so reaching for a
        # launcher slot doesn't cost a fresh engage when you come back.
        info = controller.update(cursor_pts, (w, h), now, attending=attending,
                                 suspend=cursor_pts is None and bool(hand_list))

        # Left-hand finger launcher — only while attending, only once the off
        # hand has been stably present past the role cooldown, and only while
        # that hand is actually RAISED. From a couple of metres back the
        # camera reads fingers perfectly well on a hand resting on a knee,
        # and counting those launched apps while the user sat still.
        off_ok = (attending and assigner.off_ready(now)
                  and off_pts is not None and hand_raised(off_pts, h))
        fired = launcher.update(off_pts if off_ok else None, now)
        if fired is not None:
            cmds = read_launcher_commands()
            cmd = cmds[fired] if fired < len(cmds) else ""
            if cmd:
                print(f"launcher: {fired + 1} finger(s) -> {cmd}")
                run_launch_command(cmd)
            else:
                print(f"launcher: {fired + 1} finger(s) unset (press B to configure)")
        if frame_i % 30 == 0:
            launcher_cmds = read_launcher_commands()
            launcher_labels = read_launcher_labels()
            # Magnet and brake settings apply live — tuning either with a
            # restart in between made it impossible to tell whether a change
            # did anything, which cost a whole session once already.
            live_cfg = load_config()
            fresh = resolve_params(live_cfg.get("magnet", {}))
            magnet_mouse.apply_params(
                enabled=fresh["enabled"], strength=fresh["strength"],
                reach_px=fresh["reach_px"], pull=fresh["pull"],
                capture_radius_px=fresh["capture_radius_px"],
                escape_px=fresh["escape_px"],
                refractory_s=fresh["refractory_s"])
            magnet_finder.reach_px = fresh["reach_px"]
            magnet_finder.include_text_fields = fresh["include_text_fields"]
            controller.apply_brake_params(live_cfg.get("brake"))

        if cursor_pts and info.get("mode") == POINT:
            # pinch lines: thumb-index (left click), thumb-middle (right)
            l_hot = info.get("left_owner") == "pinch"
            r_hot = info.get("right_down", False)
            cv2.line(frame, cursor_pts[THUMB_TIP], cursor_pts[INDEX_TIP],
                     (0, 170, 255) if l_hot else (120, 120, 120),
                     2 if l_hot else 1, cv2.LINE_AA)
            cv2.line(frame, cursor_pts[THUMB_TIP], cursor_pts[MIDDLE_TIP],
                     (255, 0, 255) if r_hot else (100, 100, 100),
                     2 if r_hot else 1, cv2.LINE_AA)

        if info["state"] != last_state:
            print(f"state: {last_state} -> {info['state']}")
            last_state = info["state"]

        dt, t_prev = now - t_prev, now
        if dt > 0:
            fps_avg = fps_avg * 0.9 + (1.0 / dt) * 0.1
        draw_overlay(frame, info, fps_avg, low_light, paused, attention)
        draw_offhand_hud(frame, launcher, launcher_cmds, launcher_labels)
        draw_magnet(frame, controller.mouse)
        if show_debug:
            draw_gesture_debug(frame, info, cfg)
            draw_magnet_debug(frame, controller.mouse, magnet_finder)
        draw_swipe_flash(frame, info, now)

        cv2.imshow(WINDOW, frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):
            break
        if key in (ord("p"), ord("P")):
            paused = not paused
            controller._to_idle()   # releases any held button safely
            controller.mouse = null_mouse if paused else real_mouse
            print("control PAUSED" if paused else "control RESUMED")
        if key in (ord("d"), ord("D")):
            show_debug = not show_debug
            print("gesture debug " + ("ON" if show_debug else "OFF"))
        if key in (ord("s"), ord("S")):
            cv2.imwrite(args.shot, frame)
            print(f"screenshot -> {args.shot}")
        if key in (ord("b"), ord("B")):
            import subprocess
            # Frozen builds have no .py to hand to an interpreter: the exe
            # re-launches itself with --settings instead.
            if FROZEN:
                cmd = [sys.executable, "--settings"]
            else:
                cmd = [sys.executable,
                       os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "settings_app.py")]
            subprocess.Popen(cmd)
            print("settings opened (launcher changes apply live; motion and "
                  "gesture changes need a restart)")
        if key in (ord("c"), ord("C")) and attention is not None:
            if attention.present and attention.yaw is not None:
                attention.set_neutral(attention.yaw, attention.pitch)
                ok_save = save_neutral(CONFIG_PATH, attention.yaw, attention.pitch)
                print(f"calibrated neutral: yaw {attention.yaw:.1f} "
                      f"pitch {attention.pitch:.1f}"
                      + ("" if ok_save else " (couldn't save to config)"))
            else:
                print("calibrate: look at the screen so your face is visible, then press C")
        if key in (ord("a"), ord("A")) and face_tracker is not None:
            if attention is None:
                attention = _paused_attention
            else:
                _paused_attention, attention = attention, None
            print("attention gate " + ("OFF (control always on)"
                                        if attention is None else "ON"))
        if args.selftest and now - t_start >= args.selftest:
            cv2.imwrite(args.shot, frame)
            print(f"selftest screenshot -> {args.shot}  (fps ~{fps_avg:.1f}, state {info['state']})")
            break

    controller._to_idle()   # release any held button on exit
    # remember the live-tuned sliders for next launch
    save_motion_settings(CONFIG_PATH, controller.sensitivity,
                         controller.edge_multiplier, controller.radius_scale,
                         controller.scroll_gain_notches_s)
    if magnet_finder is not None:
        magnet_finder.stop()
    cap.release()
    tracker.close()
    if face_tracker is not None:
        face_tracker.close()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    # There is no console any more, so nothing may fail silently: everything
    # printed lands in the log, and anything that stops the app from starting
    # raises a dialog that says so in words rather than leaving the user with
    # a window that simply never appeared.
    applog.start(APP_VERSION)
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:                      # noqa: BLE001 - last resort
        applog.fatal(exc=exc)
        sys.exit(1)

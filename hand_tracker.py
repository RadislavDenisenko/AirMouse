"""MediaPipe Hand Landmarker wrapper — modern tasks API, live-stream mode."""

import threading
import time

import mediapipe as mp
import cv2
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# 21-landmark hand topology (indices per MediaPipe hand model)
WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5    # index-finger base knuckle
INDEX_TIP = 8
MIDDLE_MCP = 9   # middle-finger base knuckle
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_MCP = 17   # pinky base knuckle
PINKY_TIP = 20

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),                    # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),                    # index
    (5, 9), (9, 10), (10, 11), (11, 12),               # middle
    (9, 13), (13, 14), (14, 15), (15, 16),             # ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),   # pinky + palm edge
]


class HandTracker:
    """Async hand landmarker. Feed frames with detect_async(); read latest()."""

    def __init__(self, model_path: str, num_hands: int = 2):
        self._lock = threading.Lock()
        self._result = None
        self._result_ts_ms = 0
        self._last_sent_ts = -1
        self._t0 = time.monotonic()

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.LIVE_STREAM,
            num_hands=num_hands,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            result_callback=self._on_result,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def _on_result(self, result, output_image, timestamp_ms):
        with self._lock:
            self._result = result
            self._result_ts_ms = timestamp_ms

    def detect_async(self, frame_rgb):
        """Submit an RGB frame. Non-blocking; result arrives via callback."""
        ts = int((time.monotonic() - self._t0) * 1000)
        if ts <= self._last_sent_ts:  # timestamps must strictly increase
            ts = self._last_sent_ts + 1
        self._last_sent_ts = ts
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        self._landmarker.detect_async(mp_image, ts)

    def latest(self):
        """Return (landmarks_of_first_hand or None, result_timestamp_ms).
        Kept for single-hand callers; prefer latest_hands()."""
        with self._lock:
            result, ts = self._result, self._result_ts_ms
        if result and result.hand_landmarks:
            return result.hand_landmarks[0], ts
        return None, ts

    def latest_hands(self):
        """Return a list of dicts, one per detected hand:
        {'landmarks': [...], 'label': 'Left'|'Right', 'score': float}.
        The label is MediaPipe's raw handedness — NOTE it is INVERTED for our
        flip-then-detect pipeline ('Right' = the user's left hand); hand_roles
        owns interpreting it, and leans on position since the label also flips
        when hands overlap."""
        with self._lock:
            result = self._result
        if not result or not result.hand_landmarks:
            return []
        hands = []
        for i, lms in enumerate(result.hand_landmarks):
            label, score = "", 0.0
            try:
                cat = result.handedness[i][0]
                label, score = cat.category_name, cat.score
            except (IndexError, AttributeError):
                pass
            hands.append({"landmarks": lms, "label": label, "score": score})
        return hands

    def close(self):
        self._landmarker.close()


def to_pixel_points(landmarks, w, h):
    """Convert normalized landmarks to integer pixel points."""
    return [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]


def draw_landmarks(frame_bgr, landmarks, color=(0, 200, 0),
                   joint_color=(0, 80, 255)):
    """Draw the hand skeleton on a BGR frame. Accepts raw MediaPipe
    landmarks or already-converted pixel points. Returns pixel points."""
    h, w = frame_bgr.shape[:2]
    if landmarks and hasattr(landmarks[0], "x"):
        pts = to_pixel_points(landmarks, w, h)
    else:
        pts = landmarks
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame_bgr, pts[a], pts[b], color, 2, cv2.LINE_AA)
    for i, p in enumerate(pts):
        r = 5 if i in (THUMB_TIP, INDEX_TIP) else 3
        cv2.circle(frame_bgr, p, r, joint_color, -1, cv2.LINE_AA)
    return pts

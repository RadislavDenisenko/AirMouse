"""MediaPipe Face Landmarker wrapper — tasks API, live-stream mode.

Used only to answer one question: "is the user looking at the screen?"
It exposes head pose (yaw/pitch/roll, degrees) from the facial transformation
matrix and an eye-blink score from the blendshapes. Same async design as
HandTracker so it drops into the existing capture loop.
"""

import math
import threading
import time

import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


def head_pose_deg(matrix):
    """Yaw/pitch/roll in degrees from a 4x4 canonical-face->camera matrix.

    Angles are ~0 when the face points straight at the camera and grow as the
    head turns. Signs/axis labels don't need to be exact: the attention gate
    thresholds each axis symmetrically around a (calibratable) neutral, so a
    flipped sign or a yaw/pitch swap still gates correctly.
    """
    M = np.asarray(matrix, dtype=float).reshape(4, 4)
    R = M[:3, :3]
    fwd = R @ np.array([0.0, 0.0, -1.0])   # where the face is pointing
    up = R @ np.array([0.0, -1.0, 0.0])    # head "up" (for roll)
    depth = math.hypot(fwd[0], fwd[2]) or 1e-6
    yaw = math.degrees(math.atan2(fwd[0], abs(fwd[2]) or 1e-6))
    pitch = math.degrees(math.atan2(fwd[1], depth))
    roll = math.degrees(math.atan2(up[0], abs(up[1]) or 1e-6))
    return yaw, pitch, roll


def eye_blink_score(blendshapes):
    """Mean of eyeBlinkLeft/eyeBlinkRight in [0, 1] (1 = fully closed), or
    None if the model didn't report them."""
    if not blendshapes:
        return None
    vals = [c.score for c in blendshapes
            if c.category_name in ("eyeBlinkLeft", "eyeBlinkRight")]
    return sum(vals) / len(vals) if vals else None


class FaceSignals:
    """Parsed, cheap-to-carry face read for one frame."""
    __slots__ = ("present", "pose", "blink", "ts_ms")

    def __init__(self, present, pose, blink, ts_ms):
        self.present = present      # bool
        self.pose = pose            # (yaw, pitch, roll) deg or None
        self.blink = blink          # float [0,1] or None
        self.ts_ms = ts_ms


class FaceTracker:
    """Async face landmarker. Feed frames with detect_async(); read latest().

    Feed only every Nth frame if you like — head pose moves slowly, and the
    last read persists until a newer one arrives, so throttling just saves CPU.
    """

    def __init__(self, model_path: str, num_faces: int = 1):
        self._lock = threading.Lock()
        self._signals = FaceSignals(False, None, None, 0)
        self._last_sent_ts = -1
        self._t0 = time.monotonic()

        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.LIVE_STREAM,
            num_faces=num_faces,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            result_callback=self._on_result,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def _on_result(self, result, output_image, timestamp_ms):
        present = bool(result.face_landmarks)
        pose = blink = None
        if present:
            mats = result.facial_transformation_matrixes
            if mats:
                pose = head_pose_deg(mats[0])
            bs = result.face_blendshapes
            blink = eye_blink_score(bs[0]) if bs else None
        with self._lock:
            self._signals = FaceSignals(present, pose, blink, timestamp_ms)

    def detect_async(self, frame_rgb):
        """Submit an RGB frame. Non-blocking; result arrives via callback."""
        ts = int((time.monotonic() - self._t0) * 1000)
        if ts <= self._last_sent_ts:  # timestamps must strictly increase
            ts = self._last_sent_ts + 1
        self._last_sent_ts = ts
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        self._landmarker.detect_async(mp_image, ts)

    def latest(self) -> FaceSignals:
        with self._lock:
            return self._signals

    def close(self):
        self._landmarker.close()

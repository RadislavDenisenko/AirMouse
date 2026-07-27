"""Attention gate — decides whether the user is looking at the screen.

The gesture controller only drives the mouse while this says "attending", so
you can wave your hands around, talk to someone, or turn away without moving
the cursor by accident.

Signal (in priority order):
  1. Face present at all. No face -> not attending (you walked off / turned
     right around). This alone kills most accidental control.
  2. Head facing the screen: |yaw| and |pitch| within thresholds of a neutral
     pose. Neutral defaults to straight-at-camera (0, 0) and can be calibrated
     to wherever you actually sit ("look at the screen, press C"). Thresholds
     are deliberately generous: looking at the edge of a monitor still counts
     as facing it — only clearly turning away should drop control.
  3. (optional) Eyes open: sustained eye closure past `eyes_closed_s` counts as
     not attending. Off by default — blinks and glasses make it twitchy, and
     looking down already trips the pitch threshold.

Raw attention is debounced with short on/off grace windows so a dropped frame
or a quick head bob doesn't flicker control on and off.
"""


class AttentionGate:
    def __init__(self, yaw_thresh_deg: float = 35.0,
                 pitch_thresh_deg: float = 25.0, exit_margin_deg: float = 10.0,
                 smooth_alpha: float = 0.4,
                 require_eyes_open: bool = False,
                 eyes_closed_s: float = 0.8, blink_thresh: float = 0.55,
                 on_grace_s: float = 0.15, off_grace_s: float = 0.60,
                 neutral_yaw: float = 0.0, neutral_pitch: float = 0.0):
        self.yaw_thresh_deg = yaw_thresh_deg      # inner: within = facing
        self.pitch_thresh_deg = pitch_thresh_deg
        self.exit_margin_deg = exit_margin_deg    # must exceed inner+margin to stop
        self.smooth_alpha = smooth_alpha
        self.require_eyes_open = require_eyes_open
        self.eyes_closed_s = eyes_closed_s
        self.blink_thresh = blink_thresh
        self.on_grace_s = on_grace_s
        self.off_grace_s = off_grace_s
        self.neutral_yaw = neutral_yaw
        self.neutral_pitch = neutral_pitch

        # debounced output: start OFF, so control stays off until we've
        # actually confirmed the user is looking.
        self.attending = False
        self._raw = False
        self._raw_since = None
        self._eyes_closed_t0 = None
        self._facing_latched = False   # angular hysteresis state
        self._yaw_s = None             # EMA-smoothed pose
        self._pitch_s = None

        # last read, for the overlay (smoothed)
        self.present = False
        self.yaw = None
        self.pitch = None
        self.roll = None
        self.blink = None
        self.facing = False
        self.eyes_ok = True

    def set_neutral(self, yaw: float, pitch: float):
        """Calibrate the straight-at-screen pose to where the user sits."""
        self.neutral_yaw = yaw
        self.neutral_pitch = pitch

    def update(self, signals, now) -> bool:
        """Feed one FaceSignals (or None) and the current time.
        Returns the debounced `attending` boolean."""
        present = bool(signals and signals.present)
        self.present = present
        pose = signals.pose if signals else None
        blink = signals.blink if signals else None

        raw = False
        if present and pose is not None:
            yaw, pitch, self.roll = pose
            # EMA-smooth yaw/pitch before thresholding (kills landmark jitter)
            a = self.smooth_alpha
            self._yaw_s = yaw if self._yaw_s is None else self._yaw_s + a * (yaw - self._yaw_s)
            self._pitch_s = pitch if self._pitch_s is None else self._pitch_s + a * (pitch - self._pitch_s)
            self.yaw, self.pitch = self._yaw_s, self._pitch_s

            dy = abs(self._yaw_s - self.neutral_yaw)
            dp = abs(self._pitch_s - self.neutral_pitch)
            # Schmitt hysteresis: enter facing within the inner threshold, but
            # only leave once past inner+margin, so a head hovering at the edge
            # doesn't flicker control on and off.
            out_y = self.yaw_thresh_deg + self.exit_margin_deg
            out_p = self.pitch_thresh_deg + self.exit_margin_deg
            if self._facing_latched:
                self._facing_latched = not (dy > out_y or dp > out_p)
            else:
                self._facing_latched = dy <= self.yaw_thresh_deg and dp <= self.pitch_thresh_deg
            self.facing = self._facing_latched

            self.eyes_ok = True
            self.blink = blink
            if self.require_eyes_open and blink is not None:
                if blink > self.blink_thresh:
                    if self._eyes_closed_t0 is None:
                        self._eyes_closed_t0 = now
                    if now - self._eyes_closed_t0 >= self.eyes_closed_s:
                        self.eyes_ok = False
                else:
                    self._eyes_closed_t0 = None
            raw = self.facing and self.eyes_ok
        else:
            # no face, or face without a usable pose -> not attending.
            # Reset smoothing/latch so re-acquisition starts clean.
            self.facing = self._facing_latched = False
            self._yaw_s = self._pitch_s = None
            if not present:
                self.yaw = self.pitch = self.roll = self.blink = None
            self._eyes_closed_t0 = None

        # debounce: raw must hold for on/off grace before flipping the output
        if raw != self._raw:
            self._raw = raw
            self._raw_since = now
        grace = self.on_grace_s if raw else self.off_grace_s
        if self._raw_since is not None and now - self._raw_since >= grace:
            self.attending = raw
        return self.attending

    def status(self) -> str:
        """One-word reason, for the overlay."""
        if self.attending:
            return "LOOKING"
        if not self.present:
            return "NO FACE"
        if not self.eyes_ok:
            return "EYES CLOSED"
        return "LOOKING AWAY"

"""Assign detected hands to roles: 'cursor' (dominant) and 'off' (other).

We flip the frame before inference (selfie/mirror convention), so in the
processed image the user's RIGHT hand sits at higher x. With TWO hands
visible, x-order is the robust cue, with a frame-to-frame identity freeze
for the moment they cross/overlap (labels and x-order both lie then).

v3.3 policy (user-chosen): a LONE hand is ALWAYS the cursor hand. The old
code seeded a lone hand's side from its FIRST frame — the blurriest, least
reliable one — and then latched that guess until the hand left the frame,
which is exactly how the user's raised right hand got stuck as "left" and
stopped driving the cursor. There is no guess to get wrong anymore: one
hand up = you're pointing; the launcher simply requires both hands visible.

Safety net for the launcher: `off_since` records when the current off-hand
acquisition began, and `off_ready()` holds the launcher back for a cooldown
after any role change — a hand that only just became "off" (or just entered
the frame) can't insta-fire a launch slot.

NOTE on MediaPipe's handedness label: in this flip-then-detect pipeline it
is INVERTED ("Right" = the user's LEFT hand; confirmed live 2026-07-23). It
is no longer used for role decisions, but keep the inversion in mind before
reintroducing it anywhere.
"""

import math

from hand_tracker import WRIST, MIDDLE_MCP


def _cx(pts):
    return 0.5 * (pts[WRIST][0] + pts[MIDDLE_MCP][0])


def _cy(pts):
    return 0.5 * (pts[WRIST][1] + pts[MIDDLE_MCP][1])


def _center(pts):
    return (_cx(pts), _cy(pts))


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class RoleAssigner:
    """Feed the per-frame hand list; get {'cursor', 'off', 'right', 'left'}.

    `dominant` = which physical hand drives the cursor ('right' by default;
    set 'left' for a left-handed user). `overlap_frac` is the horizontal gap
    (as a fraction of frame width) below which two hands count as
    overlapping and roles are frozen by identity. `launcher_cooldown_s` is
    how long a newly-acquired off hand must be continuously visible before
    `off_ready()` lets the launcher fire.
    """

    def __init__(self, dominant: str = "right", overlap_frac: float = 0.12,
                 launcher_cooldown_s: float = 1.0):
        self.dominant = dominant
        self.overlap_frac = overlap_frac
        self.launcher_cooldown_s = launcher_cooldown_s
        self._last = {"right": None, "left": None}   # last center per side
        self.off_since = None       # when the current off hand appeared

    def _roles(self, right_pts, left_pts):
        if self.dominant == "left":
            cursor, off = left_pts, right_pts
        else:
            cursor, off = right_pts, left_pts
        return {"cursor": cursor, "off": off,
                "right": right_pts, "left": left_pts}

    def off_ready(self, now) -> bool:
        """True once the off hand has been continuously present long enough
        that a role mixup can't have just happened."""
        return (self.off_since is not None
                and now - self.off_since >= self.launcher_cooldown_s)

    def assign(self, hands, frame_w, now: float = 0.0):
        """hands: list of {'pts', 'label', 'score'}. Returns the role dict."""
        n = len(hands)
        if n == 0:
            self._last = {"right": None, "left": None}
            self.off_since = None
            return self._roles(None, None)

        if n >= 2:
            ordered = sorted(hands, key=lambda h: _cx(h["pts"]))
            left_h, right_h = ordered[0], ordered[-1]   # extremes if >2
            sep = _cx(right_h["pts"]) - _cx(left_h["pts"])
            if (sep < self.overlap_frac * frame_w
                    and self._last["right"] and self._last["left"]):
                # crossing/overlapping: keep identity, don't trust x order
                m = self._match_two(left_h["pts"], right_h["pts"])
                right_pts, left_pts = m["right"], m["left"]
            else:
                right_pts, left_pts = right_h["pts"], left_h["pts"]
            self._last["right"] = _center(right_pts)
            self._last["left"] = _center(left_pts)
            if self.off_since is None:
                self.off_since = now   # off hand just appeared: start cooldown
            return self._roles(right_pts, left_pts)

        # Single hand: it is the cursor hand, full stop. No label, no
        # position guess, no latch — nothing to be wrong about.
        pts = hands[0]["pts"]
        side = self.dominant if self.dominant in ("right", "left") else "right"
        self._last = {"right": None, "left": None}
        self._last[side] = _center(pts)
        self.off_since = None          # no off hand -> cooldown re-arms
        return self._roles(pts if side == "right" else None,
                           pts if side == "left" else None)

    def _match_two(self, a_pts, b_pts):
        ca, cb = _center(a_pts), _center(b_pts)
        lr, ll = self._last["right"], self._last["left"]
        cost_ar = _d(ca, lr) + _d(cb, ll)   # a=right, b=left
        cost_br = _d(cb, lr) + _d(ca, ll)   # b=right, a=left
        if cost_ar <= cost_br:
            return {"right": a_pts, "left": b_pts}
        return {"right": b_pts, "left": a_pts}

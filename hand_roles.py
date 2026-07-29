"""Assign detected hands to roles: 'cursor' (dominant) and 'off' (other).

We flip the frame before inference (selfie/mirror convention), so in the
processed image the user's RIGHT hand sits at higher x. With TWO hands
visible, x-order is the robust cue, with a frame-to-frame identity freeze
for the moment they cross/overlap (labels and x-order both lie then).

v3.5 policy: **the off hand never drives the cursor.** One hand up is no
longer automatically the pointer — you can raise the launcher hand on its
own and use it, and it simply does not work as a mouse.

That only holds up if hand identity is trustworthy, and a single frame's
handedness label is not: a blurred or half-occluded hand gets labelled
confidently wrong, and acting on one such frame is how a raised right hand
once got stuck as "left" for a whole session. So identity comes from
`SideVote` — a rolling window of recent high-confidence labels — with two
properties that make the old failure impossible:

  * **it is never latched.** The verdict is recomputed from a rolling window
    every frame, so a wrong call is outvoted and corrected within ~0.2 s for
    as long as the hand is up; and a hand that leaves the frame comes back
    with no memory at all.
  * **unsure means no role.** Until the vote is confident, a lone hand is
    neither cursor nor launcher. It cannot grab the pointer by accident, and
    the caller freezes the cursor rather than disengaging, so the ~0.2 s of
    deciding costs nothing.

Safety net for the launcher: `off_since` records when the off hand was
identified, and `off_ready()` holds the launcher back for a cooldown after
that — a hand that only just became "off" can't insta-fire a launch slot.

NOTE on MediaPipe's handedness label: in this flip-then-detect pipeline it
is INVERTED ("Right" = the user's LEFT hand; confirmed live 2026-07-23).
`user_side()` is the single place that knows this.
"""

import math
from collections import deque

from hand_tracker import WRIST, MIDDLE_MCP


def user_side(label):
    """Which physical hand a MediaPipe handedness label refers to.

    The frame is mirrored before detection, so the label is INVERTED: the
    model's "Right" is the user's LEFT hand. This is a property of the
    pipeline, not a bug — don't "fix" it here.
    """
    if label == "Right":
        return "left"
    if label == "Left":
        return "right"
    return None


def _cx(pts):
    return 0.5 * (pts[WRIST][0] + pts[MIDDLE_MCP][0])


def _cy(pts):
    return 0.5 * (pts[WRIST][1] + pts[MIDDLE_MCP][1])


def _center(pts):
    return (_cx(pts), _cy(pts))


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class SideVote:
    """Sustained confidence vote on which physical hand a lone hand is.

    Pure logic, no MediaPipe: feed it a label and a score per frame, ask it
    for a side. `window` frames are kept; a verdict needs `min_votes` for one
    side with at least `ratio`x as many votes as the other, so a noisy
    disagreeing stream stays undecided instead of guessing. Frames scoring
    below `min_score` abstain rather than voting badly.

    Brief detection dropouts are ridden out — `gap(now)` only forgets the
    verdict once the hand has been away longer than `gap_s`, because
    detection blinks constantly and re-deciding on every blink would stutter.

    `blind_frames` is a last-resort escape hatch: if handedness data never
    arrives at all (every frame abstains), nothing could ever distinguish the
    hands, so rather than leave the app dead the vote falls back to calling a
    lone hand the dominant one. It cannot fire while labels are working.
    """

    def __init__(self, window=12, min_votes=6, ratio=3.0, min_score=0.6,
                 gap_s=0.3, blind_frames=24, dominant="right"):
        self.window = window
        self.min_votes = min_votes
        self.ratio = ratio
        self.min_score = min_score
        self.gap_s = gap_s
        self.blind_frames = blind_frames
        self.dominant = dominant
        self.reset()

    def reset(self):
        self._votes = deque(maxlen=self.window)
        self._side = None
        self._blind = 0
        self._last_seen = None

    def gap(self, now):
        """No lone hand this frame. Forgets everything once the hand has
        genuinely gone, so it can never come back wearing a stale verdict."""
        if self._last_seen is not None and now - self._last_seen > self.gap_s:
            self.reset()

    def update(self, label, score, now):
        """One frame of a lone hand. Returns the current verdict."""
        self.gap(now)
        self._last_seen = now
        side = user_side(label)
        if side is not None and score >= self.min_score:
            self._votes.append(side)
            self._blind = 0
        else:
            self._blind += 1
            if self._blind >= self.blind_frames and not self._votes:
                self._side = self.dominant
                return self._side

        counts = self.tally()
        for a, b in (("right", "left"), ("left", "right")):
            if counts[a] >= self.min_votes and counts[a] >= self.ratio * counts[b]:
                self._side = a
                break
        return self._side

    def tally(self):
        right = sum(1 for s in self._votes if s == "right")
        return {"right": right, "left": len(self._votes) - right}

    def side(self):
        """'right' / 'left', or None while the vote is still undecided."""
        return self._side


class RoleAssigner:
    """Feed the per-frame hand list; get {'cursor', 'off', 'right', 'left'}.

    `dominant` = which physical hand drives the cursor ('right' by default;
    set 'left' for a left-handed user) — the other one never does, in any
    situation. `overlap_frac` is the horizontal gap (as a fraction of frame
    width) below which two hands count as overlapping and roles are frozen by
    identity. `launcher_cooldown_s` is how long an identified off hand must
    be continuously present before `off_ready()` lets the launcher fire.
    """

    def __init__(self, dominant: str = "right", overlap_frac: float = 0.12,
                 launcher_cooldown_s: float = 1.0, vote=None):
        self.dominant = "left" if dominant == "left" else "right"
        self.off_side = "right" if self.dominant == "left" else "left"
        self.overlap_frac = overlap_frac
        self.launcher_cooldown_s = launcher_cooldown_s
        self.vote = vote if vote is not None else SideVote(dominant=self.dominant)
        self._last = {"right": None, "left": None}   # last center per side
        self.off_since = None       # when the off hand was identified

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
            self.vote.gap(now)
            return self._roles(None, None)

        if n >= 2:
            # Two hands: x-order decides, and it has never been wrong. The
            # lone-hand vote is cleared outright — carrying a verdict across a
            # two-hand stretch is how the surviving hand could inherit the
            # other one's identity when you drop one.
            self.vote.reset()
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

        # One hand: the vote says which one it is. Until it is sure the hand
        # holds NO role — it must never be handed the cursor on the off
        # chance, and it must not launch anything either.
        hand = hands[0]
        side = self.vote.update(hand.get("label", ""),
                                float(hand.get("score") or 0.0), now)
        self._last = {"right": None, "left": None}
        if side is None:
            self.off_since = None
            return self._roles(None, None)

        self._last[side] = _center(hand["pts"])
        if side == self.dominant:
            self.off_since = None      # no off hand -> cooldown re-arms
        elif self.off_since is None:
            self.off_since = now       # off hand identified: start cooldown
        pts = hand["pts"]
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

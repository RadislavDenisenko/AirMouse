"""Off-hand finger-count launcher: hold up N fingers to fire slot N.

The non-dominant hand raises 1-4 fingers (thumb ignored) and, held briefly,
triggers the command bound to that COUNT — it's how many fingers are up, not
which ones: 1 = one finger (usually the index), 2 = index+middle, 3 =
index+middle+ring, 4 = all four. Any combination that adds up to the same
count works. Returns the slot index; the caller owns running the configured
command. Debounced so a passing pose doesn't fire, and latched so one
continuous hold fires exactly once.
"""

from gestures import fingers_extended

SLOT_LABELS = ("1 finger", "2 fingers", "3 fingers", "4 fingers")  # slots 0..3


class FingerLauncher:
    def __init__(self, hold_s: float = 0.3, cooldown_s: float = 1.0):
        self.hold_s = hold_s
        self.cooldown_s = cooldown_s
        self._candidate = None      # slot index being held
        self._since = None          # when this count's hold began
        self._fired_for = None      # slot already fired this continuous hold
        self._cooldown_until = 0.0
        self.current = None         # current slot (finger count - 1), overlay
        self.progress = 0.0         # 0..1 hold progress (overlay)

    def _slot(self, pts):
        """Slot 0..3 from how many fingers are up (1..4), or None."""
        if pts is None:
            return None
        n = sum(fingers_extended(pts).values())
        return n - 1 if 1 <= n <= 4 else None

    def reset(self):
        self._candidate = self._since = self._fired_for = None
        self.current = None
        self.progress = 0.0

    def update(self, pts, now):
        """Feed the off-hand points (or None). Returns a fired slot 0..3 once
        per deliberate hold, else None."""
        idx = self._slot(pts)
        self.current = idx
        if idx is None:
            self._candidate = self._since = self._fired_for = None
            self.progress = 0.0
            return None
        if idx != self._candidate:
            self._candidate, self._since, self._fired_for = idx, now, None
        self.progress = (min(1.0, (now - self._since) / self.hold_s)
                         if self._since is not None and self.hold_s > 0 else 1.0)
        if now < self._cooldown_until or self._fired_for == idx:
            return None
        if self._since is not None and now - self._since >= self.hold_s:
            self._fired_for = idx
            self._cooldown_until = now + self.cooldown_s
            return idx
        return None

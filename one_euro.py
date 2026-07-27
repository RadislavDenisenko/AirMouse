"""One-Euro filter (Casiez, Roussel, Vogel — CHI 2012).

Adaptive low-pass: heavy smoothing at low speeds (kills jitter when the hand
is still), light smoothing at high speeds (no perceptible lag when moving).
"""

import math


class OneEuro:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self._prev_x = None
        self._prev_dx = 0.0
        self._prev_t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, x: float, t: float) -> float:
        if self._prev_t is None:
            self._prev_x, self._prev_t = x, t
            return x
        dt = t - self._prev_t
        if dt <= 0:
            return self._prev_x
        self._prev_t = t

        # smoothed derivative -> speed-adaptive cutoff
        dx = (x - self._prev_x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self._prev_dx
        self._prev_dx = dx_hat

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self._prev_x
        self._prev_x = x_hat
        return x_hat


class OneEuro2D:
    """Independent One-Euro filters for an (x, y) point."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0):
        self._fx = OneEuro(min_cutoff, beta, d_cutoff)
        self._fy = OneEuro(min_cutoff, beta, d_cutoff)

    def reset(self):
        self._fx.reset()
        self._fy.reset()

    def filter(self, pos, t: float):
        return (self._fx.filter(pos[0], t), self._fy.filter(pos[1], t))

"""Survival curves S(t) = P(X > t) built from bounds, and range prices read off them.

Every input is a bound, never a price. A Kalshi threshold book says bid <= S(t) <= ask at one
strike; an option call spread bounds S from below at its low strike and from above at its high
strike. Because S is non-increasing, each bound also binds every other point, and the tightest
of those is the envelope. It propagates each quote on its own; constraints that tie strikes
together, such as an option spread bounding the integral of S between its strikes, are not
used, so between option strikes the envelope can be wider than no-arbitrage allows. The point
estimate is the only modelled output and is clipped into the envelope, so a bad shape assumption
can move it but never past what the bounds allow. When the inputs contradict each other there is
no envelope to clip into: `consistent` is False, `crossings` says where, and the point is still
isotonic and within [0, 1] but not clipped.
"""

import math

from bisect import bisect_right
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()
_EPS = 1e-12


@dataclass(frozen=True)
class Bound:
    t: float
    lo: float
    hi: float

    def __post_init__(self):
        if not math.isfinite(self.t):
            raise ValueError(f"bound strike is not finite: {self.t}")
        if not (0.0 <= self.lo <= self.hi <= 1.0):
            raise ValueError(f"bound at {self.t} is not 0 <= lo <= hi <= 1: {self.lo}, {self.hi}")


@dataclass(frozen=True)
class Crossing:
    """Bounds that cannot all hold: S would have to rise from t_low to t_high. t_low == t_high
    when two bounds at one strike leave no room between them."""
    t_low: float
    t_high: float
    gap: float


def _log_phi(z):
    """log of the standard normal CDF, finite however far into the lower tail. The tail ratios
    are taken in log space so a knot deep in the tail still joins its extrapolation exactly."""
    if z > -30:
        return math.log(0.5 * math.erfc(-z / math.sqrt(2)))
    return -z * z / 2 - math.log(-z) - 0.5 * math.log(2 * math.pi) + math.log1p(-1 / z**2 + 3 / z**4)


def _pava_decreasing(y, w):
    """Weighted least-squares fit of a non-increasing sequence (pool adjacent violators)."""
    blocks = []
    for yi, wi in zip(y, w):
        blocks.append([yi, wi, 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            v2, w2, n2 = blocks.pop()
            v1, w1, n1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, n1 + n2])
    out = []
    for v, _, n in blocks:
        out.extend([v] * n)
    return out


def _pchip_slopes(x, y):
    """Fritsch-Carlson slopes: the interpolant is monotone wherever the data are."""
    n = len(x)
    h = [x[i + 1] - x[i] for i in range(n - 1)]
    d = [(y[i + 1] - y[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    m[0] = _end_slope(h[0], h[1], d[0], d[1])
    m[-1] = _end_slope(h[-1], h[-2], d[-1], d[-2])
    return m


def _end_slope(h0, h1, d0, d1):
    """Three-point end slope, zeroed if it points the wrong way. Fritsch-Carlson also caps it at
    three secants when the data turn, but knots here are non-increasing, where that cap cannot
    bind: with d1 = 0 the slope is under 2|d0|."""
    e = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    return e if e * d0 > 0 else 0.0


class Curve:
    """Envelope and point estimate of S from a set of bounds.

    The point fit runs through knots: the strikes of `estimates` when given, otherwise every
    strike quoted on both sides. A one-sided or empty quote bounds S but says nothing about
    where inside the bound it sits, so it shapes the envelope and not the fit. Tails beyond the
    outermost knots use a normal fitted in probit space to the knot values between 0.005 and
    0.995, scaled to join the interpolant continuously; with fewer than two such knots, or a
    fitted slope that is not falling, there is no tail and point() returns None outside the
    knot span (and everywhere when there are no knots).
    """

    def __init__(self, bounds, estimates=None):
        """`estimates` is an optional list of (t, value, weight) market estimates of S, such as
        mid-based digitals, used for the point fit in place of the midpoint of each bound."""
        merged = {}
        for b in bounds:
            lo, hi = merged.get(b.t, (0.0, 1.0))
            merged[b.t] = (max(lo, b.lo), min(hi, b.hi))
        if not merged:
            raise ValueError("no bounds")
        self.t = sorted(merged)
        self.lo = [merged[t][0] for t in self.t]
        self.hi = [merged[t][1] for t in self.t]
        self.crossings = self._find_crossings()
        self._fit(estimates)

    def _find_crossings(self):
        out = []
        best_hi, best_t = 1.0, None
        for t, lo, hi in zip(self.t, self.lo, self.hi):
            if hi < best_hi:
                best_hi, best_t = hi, t
            if lo > best_hi + _EPS:
                out.append(Crossing(best_t, t, lo - best_hi))
        return out

    @property
    def consistent(self):
        return not self.crossings

    def envelope(self, x):
        """(lo, hi) on S(x) implied by monotonicity alone. lo > hi only when the inputs cross."""
        hi = min((h for t, h in zip(self.t, self.hi) if t <= x), default=1.0)
        lo = max((l for t, l in zip(self.t, self.lo) if t >= x), default=0.0)
        return lo, hi

    def _clip(self, x, s):
        if not self.consistent:
            return s
        lo, hi = self.envelope(x)
        return min(max(s, lo), hi)

    def _fit(self, estimates):
        if estimates:
            if not all(math.isfinite(x) for e in estimates for x in e):
                raise ValueError("estimates must be finite")
            pts = sorted((t, min(max(v, 0.0), 1.0), w) for t, v, w in estimates)
            if any(w <= 0 for _, _, w in pts) or len({t for t, _, _ in pts}) != len(pts):
                raise ValueError("estimates need distinct strikes and positive weights")
        else:
            pts = [(t, (l + h) / 2, 1.0 / max(h - l, 0.01))
                   for t, l, h in zip(self.t, self.lo, self.hi) if l > 0 and h < 1]
        self.knots = [t for t, _, _ in pts]
        weights = [w for _, _, w in pts]
        s = _pava_decreasing([v for _, v, _ in pts], weights)
        self.s = [self._clip(t, v) for t, v in zip(self.knots, s)]
        self.m = _pchip_slopes(self.knots, self.s) if len(self.knots) > 1 else [0.0] * len(self.knots)
        self.tail = self._fit_tail(weights)

    def _fit_tail(self, weights):
        pts = [(t, _N.inv_cdf(v), w)
               for t, v, w in zip(self.knots, self.s, weights) if 0.005 < v < 0.995]
        if len(pts) < 2:
            return None
        sw = sum(w for _, _, w in pts)
        mt = sum(t * w for t, _, w in pts) / sw
        mz = sum(z * w for _, z, w in pts) / sw
        sxx = sum(w * (t - mt) ** 2 for t, _, w in pts)
        sxz = sum(w * (t - mt) * (z - mz) for t, z, w in pts)
        slope = sxz / sxx
        if slope >= 0:
            return None
        sigma = -1.0 / slope
        mu = mt + mz * sigma
        return mu, sigma


    def point(self, x):
        t, s = self.knots, self.s
        if not t:
            return None
        if t[0] <= x <= t[-1]:
            if len(t) == 1:
                return s[0]
            i = min(bisect_right(t, x) - 1, len(t) - 2)
            h = t[i + 1] - t[i]
            u = (x - t[i]) / h
            h00, h10 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u
            h01, h11 = -2 * u**3 + 3 * u**2, u**3 - u**2
            v = h00 * s[i] + h10 * h * self.m[i] + h01 * s[i + 1] + h11 * h * self.m[i + 1]
            return self._clip(x, v)
        if self.tail is None:
            return None
        mu, sigma = self.tail
        if x > t[-1]:
            v = s[-1] * math.exp(_log_phi((mu - x) / sigma) - _log_phi((mu - t[-1]) / sigma))
        else:
            v = 1.0 - (1.0 - s[0]) * math.exp(_log_phi((x - mu) / sigma) - _log_phi((t[0] - mu) / sigma))
        return self._clip(x, v)

    def range(self, a, b):
        """P(a < X <= b): (lo, point, hi). point is None where the tails cannot be modelled.
        lo > hi is possible only when the curve is not consistent."""
        if not a < b:
            raise ValueError(f"empty range ({a}, {b}]")
        lo_a, hi_a = self.envelope(a)
        lo_b, hi_b = self.envelope(b)
        pa, pb = self.point(a), self.point(b)
        p = None if pa is None or pb is None else max(pa - pb, 0.0)
        return max(lo_a - hi_b, 0.0), p, max(hi_a - lo_b, 0.0)

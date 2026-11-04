"""Any yardage range priced off the threshold books Kalshi already lists for that player.

Kalshi lists passing yards as a 25-yard ladder (KXNFLPASSYDS: 250+, 275+, ...) with two-sided
books, and as a one-yard ladder (KXNFLPASSYDSPRECISE, "Pass Yards Precise": 0+ to 601+) whose
pregame books carried no bids in this repo's sample. A whole-yard range [a, b] is
S(a - 0.5) - S(b + 0.5) on one curve, fitted here to the 25-yard ladder.
"""

from .dist import Bound, Curve
from .kalshi import quote


def player_of(m):
    return m["yes_sub_title"].split(":")[0].strip()


def bounds(markets):
    """{player: [Bound]} from threshold markets. Only 'greater' strikes carry a usable S(t)."""
    out = {}
    for m in markets:
        if m.get("strike_type") != "greater" or m.get("floor_strike") is None:
            continue
        bid, ask = quote(m)
        out.setdefault(player_of(m), []).append(
            Bound(float(m["floor_strike"]), bid or 0.0, ask if ask is not None else 1.0))
    return out


def curves(markets):
    return {p: Curve(b) for p, b in bounds(markets).items()}


def yards_range(curve, low, high):
    """P(low <= yards <= high) for whole yards, as (lo, point, hi)."""
    if low > high:
        raise ValueError(f"low {low} > high {high}")
    return curve.range(low - 0.5, high + 0.5)

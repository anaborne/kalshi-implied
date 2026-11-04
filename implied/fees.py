"""Kalshi taker fee and fee-adjusted edge, in exact arithmetic.

The quadratic schedule charges rate x C x P x (1 - P), rounded up to the next cent per order.
Rounding is done on Fractions because a float product like 0.07 * 100 * 0.5 * 0.5 lands a hair
above 1.75 and ceil() would charge a cent that Kalshi does not.
"""

from fractions import Fraction
from math import ceil

TAKER_RATE = Fraction(7, 100)


def taker_fee(price, contracts=1, rate=TAKER_RATE):
    """Fee in dollars for one order of `contracts` at `price` dollars (0 < price < 1)."""
    p = Fraction(str(price))
    if not 0 < p < 1:
        raise ValueError(f"price must be inside (0, 1): {price}")
    if contracts < 1:
        raise ValueError("contracts must be >= 1")
    cents = Fraction(str(rate)) * contracts * p * (1 - p) * 100
    return Fraction(ceil(cents), 100)


def edge(fair, bid, ask, contracts=100, rate=TAKER_RATE, bid_size=None, ask_size=None):
    """Best taker trade against a quote given a fair Yes probability, net of fees, per contract.

    Returns (side, ev, n) with side 'yes' (lift the ask), 'no' (hit the bid) or None when neither
    side clears its fee, and n the contracts the fee is charged on: `contracts`, cut to the
    displayed size of that side when one is given. A missing side is passed as None.
    """
    fair = Fraction(str(fair))
    best = (None, Fraction(0), 0)
    if ask is not None and 0 < ask < 1 and (n := _fill(contracts, ask_size)):
        a = Fraction(str(ask))
        ev = fair - a - taker_fee(a, n, rate) / n
        if ev > best[1]:
            best = ("yes", ev, n)
    if bid is not None and 0 < bid < 1 and (n := _fill(contracts, bid_size)):
        no_price = 1 - Fraction(str(bid))
        ev = (1 - fair) - no_price - taker_fee(no_price, n, rate) / n
        if ev > best[1]:
            best = ("no", ev, n)
    return best


def _fill(contracts, size):
    """Whole contracts available: Kalshi prints sizes like 10.00 and, on some books, fractions.
    Under one whole contract shown means nothing to take: 0."""
    return contracts if size is None else min(contracts, int(size))

"""Kalshi's S&P 500 close buckets against the SPX options expiring the same afternoon.

Kalshi's rules settle a KXINX bucket on the end-of-day S&P 500 value. Cboe's specifications
list SPXW as P.M.-settled, European, cash-settled, trading to 16:00 ET on expiration day (13:00 on
a half day), so on the day of expiry both price the same close. A
vertical spread bounds the digital at its two strikes: with V = C(K1) - C(K2),
(K2 - K1) S(K2) <= V <= (K2 - K1) S(K1). Bid/ask on each leg turns V into an interval, which
turns into a Bound at each strike, and puts give the mirror image. Only same-day chains are
used, and over at most six and a half hours any interest rate under 10% moves the discount
factor by less than 1e-4, so it is taken as 1.

Spreads up to 50 points wide are used: a narrow spread pins where a bound applies and a wide one
pins its level. On the synthetic two-hour chain in tests/test_spx.py the wide ones narrow the
envelope 3x at 3.6 sigma from the money and by under 1.2x within 1.8 sigma.

Options prices are risk-neutral, and no adjustment to real-world odds is made here. A Kalshi
bucket that disagrees with the options is a disagreement between two markets, not by itself an
edge.

CBOE's free chain is 15 minutes behind: its quotes are as of data.last_trade_time, and by the
time Kalshi's book is read both spot and the time left to the close have moved. Rolling the
chain forward needs a model, so two curves are kept. `recorded` is the chain as of its own
timestamp; its envelope is forced by the option quotes, as of that time. `current` moves it to
a live spot print and that print's own time, under three stated assumptions: a forward for what
is left of the session, the distribution of log(close / forward) keeping its shape in units of
its own standard deviation, and variance accruing evenly through the rest of the session. Its
envelope is a model bound, not a forced one. The forward is the live spot by default; carry over
minutes is under 0.01 points. The chain's own parity forward sat 0.61 to 2.27 points above its
current_price in all 12 chains recorded on Nov 4, a gap of one sign that quote timing alone does
not obviously explain, so `keep_basis` carries that gap onto the live spot instead and results
are reported both ways. The fair value comes from `current`. Flags are reported under each curve
and under their union, which flags a quote only when it is outside under both.
"""

import json
import math
import re
import statistics
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .dist import Bound, Curve
from .fees import edge
from .kalshi import quote, sizes

CHAIN_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/_SPX.json"
_SYMBOL = re.compile(r"^(SPXW?)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
ET = ZoneInfo("America/New_York")
_YEAR = 365 * 86400

# Spelled out because %b follows the locale, and Kalshi tickers do not.
_MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def event_prefix(day):
    """Start of Kalshi's KXINX event ticker for `day`; the rest is the close hour, H1600 on a
    full session and H1300 on a half day."""
    return f"KXINX-{day:%y}{_MONTHS[day.month - 1]}{day:%d}H"


def day_markets(books, day):
    """(event ticker, markets, close as an aware datetime) for the KXINX close on `day`."""
    prefix = event_prefix(day)
    events = {m["event_ticker"] for m in books if m["event_ticker"].startswith(prefix)}
    if len(events) != 1:
        raise ValueError(f"expected one KXINX event starting {prefix}, found {sorted(events)}")
    event = events.pop()
    ms = [m for m in books if m["event_ticker"] == event]
    close = min(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) for m in ms)
    return event, ms, close.astimezone(ET)


def years_between(start, end):
    return (end - start).total_seconds() / _YEAR


def chain_time(chain):
    """When the chain's quotes are from, as an aware datetime (CBOE prints ET without a zone)."""
    return datetime.fromisoformat(chain["data"]["last_trade_time"]).replace(tzinfo=ET)


def fetch_chain():
    with urllib.request.urlopen(CHAIN_URL, timeout=60) as r:
        return json.load(r)


def legs(chain, expiry, root="SPXW"):
    """{strike: {'C': (bid, ask), 'P': (bid, ask)}} for one expiry. A zero ask is no offer."""
    out = {}
    for o in chain["data"]["options"]:
        m = _SYMBOL.match(o["option"])
        if not m or m[1] != root:
            continue
        if date(2000 + int(m[2]), int(m[3]), int(m[4])) != expiry:
            continue
        bid, ask = float(o["bid"] or 0), float(o["ask"] or 0)
        if ask <= 0 or bid > ask:
            continue
        out.setdefault(int(m[6]) / 1000, {})[m[5]] = (bid, ask)
    return out


def forward(legs_by_strike, spot, n=20):
    """Put-call parity with a unit discount factor: F = K + C - P at each strike, from mids.
    The median over the n strikes nearest spot is robust to a few stale legs."""
    pts = [k + (sum(q["C"]) - sum(q["P"])) / 2 for k, q in
           sorted(legs_by_strike.items(), key=lambda kq: abs(kq[0] - spot)) if "C" in q and "P" in q][:n]
    if len(pts) < 3:
        raise ValueError("fewer than three strikes with both a call and a put quoted")
    f = statistics.median(pts)
    if abs(f - spot) > 0.01 * spot:
        raise ValueError(f"parity forward {f:.2f} is more than 1% from spot {spot:.2f}; chain is stale or crossed")
    return f


def spread_bounds(legs_by_strike, max_width=50.0):
    ks = sorted(legs_by_strike)
    out = []
    for i, k1 in enumerate(ks):
        for k2 in ks[i + 1:]:
            w = k2 - k1
            if w > max_width:
                break
            q1, q2 = legs_by_strike[k1], legs_by_strike[k2]
            if "C" in q1 and "C" in q2:
                (b1, a1), (b2, a2) = q1["C"], q2["C"]
                out.append(Bound(k1, _unit(max(b1 - a2, 0) / w), 1.0))
                out.append(Bound(k2, 0.0, _unit((a1 - b2) / w)))
            if "P" in q1 and "P" in q2:
                (b1, a1), (b2, a2) = q1["P"], q2["P"]
                out.append(Bound(k1, _unit(1 - (a2 - b1) / w), 1.0))
                out.append(Bound(k2, 0.0, _unit(1 - max(b2 - a1, 0) / w)))
    return out


def _unit(x):
    return min(max(x, 0.0), 1.0)


def digital_estimates(legs_by_strike, fwd):
    """Mid-price digitals from each adjacent vertical, using calls where the pair's midpoint is
    above the forward and puts below it, so in-the-money legs (wider, noisier) are only ever read
    in the one pair that straddles the forward. A spread over (k0, k1) measures the average
    of S across it, which differs from S at the midpoint by (k1 - k0)^2 S'' / 24, so that is where
    it is placed:
    the same-day grid runs 5 points apart near the money and 10 to 200 apart further out, where
    a middle-strike placement would be off by half the gap. Weight is the inverse of the mean
    bid/ask width of the two legs, in probability units."""
    ks = sorted(legs_by_strike)
    out = []
    for k0, k1 in zip(ks, ks[1:]):
        k = (k0 + k1) / 2
        side = "C" if k >= fwd else "P"
        q0, q1 = legs_by_strike[k0].get(side), legs_by_strike[k1].get(side)
        if q0 is None or q1 is None:
            continue
        w = k1 - k0
        v = (sum(q0) - sum(q1)) / 2 / w
        s = v if side == "C" else 1 + v
        width = ((q0[1] - q0[0]) + (q1[1] - q1[0])) / 2 / w
        out.append((k, _unit(s), 1.0 / max(width, 0.005)))
    return out


def implied_curves(chain, close, spot=None, now=None, max_width=50.0, keep_basis=False):
    """(current, recorded, chain forward) for the SPX close at `close` (an aware datetime).

    With a live `spot` printed at `now`, a strike K on the recorded chain maps to
    F' * (K / F) ** sqrt(T_now / T_chain), where F is the chain's parity forward, F' is the live
    spot (plus the chain's F - current_price with `keep_basis`) and T is the time left to the
    close: the same standardized log-moneyness, hence the same digital.
    Without them the two curves are the same object.
    """
    if (spot is None) != (now is None):
        raise ValueError("re-centring needs both spot and now")
    stamp = chain_time(chain)
    if stamp.date() != close.date():
        raise ValueError(f"chain is from {stamp:%Y-%m-%d}; only same-day chains are used")
    t_chain = years_between(stamp, close)
    if t_chain <= 0:
        raise ValueError("chain is stamped at or after the close")
    lb = legs(chain, close.date())
    fwd = forward(lb, float(chain["data"]["current_price"]))
    raw_bounds, raw_est = spread_bounds(lb, max_width), digital_estimates(lb, fwd)
    recorded = Curve(raw_bounds, raw_est)
    if spot is None:
        return recorded, recorded, fwd
    t_now = years_between(now, close)
    if t_now <= 0:
        raise ValueError("now is at or after the close")
    power = math.sqrt(t_now / t_chain)
    fwd_now = spot + (fwd - float(chain["data"]["current_price"]) if keep_basis else 0.0)
    move = lambda k: fwd_now * (k / fwd) ** power
    current = Curve([Bound(move(b.t), b.lo, b.hi) for b in raw_bounds],
                    [(move(k), s, w) for k, s, w in raw_est])
    return current, recorded, fwd


def _cent_above(x):
    """The first cent strictly above x: the close prints to the cent, so P(X > x) = P(X >= this)."""
    return (math.floor(x * 100 + 1e-6) + 1) / 100


def _at_least(curve, k):
    """(lo, point, hi) on P(X >= k). A spread's ceiling at its upper strike K2 bounds P(X >= K2),
    since a call spread pays its full width whenever X >= K2, and a floor at K1 bounds
    P(X > K1) <= P(X >= K1). So the envelope taken exactly at k bounds P(X >= k)."""
    lo, hi = curve.envelope(k)
    return lo, curve.point(k), hi


def kalshi_prob(curve, m):
    """(lo, point, hi) on Yes for one KXINX market. Kalshi writes "7,675 to 7,699.9999" with both
    ends included, "7,875 or above" as greater-than 7874.9999 and "7,174.9999 or below" as less
    than 7175; with cent prints each is a difference of P(X >= k) at whole cents."""
    kind, lo_k, hi_k = m["strike_type"], m.get("floor_strike"), m.get("cap_strike")
    if kind == "between":
        (alo, ap, ahi), (blo, bp, bhi) = _at_least(curve, float(lo_k)), _at_least(curve, _cent_above(float(hi_k)))
        p = None if ap is None or bp is None else max(ap - bp, 0.0)
        return max(alo - bhi, 0.0), p, max(ahi - blo, 0.0)
    if kind == "greater":
        return _at_least(curve, _cent_above(float(lo_k)))
    if kind == "greater_or_equal":
        return _at_least(curve, float(lo_k))
    if kind == "less":
        lo, p, hi = _at_least(curve, float(hi_k))
        return 1 - hi, None if p is None else 1 - p, 1 - lo
    raise ValueError(f"unhandled strike_type {kind!r} on {m['ticker']}")


def compare(curve, markets, rate, contracts=100, recorded=None):
    """One row per market: the fair value from `curve`, and floor, ceiling and flag under
    `curve` alone (_current), `recorded` alone (_recorded) and their union (lo/hi/outside)."""
    rows = []
    for m in markets:
        clo, p, chi = kalshi_prob(curve, m)
        rlo, _, rhi = kalshi_prob(recorded, m) if recorded is not None else (clo, None, chi)
        lo, hi = min(clo, rlo), max(chi, rhi)
        bid, ask = quote(m)
        bid_size, ask_size = sizes(m)
        side, ev, n = edge(p, bid, ask, contracts, rate, bid_size, ask_size) if p is not None else (None, 0, 0)
        rows.append({
            "ticker": m["ticker"], "label": m["yes_sub_title"], "bid": bid, "ask": ask,
            "bid_size": bid_size, "ask_size": ask_size,
            "lo": lo, "point": p, "hi": hi, "side": side, "ev": float(ev), "contracts": n,
            "outside": _outside(bid, ask, lo, hi),
            "lo_current": clo, "hi_current": chi, "outside_current": _outside(bid, ask, clo, chi),
            "lo_recorded": rlo, "hi_recorded": rhi, "outside_recorded": _outside(bid, ask, rlo, rhi),
        })
    return rows


def _outside(bid, ask, lo, hi):
    """Where the Kalshi quote sits wholly outside an envelope, before fees."""
    if ask is not None and ask < lo:
        return "ask below floor"
    if bid is not None and bid > hi:
        return "bid above ceiling"
    return None


def agreement(rows):
    """How a set of compare() rows sits against the options. Over two-sided buckets: the share
    whose bid <= fair <= ask, the distance from fair to the Kalshi mid, and the widths of the
    Kalshi quote and of each envelope, and how many show under 10 contracts on either side.
    Over all rows: flag counts under each reading."""
    two = [r for r in rows if r["bid"] is not None and r["ask"] is not None and r["point"] is not None]
    if not two:
        raise ValueError("no two-sided buckets")
    gaps = [abs((r["bid"] + r["ask"]) / 2 - r["point"]) for r in two]
    mean = lambda xs: sum(xs) / len(xs)
    return {"buckets": len(two),
            "fair_inside_quote": sum(r["bid"] <= r["point"] <= r["ask"] for r in two) / len(two),
            "mean_gap_to_mid": mean(gaps), "max_gap_to_mid": max(gaps),
            "mean_quote_width": mean([r["ask"] - r["bid"] for r in two]),
            "thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) < 10 for r in two),
            "mean_width_current": mean([r["hi_current"] - r["lo_current"] for r in two]),
            "mean_width_union": mean([r["hi"] - r["lo"] for r in two]),
            "flagged_union": sum(r["outside"] is not None for r in rows),
            "flagged_current": sum(r["outside_current"] is not None for r in rows),
            "flagged_recorded": sum(r["outside_recorded"] is not None for r in rows)}

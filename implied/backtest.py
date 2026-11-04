"""Hold-one-strike-out test of the NFL range curve on settled games.

For each player-game, the pre-kickoff book on every threshold is the closing quote of the last
one-minute candle at or before the snapshot. Kalshi writes a candle only in a minute when
something changes, so that candle can be hours older than the snapshot, and its close is the
book as it stood until the next change. One strike is hidden, the curve is fitted to the
rest, and its estimate of the hidden S(t) is scored against how that market settled. Two
references sit beside it: straight-line interpolation between the neighbouring mids (the obvious
cheap answer) and the hidden market's own mid (what the book itself said, the bar to get close to).

Kickoff is taken as occurrence_datetime minus three hours. On the 2,309 passing-yards markets
settled from Sep 1 to Nov 3 2026, that lands every one on a real slot (Sun 13:00, 16:05, 16:25,
20:20; Mon and Thu 20:15; Wed 20:20 and Thu 20:35 in week 1; Sun 09:30). The snapshot is ten
minutes before kickoff; markets listed after it are in-game listings and are dropped.
"""

import json
import os
import random
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import kalshi
from .dist import Bound, Curve
from .nfl import player_of

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "nfl")


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def snapshot_time(m):
    return _ts(m["occurrence_datetime"]) - timedelta(hours=3, minutes=10)


def pregame_quote(series, m):
    """(bid, ask) at the snapshot; cached by ticker, snapshot time and candle period. A market
    listed at or after its snapshot has no pregame book; it returns (None, None) and is not cached.

    A book that has not changed for days has no recent candle, so the search steps back from
    the snapshot in 72-hour windows until it finds one or reaches the market's open_time. A
    one-minute window of 84 hours is refused by the API (80 is accepted).
    """
    snap = int(snapshot_time(m).timestamp())
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{m['ticker']}@{snap}-p1.json")
    if os.path.exists(path):
        with open(path) as f:
            return tuple(json.load(f))
    opened, end, q = int(_ts(m["open_time"]).timestamp()), snap, (None, None)
    if opened >= snap:
        return q
    while end > opened:
        start = max(opened, end - 72 * 3600)
        cs = [c for c in kalshi.candles(series, m["ticker"], start, end, period=1) if c["end_period_ts"] <= snap]
        if cs:
            q = kalshi.parse_quote(cs[-1]["yes_bid"].get("close_dollars"), cs[-1]["yes_ask"].get("close_dollars"))
            break
        end = start
    with open(path, "w") as f:
        json.dump(q, f)
    return q


def player_games(series, settled, min_rows=4, two_sided=True):
    """[(event, player, [(strike, bid, ask, result)])] with two-sided pregame quotes only, or with
    every pregame quote when `two_sided` is False."""
    groups = {}
    for m in settled:
        if m.get("strike_type") != "greater" or m.get("result") not in ("yes", "no"):
            continue
        if _ts(m["open_time"]) >= snapshot_time(m):
            continue
        groups.setdefault((m["event_ticker"], player_of(m)), []).append(m)
    out = []
    for (event, player), ms in sorted(groups.items()):
        rows = []
        for m in ms:
            bid, ask = pregame_quote(series, m)
            if not two_sided or (bid is not None and ask is not None and bid < ask):
                rows.append((float(m["floor_strike"]), bid, ask, m["result"] == "yes"))
        rows.sort()
        if len(rows) >= min_rows:
            out.append((event, player, rows))
    return out


def _linear(rows, t):
    (t0, b0, a0, _), (t1, b1, a1, _) = rows
    m0, m1 = (b0 + a0) / 2, (b1 + a1) / 2
    return m0 + (m1 - m0) * (t - t0) / (t1 - t0)


def holdout(games):
    """One record per hidden interior strike: estimates from the curve, the line and the book."""
    recs = []
    for event, player, rows in games:
        for j in range(1, len(rows) - 1):
            t, bid, ask, hit = rows[j]
            rest = rows[:j] + rows[j + 1:]
            curve = Curve([Bound(k, b, a) for k, b, a, _ in rest])
            lo, hi = curve.envelope(t)
            recs.append({
                "event": event, "player": player, "t": t, "hit": hit, "bid": bid, "ask": ask,
                "curve": curve.point(t), "line": _linear((rows[j - 1], rows[j + 1]), t),
                "book": (bid + ask) / 2, "lo": lo, "hi": hi, "crossed": not curve.consistent,
                "gaps": (t - rows[j - 1][0], rows[j + 1][0] - t),
            })
    return recs


def one_yard_sample(precise_settled):
    """The one-yard markets the benchmark reads: yards 100 to 397 ending in 0, 2, 5 or 7. Two
    fifths of every player's ladder, spread evenly, because candle requests ran at under one a
    second from this machine on Nov 4 2026 and the full 16,856 would have taken hours. The first
    version read only yards ending in 2 or 7 and so never saw a round yard; bids turned out to sit
    on round yards, and the sample was widened to include them."""
    pick = lambda m: 99 < m["floor_strike"] + 0.5 < 398 and int(m["floor_strike"] + 0.5) % 5 in (0, 2)
    return [m for m in precise_settled if m.get("floor_strike") is not None and pick(m)]


def one_yard(ladder_games, precise_games):
    """One record per one-yard strike that falls strictly between two strikes of the same
    player's 25-yard ladder: the curve fitted to the whole ladder, the straight line between the
    bracketing ladder mids, and the one-yard book's own bid and ask (None where empty)."""
    ladders = {(e.replace("KXNFLPASSYDS-", "", 1), p): rows for e, p, rows in ladder_games}
    recs = []
    for event, player, rows in precise_games:
        ladder = ladders.get((event.replace("KXNFLPASSYDSPRECISE-", "", 1), player))
        if not ladder:
            continue
        strikes = [r[0] for r in ladder]
        curve = Curve([Bound(k, b, a) for k, b, a, _ in ladder])
        for t, bid, ask, hit in rows:
            if t in strikes or not strikes[0] < t < strikes[-1]:
                continue
            j = bisect_right(strikes, t)
            lo, hi = curve.envelope(t)
            recs.append({"event": event, "player": player, "t": t, "hit": hit, "curve": curve.point(t),
                         "line": _linear((ladder[j - 1], ladder[j]), t), "bid": bid, "ask": ask,
                         "lo": lo, "hi": hi, "crossed": not curve.consistent})
    return recs


def one_yard_summary(recs):
    """What Kalshi's one-yard books showed before kickoff: how many had a bid, the asks they
    carried, and the range of the curve's fair value on the strikes that had one."""
    if not recs:
        raise ValueError("no records")
    asks = [r for r in recs if r["ask"] is not None]
    brier = lambda k: sum((r[k] - r["hit"]) ** 2 for r in recs) / len(recs)
    return {"strikes": len(recs), "games": len({r["event"] for r in recs}),
            "player_games": len({(r["event"], r["player"]) for r in recs}),
            "with_bid": sum(r["bid"] is not None for r in recs), "with_ask": len(asks),
            "empty": sum(r["bid"] is None and r["ask"] is None for r in recs),
            "asks": {f"{a:.2f}": n for a, n in sorted(Counter(r["ask"] for r in asks).items())},
            "curve_where_asked": [min(r["curve"] for r in asks), max(r["curve"] for r in asks)] if asks else None,
            "ask_below_forced_floor": sum(r["ask"] < r["lo"] for r in asks),
            "bids": [{"player": r["player"], "yards": int(r["t"] + 0.5), "bid": r["bid"], "ask": r["ask"],
                      "curve": r["curve"]} for r in recs if r["bid"] is not None],
            "crossed_curves": sum(r["crossed"] for r in recs),
            "hit_rate": sum(r["hit"] for r in recs) / len(recs),
            "brier": {"curve": brier("curve"), "line": brier("line")}}


def score(recs):
    n = len(recs)
    if not n:
        raise ValueError("no records")
    brier = {k: sum((r[k] - r["hit"]) ** 2 for r in recs) / n for k in ("curve", "line", "book")}
    gap = {k: sum(abs(r[k] - r["book"]) for r in recs) / n for k in ("curve", "line")}
    inside = sum(r["lo"] - 1e-9 <= r["book"] <= r["hi"] + 1e-9 for r in recs) / n
    in_quote = lambda k: sum(r["bid"] <= r[k] <= r["ask"] for r in recs) / n
    return {"n": n, "games": len({r["event"] for r in recs}),
            "player_games": len({(r["event"], r["player"]) for r in recs}),
            "crossed_curves": sum(r.get("crossed", False) for r in recs),
            "neighbour_gaps": {f"{a:g}/{b:g}": c for (a, b), c in sorted(Counter(r["gaps"] for r in recs if "gaps" in r).items())},
            "mean_spread": sum(r["ask"] - r["bid"] for r in recs if "bid" in r) / n if "bid" in recs[0] else None,
            "inside_quote": {k: in_quote(k) for k in ("curve", "line")} if "bid" in recs[0] else None,
            "brier": brier, "mean_abs_gap_to_book": gap, "book_mid_inside_envelope": inside}


def bootstrap(recs, draws=2000, seed=20261104):
    """95% percentile intervals for the score differences, resampling whole games: strikes on
    one book share its noise and both quarterbacks in a game share its script and weather, so
    resampling anything smaller would overstate the precision."""
    by = {}
    for r in recs:
        by.setdefault(r["event"], []).append(r)
    keys = sorted(by)

    def diffs(ks):
        s = score([r for k in ks for r in by[k]])
        return {"brier_curve_minus_line": s["brier"]["curve"] - s["brier"]["line"],
                "brier_curve_minus_book": s["brier"]["curve"] - s["brier"]["book"],
                "gap_curve_minus_line": s["mean_abs_gap_to_book"]["curve"] - s["mean_abs_gap_to_book"]["line"],
                "gap_curve": s["mean_abs_gap_to_book"]["curve"]}

    rng = random.Random(seed)
    boot = [diffs([rng.choice(keys) for _ in keys]) for _ in range(draws)]
    return {name: {"value": value, "ci95": list(percentile_ci(sorted(b[name] for b in boot)))}
            for name, value in diffs(keys).items()}


def percentile_ci(xs):
    """2.5th and 97.5th percentiles of a sorted sample, by rank: of 2,000 draws, the 51st and
    the 1,950th, so 50 sit below the interval and 50 above it."""
    n = len(xs)
    return xs[int(0.025 * n)], xs[int(0.975 * n) - 1]

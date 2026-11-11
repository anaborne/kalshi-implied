"""Plant one known bug at a time in a scratch copy and require the test suite to fail on each.

Exits 1 if any mutation survives, any pattern no longer matches exactly once, or any mutation
does not compile, so a refactor that moves the code cannot quietly turn a check into a no-op and
a kill always comes from a test, not from a broken file.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.abspath(__file__))

M = [
    ("implied/fees.py", "return Fraction(ceil(cents), 100)", "return Fraction(round(cents), 100)"),
    ("implied/fees.py", 'if ev > best[1]:\n            best = ("yes", ev, n)', 'if ev >= best[1]:\n            best = ("yes", ev, n)'),
    ("implied/fees.py", '        if ev > best[1]:\n            best = ("no", ev, n)', '        if ev >= best[1]:\n            best = ("no", ev, n)'),
    ("implied/dist.py", "            if hi < best_hi:\n                best_hi, best_t = hi, t\n            if lo > best_hi + _EPS:",
     "            if lo > best_hi + _EPS:\n                pass\n            if hi < best_hi:\n                best_hi, best_t = hi, t\n            if False:"),
    ("implied/dist.py", "_EPS = 1e-12", "_EPS = 1e-3"),
    ("implied/dist.py", "for t, l, h in zip(self.t, self.lo, self.hi) if l > 0 and h < 1]", "for t, l, h in zip(self.t, self.lo, self.hi)]"),
    ("implied/dist.py", "pts = [(t, (l + h) / 2, 1.0 / max(h - l, 0.01))", "pts = [(t, (l + h) / 2, 1.0)"),
    ("implied/dist.py", "s = _pava_decreasing([v for _, v, _ in pts], weights)", "s = _pava_decreasing([v for _, v, _ in pts], [1.0] * len(weights))"),
    ("implied/dist.py", "self.s = [self._clip(t, v) for t, v in zip(self.knots, s)]", "self.s = list(s)"),
    ("implied/dist.py", "pts = sorted((t, min(max(v, 0.0), 1.0), w) for t, v, w in estimates)", "pts = sorted(estimates)"),
    ("implied/dist.py", "hi = min((h for t, h in zip(self.t, self.hi) if t <= x), default=1.0)", "hi = min((h for t, h in zip(self.t, self.hi) if t < x), default=1.0)"),
    ("implied/dist.py", "        return max(lo_a - hi_b, 0.0), p, max(hi_a - lo_b, 0.0)", "        return max(lo_a - lo_b, 0.0), p, max(hi_a - lo_b, 0.0)"),
    ("implied/dist.py", "        return mu, sigma", "        return mu, sigma * 1.2"),
    ("implied/dist.py", "if not all(math.isfinite(x) for e in estimates for x in e):", "if False:"),
    ("implied/dist.py", "        if not math.isfinite(self.t):", "        if False:"),
    ("implied/dist.py", "        if not self.consistent:\n            return s\n", ""),
    ("implied/dist.py", "w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]", "w2, w1 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]"),
    ("implied/dist.py", "        return [d[0], d[0]]", "        return [0.0, 0.0]"),
    ("implied/dist.py", "    m[0] = _end_slope(h[0], h[1], d[0], d[1])", "    m[0] = d[0]"),
    ("implied/dist.py", "if 0.005 < v < 0.995]", "if 0.05 < v < 0.95]"),
    ("implied/dist.py", "sw = sum(w for _, _, w in pts)", "pts = [(t, z, 1.0) for t, z, _ in pts]\n        sw = sum(w for _, _, w in pts)"),
    ("implied/dist.py", "    if z > -30:", "    if z > -3:"),
    ("implied/dist.py", " + math.log1p(-1 / z**2 + 3 / z**4)", ""),
    ("implied/dist.py", "v = s[-1] * math.exp(_log_phi((mu - x) / sigma) - _log_phi((mu - t[-1]) / sigma))",
     "b = _N.cdf((mu - t[-1]) / sigma)\n            v = s[-1] * _N.cdf((mu - x) / sigma) / b if b > 1e-12 else 0.0"),
    ("implied/spx.py", "out.append(Bound(k1, _unit(1 - (a2 - b1) / w), 1.0))", "out.append(Bound(k1, 0.0, 1.0))"),
    ("implied/spx.py", "out.append(Bound(k2, 0.0, _unit((a1 - b2) / w)))", "out.append(Bound(k2, 0.0, 1.0))"),
    ("implied/spx.py", 'side = "C" if k >= fwd else "P"', 'side = "C"'),
    ("implied/spx.py", "        k = (k0 + k1) / 2\n", "        k = k1\n"),
    ("implied/spx.py", "width = ((q0[1] - q0[0]) + (q1[1] - q1[0])) / 2 / w", "width = 0.01"),
    ("implied/spx.py", "        lo, hi = min(clo, rlo), max(chi, rhi)", "        lo, hi = min(clo, rlo), chi"),
    ("implied/spx.py", "        lo, hi = min(clo, rlo), max(chi, rhi)", "        lo, hi = clo, max(chi, rhi)"),
    ("implied/spx.py", "    if ask is not None and ask < lo:", "    if ask is not None and ask < lo - 0.02:"),
    ("implied/spx.py", "    f = statistics.median(pts)", "    f = statistics.mean(pts)"),
    ("implied/spx.py", "sorted(legs_by_strike.items(), key=lambda kq: abs(kq[0] - spot))", "sorted(legs_by_strike.items())"),
    ("implied/spx.py", "def forward(legs_by_strike, spot, n=20):", "def forward(legs_by_strike, spot, n=3):"),
    ("implied/spx.py", "    if abs(f - spot) > 0.01 * spot:", "    if False:"),
    ("implied/spx.py", "def spread_bounds(legs_by_strike, max_width=50.0):", "def spread_bounds(legs_by_strike, max_width=25.0):"),
    ("implied/spx.py", "    power = math.sqrt(t_now / t_chain)", "    power = 1.0"),
    ("implied/spx.py", "    move = lambda k: fwd_now * (k / fwd) ** power", "    move = lambda k: fwd * spot / float(chain[\"data\"][\"current_price\"]) * (k / fwd) ** power"),
    ("implied/spx.py", "    if stamp.date() != close.date():", "    if False:"),
    ("implied/spx.py", 'close = min(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) for m in ms)',
     'close = datetime.combine(day, datetime.min.time().replace(hour=16), ET)'),
    ("implied/spx.py", "edge(p, bid, ask, contracts, rate, bid_size, ask_size)", "edge(p, bid, ask, contracts, bid_size=bid_size, ask_size=ask_size)"),
    ("implied/spx.py", '"fair_inside_quote": sum(r["bid"] <= r["point"] <= r["ask"] for r in two) / len(two),',
     '"fair_inside_quote": sum(r["bid"] < r["point"] < r["ask"] for r in two) / len(two),'),
    ("implied/spx.py", "_MONTHS[day.month - 1]", "_MONTHS[day.month % 12]"),
    ("implied/kalshi.py", "(ask if 0 < ask < 1 else None)", "(ask if ask < 1 else None)"),
    ("implied/kalshi.py", "(bid if 0 < bid < 1 else None)", "(bid if 0 < bid else None)"),
    ("implied/kalshi.py", "if e.code != 429 or attempt == 7:", "if e.code != 429 or attempt == 6:"),
    ("implied/kalshi.py", "min(2 ** attempt, 60)", "2 ** attempt"),
    ("implied/kalshi.py", "float(wait) if wait and wait.isdigit() else ", ""),
    ("implied/kalshi.py", 'page = get("/markets", limit=1000, cursor=cursor, **params)', 'page = get("/markets", limit=100, cursor=cursor, **params)'),
    ("implied/nfl.py", "return curve.range(low - 0.5, high + 0.5)", "return curve.range(low, high)"),
    ("implied/nfl.py", 'return m["yes_sub_title"].split(":")[0].strip()', 'return m["yes_sub_title"].split(":")[0]'),
    ("implied/backtest.py", 'return _ts(m["occurrence_datetime"]) - timedelta(hours=3, minutes=10)', 'return _ts(m["occurrence_datetime"]) - timedelta(hours=2, minutes=50)'),
    ("implied/backtest.py", 'if c["end_period_ts"] <= snap]', 'if c["end_period_ts"] <= snap + 3600]'),
    ("implied/backtest.py", 'if _ts(m["open_time"]) >= snapshot_time(m):', "if False and m:"),
    ("implied/backtest.py", "(bid is not None and ask is not None and bid < ask)", "(bid is not None and ask is not None)"),
    ("implied/backtest.py", "for j in range(1, len(rows) - 1):", "for j in range(0, len(rows) - 1):"),
    ("implied/backtest.py", "return m0 + (m1 - m0) * (t - t0) / (t1 - t0)", "return (m0 + m1) / 2"),
    ("implied/backtest.py", 'by.setdefault(r["event"], []).append(r)', 'by.setdefault((r["event"], r["player"]), []).append(r)'),
    ("implied/backtest.py", "return xs[int(0.025 * n)], xs[int(0.975 * n) - 1]", "return xs[int(0.025 * n)], xs[int(0.975 * n)]"),
    ("implied/backtest.py", "boot = [diffs([rng.choice(keys) for _ in keys]) for _ in range(draws)]", "boot = [diffs([rng.choice(keys) for _ in keys[:5]]) for _ in range(draws)]"),
    ("implied/__main__.py", '        except BODY_ERRORS as e:\n            entry["error"]', '        except ZeroDivisionError as e:\n            entry["error"]'),
    ("implied/__main__.py", "day = date.fromisoformat(a.date) if a.date else folder_date(a.snapshot)", "day = date.fromisoformat(a.date) if a.date else _now().date()"),
    ("implied/__main__.py", "    while (now := _now()) < close:", "    while (now := _now()) <= close:"),
    ("implied/__main__.py", "FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError, KeyError)", "FETCH_ERRORS = (ValueError, KeyError)"),
    ("implied/__main__.py", "FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError, KeyError)", "FETCH_ERRORS = (OSError, ValueError, KeyError)"),
    ("implied/__main__.py", "    rows = spx.compare(curve, ms, rate, recorded=recorded)\n    print(", "    rows = spx.compare(curve, ms, rate)\n    print("),
    ("implied/__main__.py", "            rows = spx.compare(curve, ms, rate, recorded=recorded)", "            rows = spx.compare(curve, ms, rate)"),
    ("implied/__main__.py", '        rate = kalshi.rate_of(_load(f"{a.snapshot}/series-KXINX.json")["series"])\n        day', '        rate = kalshi.TAKER_RATE\n        day'),
    ("implied/__main__.py", "    spot, spot_time = read_spot(_load(f\"{folder}/spot-{at}.json\"))", "    spot, spot_time = float(chain[\"data\"][\"current_price\"]), read_spot(_load(f\"{folder}/spot-{at}.json\"))[1]"),
    ("implied/__main__.py", "        spot, spot_time = read_spot(fetch_spot())", "        spot, spot_time = 7700.0, read_spot(fetch_spot())[1]"),
    ("implied/__main__.py", "        if r[\"hi\"] < 0.005 and not r[\"bid\"]:", "        if r[\"hi\"] < 0.5 and not r[\"bid\"]:"),
    ("implied/__main__.py", 'entry["winner_kalshi_mid"] = None if row["bid"] is None or row["ask"] is None else (row["bid"] + row["ask"]) / 2',
     'entry["winner_kalshi_mid"] = (row["bid"] or 0) / 2 + (row["ask"] or 0) / 2'),
    ("implied/__main__.py", "    _, _, close = spx.day_markets(kalshi.markets(series_ticker=\"KXINX\", status=\"open\"), today.date())",
     "    close = today.replace(hour=16, minute=0, second=0, microsecond=0)"),
    ("implied/__main__.py", 'if o["option"][:10] == prefix', 'if o["option"][:4] == "SPXW"'),
    ("implied/spx.py", "    power = math.sqrt(t_now / t_chain)", "    power = (t_now / t_chain) ** 0.6"),
    ("implied/spx.py", "        if ask <= 0 or bid > ask:", "        if ask <= 0 or bid >= ask:"),
    ("implied/dist.py", "        return max(lo_a - hi_b, 0.0), p, max(hi_a - lo_b, 0.0)", "        return lo_a - hi_b, p, max(hi_a - lo_b, 0.0)"),
    ("implied/dist.py", "            if len(t) == 1:\n                return s[0]", "            if len(t) == 1:\n                return 0.5"),
    ("implied/fees.py", "    if ask is not None and 0 < ask < 1 and", "    if ask is not None and"),
    ("implied/nfl.py", 'if m.get("strike_type") != "greater" or m.get("floor_strike") is None:', 'if m.get("strike_type") != "greater":'),
    ("implied/backtest.py", '    return {"n": n, "games": len({r["event"] for r in recs}),', '    return {"n": n, "games": len({r["player"] for r in recs}),'),
    ("implied/backtest.py", "def bootstrap(recs, draws=2000, seed=20261104):", "def bootstrap(recs, draws=200, seed=20261104):"),
    ("implied/backtest.py", "        rows.sort()\n", ""),
    ("implied/backtest.py", 'if _ts(m["open_time"]) >= snapshot_time(m):', 'if _ts(m["open_time"]) > snapshot_time(m):'),
    ("implied/spx.py", 'side = "C" if k >= fwd else "P"', 'side = "C" if k0 >= fwd else "P"'),
    ("implied/spx.py", "def forward(legs_by_strike, spot, n=20):", "def forward(legs_by_strike, spot, n=10):"),
    ("implied/spx.py", "1.0 / max(width, 0.005)", "1.0 / max(width, 0.02)"),
    ("implied/spx.py", "    if len(pts) < 3:", "    if len(pts) < 2:"),
    ("implied/spx.py", "    if abs(f - spot) > 0.01 * spot:", "    if abs(f - spot) > 0.015 * spot:"),
    ("implied/spx.py", "    if t_now <= 0:", "    if t_now < 0:"),
    ("implied/spx.py", ' if "C" in q and "P" in q][:n]', "][:n]"),
    ("implied/spx.py", '    fwd_now = spot + (fwd - float(chain["data"]["current_price"]) if keep_basis else 0.0)', "    fwd_now = spot"),
    ("implied/kalshi.py", 'return TAKER_RATE * Fraction(str(series["fee_multiplier"]))', 'return TAKER_RATE * Fraction(series["fee_multiplier"])'),
    ("implied/dist.py", "            if hi < best_hi:", "            if hi <= best_hi:"),
    ("implied/dist.py", "                out.append(Crossing(best_t, t, lo - best_hi))", "                out.append(Crossing(best_t, t, lo - best_hi))\n                break"),
    ("implied/dist.py", "1.0 / max(h - l, 0.01))", "1.0 / max(h - l, 0.02))"),
    ("implied/backtest.py", 'if c["end_period_ts"] <= snap]', 'if c["end_period_ts"] < snap]'),
    ("implied/backtest.py", 'r["lo"] - 1e-9 <= r["book"] <= r["hi"] + 1e-9', 'r["lo"] <= r["book"] <= r["hi"]'),
    ("implied/backtest.py", "            if t in strikes or not strikes[0] < t < strikes[-1]:", "            if not strikes[0] < t < strikes[-1]:"),
    ("implied/fees.py", "    return contracts if size is None else min(contracts, int(size))", "    return contracts"),
    ("implied/spx.py", '            "thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) < 10 for r in two),', '            "thin": 0,'),
    ("implied/__main__.py", "now=book_time if a.clock == \"book\" else spot_time,\n                                                  keep_basis=a.keep_basis)",
     "now=spot_time,\n                                                  keep_basis=a.keep_basis)"),
    ("implied/__main__.py", "    until = int(datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc).timestamp()) - 1",
     "    until = int(datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc).timestamp())"),
    ("implied/__main__.py", '        curve, recorded, fwd = spx.implied_curves(chain, close, spot=spot, now=book_time if a.clock == "book" else spot_time,\n                                                  keep_basis=a.keep_basis)',
     "        curve, recorded, fwd = spx.implied_curves(chain, close)"),
    ("implied/__main__.py", 'now=book_time if a.clock == "book" else spot_time,\n                                                    keep_basis', 'now=book_time,\n                                                    keep_basis'),
    ("implied/__main__.py", 'now=book_time if a.clock == "book" else spot_time,\n                                                    keep_basis=a.keep_basis)', 'now=book_time if a.clock == "book" else spot_time,\n                                                    keep_basis=False)'),
    ("implied/__main__.py", 'backtest.player_games("KXNFLPASSYDSPRECISE", sample, min_rows=1, two_sided=False)', 'backtest.player_games("KXNFLPASSYDSPRECISE", sample, min_rows=1)'),
    ("implied/backtest.py", '            if not two_sided or (bid is not None and ask is not None and bid < ask):', '            if bid is not None and ask is not None and bid < ask:'),
    ("implied/backtest.py", '            "ask_below_forced_floor": sum(r["ask"] < r["lo"] for r in asks),', '            "ask_below_forced_floor": sum(r["ask"] <= r["lo"] for r in asks),'),
    ("implied/backtest.py", '            "with_bid": sum(r["bid"] is not None for r in recs), "with_ask": len(asks),', '            "with_bid": 0, "with_ask": len(asks),'),
    ("implied/backtest.py", "        start = max(opened, end - 72 * 3600)", "        start = max(opened, end - 12 * 3600)"),
    ("implied/backtest.py", "            break\n        end = start", "            break\n        break"),
    ("implied/backtest.py", "int(m[\"floor_strike\"] + 0.5) % 5 in (0, 2)", "int(m[\"floor_strike\"] + 0.5) % 5 == 2"),
    ("implied/backtest.py", "    pick = lambda m: 99 < m[\"floor_strike\"] + 0.5 < 398", "    pick = lambda m: 101 < m[\"floor_strike\"] + 0.5 < 398"),
    ("implied/__main__.py", "BODY_ERRORS = (ValueError, KeyError, TypeError, IndexError)", "BODY_ERRORS = (ValueError,)"),
    ("implied/__main__.py", "            read_spot(trio[\"spot\"])\n", ""),
    ("implied/__main__.py", "    if os.path.isdir(folder) and any(n.startswith(\"kxinx-\") for n in os.listdir(folder)):", "    if False:"),
    ("implied/__main__.py", "    os.makedirs(folder, exist_ok=True)\n    _write(os.path.join(folder, \"series-KXINX.json\"), kalshi.get(\"/series/KXINX\"))\n    while",
     "    while"),
    ("implied/__main__.py", "print(f\"  {t + 0.5:>5.0f}+   {_p(bid if bid > 0 else None)} {_p(ask if ask < 1 else None)}\"",
     "print(f\"  {t + 0.5:>5.0f}+   {_p(c.envelope(t)[0])} {_p(c.envelope(t)[1])}\""),
    ("implied/kalshi.py", 'f = lambda k: None if m.get(k) in (None, "") else float(m[k])', 'f = lambda k: None if m.get(k) is None else float(m[k])'),
    ("implied/fees.py", "min(contracts, int(size))", "min(contracts, round(size))"),
    ("implied/fees.py", "    if bid is not None and 0 < bid < 1 and", "    if bid is not None and 0 < bid and"),
    ("implied/spx.py", '"thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) < 10 for r in two),', '"thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) <= 10 for r in two),'),
    ("implied/spx.py", 'close = min(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) for m in ms)', 'close = max(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) for m in ms)'),
    ("implied/fees.py", "    return contracts if size is None else min(contracts, int(size))", "    return contracts if size is None else max(1, min(contracts, int(size)))"),
    ("implied/backtest.py", "    if opened >= snap:\n        return q\n", ""),
    ("implied/backtest.py", '            "crossed_curves": sum(r.get("crossed", False) for r in recs),', '            "crossed_curves": 0,'),
    ("implied/backtest.py", 'in_quote = lambda k: sum(r["bid"] <= r[k] <= r["ask"] for r in recs) / n', 'in_quote = lambda k: sum(r["bid"] < r[k] < r["ask"] for r in recs) / n'),
    ("implied/backtest.py", '"gaps": (t - rows[j - 1][0], rows[j + 1][0] - t),', '"gaps": (rows[j + 1][0] - t, t - rows[j - 1][0]),'),
    ("implied/__main__.py", "                                    and min(r[\"bid_size\"] or 0, r[\"ask_size\"] or 0) < 10]", "                                    and min(r[\"bid_size\"] or 0, r[\"ask_size\"] or 0) < 1]"),
    ("implied/__main__.py", '"listed_after_snapshot": sum(backtest._ts(m["open_time"]) >= backtest.snapshot_time(m) for m in ladder),', '"listed_after_snapshot": 0,'),
    ("implied/__main__.py", 'f.add_argument("--since", default="2026-09-01")', 'f.add_argument("--since", default="2026-09-12")'),
    ("implied/__main__.py", 'r.add_argument("--every", type=int, default=600, help="seconds between rounds")', 'r.add_argument("--every", type=int, default=60, help="seconds between rounds")'),
    ("implied/__main__.py", 'b.add_argument("--series", default="KXNFLPASSYDS")', 'b.add_argument("--series", default="KXNFLPASSYDSPRECISE")'),
    ("implied/__main__.py", '                  + (f"  pays {1 / p:.2f}x at fair" if p else ""))', '                  + (f"  pays {1 / p:.1f}x at fair" if p else ""))'),
    ("implied/__main__.py", "    for r in sorted(rows, key=lambda r: r[\"lo\"] + r[\"hi\"], reverse=True):", "    for r in rows:"),
    ("implied/fees.py", "    if contracts < 1:", "    if contracts < 0.5:"),
    ("implied/spx.py", '"thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) < 10 for r in two),', '"thin": sum(min(r["bid_size"] or 0, r["ask_size"] or 0) < 9 for r in two),'),
    ("implied/spx.py", "1.0 / max(width, 0.005)", "1.0 / max(width, 0.004)"),
    ("implied/spx.py", "    return (math.floor(x * 100 + 1e-6) + 1) / 100", "    return x"),
    ("implied/spx.py", "_at_least(curve, _cent_above(float(hi_k)))", "_at_least(curve, float(hi_k))"),
    ("implied/spx.py", "    lo, hi = curve.envelope(k)\n    return lo, curve.point(k), hi", "    lo, hi = curve.envelope(k - 0.005)\n    return lo, curve.point(k - 0.005), hi"),
    ("implied/spx.py", "        return max(alo - bhi, 0.0), p, max(ahi - blo, 0.0)", "        return max(alo - blo, 0.0), p, max(ahi - blo, 0.0)"),
    ("implied/spx.py", "        return 1 - hi, None if p is None else 1 - p, 1 - lo", "        return 1 - lo, None if p is None else 1 - p, 1 - hi"),
    ("implied/spx.py", '    if kind == "greater":\n        return _at_least(curve, _cent_above(float(lo_k)))', '    if kind == "greater":\n        return _at_least(curve, float(lo_k))'),
    ("implied/spx.py", "        if q0 is None or q1 is None:\n            continue", "        if q0 is None or q1 is None:\n            break"),
    ("implied/backtest.py", '        if m.get("strike_type") != "greater" or m.get("result") not in ("yes", "no"):', '        if m.get("result") not in ("yes", "no"):'),
    ("implied/__main__.py", 'won = next((m for m in settled if m["event_ticker"].startswith(prefix) and m.get("result") == "yes"), None)', 'won = next((m for m in settled if m.get("result") == "yes"), None)'),
    ("implied/kalshi.py", 'period_interval=period, include_latest_before_start="true")', "period_interval=period)"),
    ("implied/kalshi.py", 'x.get("candlesticks") or []', 'x["candlesticks"]'),
    ("implied/backtest.py", 'cs[-1]["yes_bid"].get("close_dollars"), cs[-1]["yes_ask"].get("close_dollars"))', 'cs[0]["yes_bid"].get("close_dollars"), cs[0]["yes_ask"].get("close_dollars"))'),
    ("implied/backtest.py", "snap - 2 * 3600, snap, period=1)", "snap - 2 * 3600, snap + 3600, period=1)"),
    ("implied/backtest.py", "snap - 2 * 3600, snap, period=1)", "snap - 2 * 3600, snap, period=60)"),
    ("implied/backtest.py", "def fetch_quotes(series, settled, per_request=40):", "def fetch_quotes(series, settled, per_request=100):"),
    ("implied/backtest.py", "        by_snap.setdefault(int(snapshot_time(m).timestamp()), []).append(m)", "        by_snap.setdefault(0, []).append(m)"),
    ("implied/backtest.py", "                    q = pregame_quote(series, m)", "                    q = (None, None)"),
    ("implied/backtest.py", "                    searched.append(quote_key(m))\n", ""),
    ("implied/__main__.py", "              if k not in searched and os.path.exists(", "              if os.path.exists("),
    ("implied/backtest.py", "            q = (quotes or {}).get(quote_key(m))", "            q = None"),
    ("implied/backtest.py", "bid, ask = q if q is not None else pregame_quote(series, m)", "bid, ask = q if q is not None and q[0] is not None else pregame_quote(series, m)"),
    ("implied/backtest.py", "(rows[1:] if j == 0 else rows[:-1])]).point(t)", "rows]).point(t)"),
    ("implied/backtest.py", '    for name, j in (("lowest", 0), ("highest", -1)):', '    for name, j in (("lowest", 0), ("highest", 0)):'),
    ("implied/backtest.py", "                errs.append(p - (bid + ask) / 2)", "                errs.append((bid + ask) / 2 - p)"),
    ("implied/backtest.py", '"no_tail": len(games) - len(errs),', '"no_tail": 0,'),
    ("implied/backtest.py", "median([(rows[0][1] + rows[0][2]) / 2 for _, _, rows in games])", "median([(rows[-1][1] + rows[-1][2]) / 2 for _, _, rows in games])"),
    ("implied/backtest.py", "in rows]).point(-0.5) for _, _, rows in games)", "in rows]).point(0.0) for _, _, rows in games)"),
    ("implied/backtest.py", '"over_1c": mean([x > 0.01 for x in below])', '"over_1c": mean([x > 0.1 for x in below])'),
    ("implied/__main__.py", '    quotes = quotes or (q and os.path.join(REPO, "data", q))\n', ""),
    ("implied/__main__.py", "    recs = backtest.holdout(backtest.player_games(a.series, ms, quotes=quotes))", "    recs = backtest.holdout(backtest.player_games(a.series, ms, quotes=None))"),
    ("implied/__main__.py", "        out[series] = backtest.extrapolation(backtest.player_games(series, ms, quotes=quotes))", "        out[series] = backtest.extrapolation(backtest.player_games(series, ms, quotes=None))"),
    ("implied/__main__.py", "sum(quotes[k] == v for k, v in cached.items())", "len(cached)"),
    ("implied/__main__.py", '"KXNFLRECYDS": ("recyds_settled_20261110.json", "recyds_quotes_20261110.json"),', '"KXNFLRECYDS": ("recyds_settled_20261110.json", "rshyds_quotes_20261110.json"),'),
    ("implied/__main__.py", "not c.knots[0] <= a.range[0] - 0.5 <= a.range[1] + 0.5 <= c.knots[-1]:", "not c.knots[0] < a.range[0] - 0.5 <= a.range[1] + 0.5 <= c.knots[-1]:"),
    ("implied/__main__.py", "not c.knots[0] <= a.range[0] - 0.5 <= a.range[1] + 0.5 <= c.knots[-1]:", "not c.knots[0] <= a.range[0] - 0.5 <= a.range[1] + 0.5 < c.knots[-1]:"),
]


# A clean run of the suite takes about 27 seconds alone and under 300 MB, most of both in the
# golden backtests. macOS refuses setrlimit memory caps, so a mutant that loops or allocates
# without end is watched and killed. Mutants run four at a time, so the time cap leaves room.
LIMIT_SECONDS, LIMIT_RSS_KB, WORKERS = 300, 2 * 1024 * 1024, 4


def run_capped(cmd, cwd):
    """CompletedProcess-like (returncode, stdout, stderr), or None if the run hit a cap."""
    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=err, text=True)
        start = time.monotonic()
        while p.poll() is None:
            rss = subprocess.run(["ps", "-o", "rss=", "-p", str(p.pid)], capture_output=True, text=True).stdout.strip()
            if time.monotonic() - start > LIMIT_SECONDS or (rss.isdigit() and int(rss) > LIMIT_RSS_KB):
                p.kill()
                p.wait()
                return None
            time.sleep(1)
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(cmd, p.returncode, out.read(), err.read())


def attempt(mutation):
    """(line to print, whether the mutation counts against the run)."""
    path, old, new = mutation
    with tempfile.TemporaryDirectory() as d:
        dst = os.path.join(d, "r")
        # A copy, not a link: a mutation can make the code write where it should only read.
        shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        f = os.path.join(dst, path)
        with open(f) as fh:
            src = fh.read()
        if src.count(old) != 1:
            return f"PATTERN NOT FOUND ONCE  {path} | {old.strip()[:70]}", True
        mutated = src.replace(old, new)
        try:
            compile(mutated, f, "exec")
        except SyntaxError as e:
            return f"MUTATION IS NOT VALID PYTHON  {path} | {e}", True
        with open(f, "w") as fh:
            fh.write(mutated)
        # Stops at the first failure: one failing test is a kill, and most come before the golden runs.
        r = run_capped([sys.executable, "-W", "error", "-m", "unittest", "discover", "-f", "-s", "tests", "-t", "."], dst)
        first = old.strip().splitlines()[0][:70]
        if r is None:
            return f"killed (runaway)  {path} | {first}", False
        if "Failed to import" in r.stderr or "ImportError" in r.stderr:
            return f"MUTATION BREAKS IMPORT  {path} | {first}", True
        killed = r.returncode != 0
        return f"{'killed  ' if killed else 'SURVIVED'}  {path} | {first}", not killed


def main():
    bad = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for line, failed in pool.map(attempt, M):
            print(line, flush=True)
            bad += failed
    print(f"{len(M) - bad} of {len(M)} mutations killed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

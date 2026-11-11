"""python3 -m implied spx | spx-day | record | trim | nfl EVENT | backtest | extrapolate | one-yard | fetch-settled | fetch-quotes  (--help on each)"""

import argparse
import http.client
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone

from . import backtest, kalshi, nfl, spx

SPOT_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1m&range=1d"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Yahoo's chart endpoint answers 429 with no User-Agent and with Python's default one.
USER_AGENT = "kalshi-implied (github.com/anaborne/kalshi-implied)"
# Settled markets and packed pregame quotes by series. Passing's quotes are the one-file-per-market
# cache in data/nfl; the other two series are ten times larger and ship as one file each.
SHIPPED = {"KXNFLPASSYDS": ("passyds_settled_20261104.json", None),
           "KXNFLRECYDS": ("recyds_settled_20261110.json", "recyds_quotes_20261110.json"),
           "KXNFLRSHYDS": ("rshyds_settled_20261110.json", "rshyds_quotes_20261110.json")}
# Everything a fetch can raise short of a bug: URLError, timeouts and resets are OSError,
# truncated or dropped responses are HTTPException, bad bodies are ValueError or KeyError.
FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError, KeyError)
# What a well-formed request with a malformed body raises when it is read.
BODY_ERRORS = (ValueError, KeyError, TypeError, IndexError)


def fetch_spot():
    req = urllib.request.Request(SPOT_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_spot(raw):
    """(price, aware datetime of that price) from a Yahoo chart response."""
    meta = raw["chart"]["result"][0]["meta"]
    return float(meta["regularMarketPrice"]), datetime.fromtimestamp(meta["regularMarketTime"], spx.ET)


def _load(path):
    with open(path) as f:
        return json.load(f)


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def _p(x):
    return "  -  " if x is None else f"{x:5.3f}"


def _now():
    return datetime.now(spx.ET)


def load_snapshot(folder, at):
    """(chain, books, spot, spot_time, book_time) for one recorded trio; a missing file is an
    error. Each round fetches Kalshi's book first, at the time in its file name."""
    for name in ("kxinx", "spot"):
        if not os.path.exists(f"{folder}/{name}-{at}.json"):
            raise SystemExit(f"{folder} has no {name}-{at}.json; replay needs the whole trio")
    trimmed = f"{folder}/spxw-{at}.json"
    chain = _load(trimmed if os.path.exists(trimmed) else f"{folder}/spx-{at}.json")
    spot, spot_time = read_spot(_load(f"{folder}/spot-{at}.json"))
    book_time = datetime.combine(folder_date(folder), datetime.strptime(at, "%H%M%S").time(), spx.ET)
    return chain, _load(f"{folder}/kxinx-{at}.json")["markets"], spot, spot_time, book_time


def folder_date(folder):
    return datetime.strptime(os.path.basename(os.path.normpath(folder)), "%Y%m%d").date()


def cmd_spx(a):
    if a.snapshot:
        chain, books, spot, spot_time, book_time = load_snapshot(a.snapshot, a.at)
        rate = kalshi.rate_of(_load(f"{a.snapshot}/series-KXINX.json")["series"])
        day = date.fromisoformat(a.date) if a.date else folder_date(a.snapshot)
    else:
        book_time, books = _now(), kalshi.markets(series_ticker="KXINX", status="open")
        spot, spot_time = read_spot(fetch_spot())
        chain, rate = spx.fetch_chain(), kalshi.fee_rate("KXINX")
        day = date.fromisoformat(a.date) if a.date else book_time.date()
    try:
        event, ms, close = spx.day_markets(books, day)
        curve, recorded, fwd = spx.implied_curves(chain, close, spot=spot, now=book_time if a.clock == "book" else spot_time,
                                                  keep_basis=a.keep_basis)
    except ValueError as e:
        raise SystemExit(str(e))
    rows = spx.compare(curve, ms, rate, recorded=recorded)
    print(f"{event}  book at {book_time:%H:%M:%S} ET  spot {spot:.2f} at {spot_time:%H:%M:%S} ET  "
          f"close {close:%H:%M} ET  option quotes as of {spx.chain_time(chain):%H:%M:%S} ET, parity forward then {fwd:.2f}"
          + ("" if curve.consistent else "  CROSSED: options bounds contradict each other"))
    print("fair and model floor/ceiling: chain rolled to the spot print; forced floor/ceiling: chain as recorded")
    print(f"{'bucket':>24}  {'bid':>5} {'ask':>5}   {'fair':>5}   {'model':>11}   {'forced':>11}   trade / flags")
    for r in sorted(rows, key=lambda r: r["lo"] + r["hi"], reverse=True):
        if r["hi"] < 0.005 and not r["bid"]:
            continue
        trade = f"{r['side']} {r['ev'] * 100:+.2f}c on {r['contracts']}" if r["side"] else ""
        flags = [f"{name}: {r[key]}" for name, key in (("model", "outside_current"), ("forced", "outside_recorded"))
                 if r[key]]
        print(f"{r['label']:>24}  {_p(r['bid'])} {_p(r['ask'])}   {_p(r['point'])}   "
              f"{_p(r['lo_current'])} {_p(r['hi_current'])}   {_p(r['lo_recorded'])} {_p(r['hi_recorded'])}   "
              + "  ".join([trade] + flags).strip())


def cmd_spx_day(a):
    """Every complete snapshot of a recorded day, scored for agreement, plus the settled bucket.
    A snapshot that cannot be scored is kept in the report with its error."""
    day = folder_date(a.snapshot)
    rate = kalshi.rate_of(_load(f"{a.snapshot}/series-KXINX.json")["series"])
    stamps = sorted(n[6:-5] for n in os.listdir(a.snapshot) if n.startswith("kxinx-"))
    settled = _load(a.settled)["markets"] if a.settled else []
    prefix = spx.event_prefix(day)
    won = next((m for m in settled if m["event_ticker"].startswith(prefix) and m.get("result") == "yes"), None)
    winner = won and won["ticker"]
    event, report = None, []
    for at in stamps:
        if not os.path.exists(f"{a.snapshot}/spot-{at}.json"):
            continue
        entry = {"at": at}
        try:
            chain, books, spot, spot_time, book_time = load_snapshot(a.snapshot, at)
            entry.update(spot=spot, spot_time=f"{spot_time:%H:%M:%S}")
            event, ms, close = spx.day_markets(books, day)
            curve, recorded, _ = spx.implied_curves(chain, close, spot=spot, now=book_time if a.clock == "book" else spot_time,
                                                    keep_basis=a.keep_basis)
            rows = spx.compare(curve, ms, rate, recorded=recorded)
            entry.update(consistent=curve.consistent, **spx.agreement(rows))
            entry["thin_quotes"] = [{"bucket": r["label"], "bid": r["bid"], "bid_size": r["bid_size"],
                                     "ask": r["ask"], "ask_size": r["ask_size"]} for r in rows
                                    if r["bid"] is not None and r["ask"] is not None and r["point"] is not None
                                    and min(r["bid_size"] or 0, r["ask_size"] or 0) < 10]
            entry["flags"] = [{"reading": name, "bucket": r["label"], "bid": r["bid"], "ask": r["ask"],
                               "floor": r[f"lo_{key}"], "ceiling": r[f"hi_{key}"], "flag": r[f"outside_{key}"]}
                              for r in rows for name, key in (("model", "current"), ("forced", "recorded"))
                              if r[f"outside_{key}"]]
        except BODY_ERRORS as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            report.append(entry)
            continue
        row = {r["ticker"]: r for r in rows}.get(winner)
        if row:
            entry["winner_kalshi_mid"] = None if row["bid"] is None or row["ask"] is None else (row["bid"] + row["ask"]) / 2
            entry["winner_options_fair"] = row["point"]
        report.append(entry)
    print(json.dumps({"event": event, "winner": winner,
                      "settled_value": won and won.get("expiration_value"),
                      "settled_status": won and won.get("status"), "snapshots": report}, indent=1))


def cmd_record(a):
    """Snapshot Kalshi KXINX books, a live S&P print and the CBOE chain every `--every` seconds
    until today's KXINX close, read from Kalshi's own close_time. The book is fetched first, at
    the time in the file names. A trio is written only once all three fetches succeed, so replay
    never mixes timestamps, and only once each body parses the way replay will read it; a failed
    round is reported on stderr and skipped. The close time and the fee file are fetched once up
    front. The recorder refuses a folder that already holds a recording, and refuses to start when
    today has no open KXINX event (after the close, or on a day the market is shut)."""
    today = _now()
    folder = a.out or os.path.join(REPO, "data", "snapshots", f"{today:%Y%m%d}")
    if os.path.isdir(folder) and any(n.startswith("kxinx-") for n in os.listdir(folder)):
        raise SystemExit(f"{folder} already holds a recording; pass a new --out")
    try:
        _, _, close = spx.day_markets(kalshi.markets(series_ticker="KXINX", status="open"), today.date())
    except ValueError as e:
        raise SystemExit(f"nothing to record: {e}")
    os.makedirs(folder, exist_ok=True)
    _write(os.path.join(folder, "series-KXINX.json"), kalshi.get("/series/KXINX"))
    while (now := _now()) < close:
        at = f"{now:%H%M%S}"
        try:
            trio = {"kxinx": {"markets": kalshi.markets(series_ticker="KXINX", status="open")},
                    "spot": fetch_spot(), "spx": spx.fetch_chain()}
            read_spot(trio["spot"])
            spx.chain_time(trio["spx"])
            spx.day_markets(trio["kxinx"]["markets"], today.date())
        except FETCH_ERRORS + BODY_ERRORS as e:
            print(f"{at} fetch failed, round skipped: {e!r}", file=sys.stderr)
        else:
            for name, obj in trio.items():
                _write(os.path.join(folder, f"{name}-{at}.json"), obj)
        time.sleep(a.every)


def cmd_trim(a):
    """Write spxw-HHMMSS.json beside each recorded spx-HHMMSS.json, keeping only the SPXW legs
    that expire on the folder's date: about 500 of the chain's 29,000 symbols, all replay uses."""
    day = folder_date(a.snapshot)
    for name in sorted(os.listdir(a.snapshot)):
        if not (name.startswith("spx-") and name.endswith(".json")):
            continue
        chain = _load(os.path.join(a.snapshot, name))
        prefix = f"SPXW{day:%y%m%d}"
        chain["data"]["options"] = [o for o in chain["data"]["options"] if o["option"][:10] == prefix
                                    and o["option"][10] in "CP"]
        _write(os.path.join(a.snapshot, "spxw-" + name[4:]), chain)
        print(f"{name}: kept {len(chain['data']['options'])}")


def cmd_nfl(a):
    ms = kalshi.markets(event_ticker=a.event)
    curves = nfl.curves(ms)
    names = [a.player] if a.player else sorted(curves)
    for name in names:
        if name not in curves:
            raise SystemExit(f"{name!r} not in {a.event}; players: {', '.join(sorted(curves))}")
        c = curves[name]
        flag = "" if c.consistent else f"  CROSSED at {[(x.t_low, x.t_high) for x in c.crossings]}"
        print(f"{name}{flag}")
        if a.range:
            lo, p, hi = nfl.yards_range(c, *a.range)
            print(f"  {a.range[0]}-{a.range[1]} yds  floor {_p(lo)}  fair {_p(p)}  ceiling {_p(hi)}"
                  + (f"  pays {1 / p:.2f}x at fair" if p else ""))
            if c.knots and not c.knots[0] <= a.range[0] - 0.5 <= a.range[1] + 0.5 <= c.knots[-1]:
                print(f"  reaches outside the two-sided strikes ({c.knots[0] + 0.5:.0f}+ to {c.knots[-1] + 0.5:.0f}+): "
                      "that part of fair is a fitted tail, which reads low in the backtest (README, Scope)")
        else:
            print("  strike   book bid/ask   envelope       fitted")
            for t, bid, ask in zip(c.t, c.lo, c.hi):
                lo, hi = c.envelope(t)
                print(f"  {t + 0.5:>5.0f}+   {_p(bid if bid > 0 else None)} {_p(ask if ask < 1 else None)}"
                      f"   [{lo:.2f}, {hi:.2f}]   {_p(c.point(t))}")


def shipped(series, settled=None, quotes=None):
    """(settled markets, packed quotes or None) for a series: the given files, else the shipped ones."""
    s, q = SHIPPED.get(series, (None, None))
    if not (settled or s):
        raise SystemExit(f"no settled file ships for {series}; pass --settled (and fetch-quotes for --quotes)")
    quotes = quotes or (q and os.path.join(REPO, "data", q))
    return _load(settled or os.path.join(REPO, "data", s)), quotes and _load(quotes)


def cmd_backtest(a):
    ms, quotes = shipped(a.series, a.settled, a.quotes)
    ladder = [m for m in ms if m.get("strike_type") == "greater"]
    recs = backtest.holdout(backtest.player_games(a.series, ms, quotes=quotes))
    print(json.dumps({"settled_by_result": dict(sorted(Counter(m.get("result") for m in ladder).items())),
                      "listed_after_snapshot": sum(backtest._ts(m["open_time"]) >= backtest.snapshot_time(m) for m in ladder),
                      "score": backtest.score(recs), "bootstrap": backtest.bootstrap(recs)}, indent=1))


def cmd_one_yard(a):
    """The 25-yard curve against Kalshi's own one-yard books, on the fixed one-yard sample."""
    precise, ladder = _load(a.precise), _load(a.settled)
    sample = backtest.one_yard_sample(precise)
    recs = backtest.one_yard(backtest.player_games("KXNFLPASSYDS", ladder),
                             backtest.player_games("KXNFLPASSYDSPRECISE", sample, min_rows=1, two_sided=False))
    print(json.dumps({"one_yard_markets": len(precise),
                      "one_yard_markets_traded": sum(float(m.get("volume_fp") or 0) > 0 for m in precise),
                      "sampled": len(sample), **backtest.one_yard_summary(recs)}, indent=1))


def cmd_extrapolate(a):
    """The curve outside the quoted strikes, on every series whose data ships."""
    out = {}
    for series in SHIPPED:
        ms, quotes = shipped(series)
        out[series] = backtest.extrapolation(backtest.player_games(series, ms, quotes=quotes))
    print(json.dumps(out, indent=1))


def cmd_fetch_quotes(a):
    """Pregame quotes for a settled file in one packed file, and a count of how many agree with
    the single-market cache in data/nfl wherever that already holds the same market. A market
    the batch endpoint had nothing for was read through that cache's own search, so it is
    counted apart and kept out of the comparison."""
    quotes, searched = backtest.fetch_quotes(a.series, _load(a.settled))
    _write(a.out, quotes)
    cached = {k: _load(path) for k in quotes
              if k not in searched and os.path.exists(path := os.path.join(backtest.CACHE, k + ".json"))}
    print(f"{len(quotes)} pregame quotes for {a.series} -> {a.out}; {len(searched)} came from the single-market search; "
          f"of the rest, {sum(quotes[k] == v for k, v in cached.items())} of the {len(cached)} that data/nfl also holds are identical")


def cmd_fetch_settled(a):
    since = int(datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc).timestamp())
    # Kalshi's max_close_ts is inclusive; one second off makes --until exclusive.
    until = int(datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc).timestamp()) - 1
    ms = kalshi.markets(series_ticker=a.series, status="settled", min_close_ts=since, max_close_ts=until)
    _write(a.out, ms)
    print(f"{len(ms)} settled {a.series} markets closing {a.since} to {a.until} -> {a.out}")


def _roll_options(p):
    p.add_argument("--keep-basis", action="store_true",
                   help="roll to live spot plus the chain's forward-minus-spot, not to live spot")
    p.add_argument("--clock", choices=("spot", "book"), default="spot",
                   help="time the roll runs to: the spot print's (default) or the Kalshi book's")


def main():
    p = argparse.ArgumentParser(prog="implied")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spx", help="Kalshi S&P close buckets against SPX options, live or replayed")
    s.add_argument("--date", help="expiry; default today (ET), or the snapshot folder's date")
    s.add_argument("--snapshot", help="replay a recorded folder (named YYYYMMDD) instead of fetching live")
    s.add_argument("--at", help="HHMMSS of the recorded trio, with --snapshot")
    _roll_options(s)
    s.set_defaults(fn=cmd_spx)
    sd = sub.add_parser("spx-day", help="score every snapshot in a recorded folder")
    sd.add_argument("--snapshot", required=True)
    sd.add_argument("--settled", help="saved /markets?event_ticker=... response after settlement")
    _roll_options(sd)
    sd.set_defaults(fn=cmd_spx_day)
    r = sub.add_parser("record", help="record S&P snapshots until today's KXINX close")
    r.add_argument("--out", help="folder; default <repo>/data/snapshots/YYYYMMDD (ET)")
    r.add_argument("--every", type=int, default=600, help="seconds between rounds")
    r.set_defaults(fn=cmd_record)
    t = sub.add_parser("trim", help="keep only same-day SPXW legs of a recorded folder's chains")
    t.add_argument("--snapshot", required=True)
    t.set_defaults(fn=cmd_trim)
    n = sub.add_parser("nfl", help="range prices off an NFL event's threshold ladders (passing, receiving or rushing yards)")
    n.add_argument("event")
    n.add_argument("--player")
    n.add_argument("--range", nargs=2, type=int, metavar=("LOW", "HIGH"))
    n.set_defaults(fn=cmd_nfl)
    b = sub.add_parser("backtest", help="hold-one-strike-out test on settled NFL markets")
    b.add_argument("--settled", help="default: the file shipped for --series")
    b.add_argument("--series", default="KXNFLPASSYDS")
    b.add_argument("--quotes", help="a fetch-quotes file; default: the one shipped for --series, else data/nfl")
    b.set_defaults(fn=cmd_backtest)
    x = sub.add_parser("extrapolate", help="the curve outside the quoted strikes, on the shipped series")
    x.set_defaults(fn=cmd_extrapolate)
    o = sub.add_parser("one-yard", help="the 25-yard curve against Kalshi's one-yard books")
    o.add_argument("--precise", default=os.path.join(REPO, "data", "precise_settled_20261104.json"))
    o.add_argument("--settled", default=os.path.join(REPO, "data", "passyds_settled_20261104.json"))
    o.set_defaults(fn=cmd_one_yard)
    f = sub.add_parser("fetch-settled", help="save a series' settled markets for the backtest")
    f.add_argument("--series", default="KXNFLPASSYDS")
    f.add_argument("--since", default="2026-09-01")
    f.add_argument("--until", required=True, help="UTC date, exclusive (Kalshi's max_close_ts is inclusive, so one second is taken off)")
    f.add_argument("--out", required=True)
    f.set_defaults(fn=cmd_fetch_settled)
    q = sub.add_parser("fetch-quotes", help="save pregame quotes for a settled file, by the batch candlestick endpoint")
    q.add_argument("--series", required=True)
    q.add_argument("--settled", required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(fn=cmd_fetch_quotes)
    a = p.parse_args()
    if a.cmd == "spx" and a.snapshot and not a.at:
        p.error("--snapshot needs --at")
    a.fn(a)


if __name__ == "__main__":
    main()

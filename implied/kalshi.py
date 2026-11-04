"""Read-only access to Kalshi's public market data. No key, no orders."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from fractions import Fraction

from .fees import TAKER_RATE

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def get(path, **params):
    url = f"{BASE}{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 7:
                raise
            wait = e.headers.get("Retry-After") if e.headers else None
            time.sleep(float(wait) if wait and wait.isdigit() else min(2 ** attempt, 60))


def markets(**params):
    out, cursor = [], None
    while True:
        page = get("/markets", limit=1000, cursor=cursor, **params)
        out.extend(page["markets"])
        cursor = page.get("cursor")
        if not cursor:
            return out


def fee_rate(series_ticker):
    """Taker rate for a series: the 7% base times the series' published multiplier."""
    return rate_of(get(f"/series/{series_ticker}")["series"])


def rate_of(series):
    if series.get("fee_type") != "quadratic":
        raise ValueError(f"{series['ticker']} has fee_type {series.get('fee_type')!r}; only quadratic is modelled")
    return TAKER_RATE * Fraction(str(series["fee_multiplier"]))


def quote(m):
    """(bid, ask) in dollars with an empty side as None.

    An empty bid prints as 0. An empty ask prints as 1 when the book has bids and as 0 when the
    book has no orders at all, so only an ask strictly inside (0, 1) is a quote.
    """
    return parse_quote(m.get("yes_bid_dollars"), m.get("yes_ask_dollars"))


def sizes(m):
    """(bid size, ask size) in contracts as Kalshi displays them, None where absent."""
    f = lambda k: None if m.get(k) in (None, "") else float(m[k])
    return f("yes_bid_size_fp"), f("yes_ask_size_fp")


def parse_quote(bid, ask):
    bid, ask = float(bid or 0), float(ask or 0)
    return (bid if 0 < bid < 1 else None), (ask if 0 < ask < 1 else None)


def candles(series_ticker, ticker, start_ts, end_ts, period=60):
    return get(f"/series/{series_ticker}/markets/{ticker}/candlesticks",
               start_ts=int(start_ts), end_ts=int(end_ts), period_interval=period)["candlesticks"]

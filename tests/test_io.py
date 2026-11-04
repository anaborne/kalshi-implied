import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date
from fractions import Fraction
from unittest import mock

from implied import backtest, kalshi, spx
from implied.dist import Bound, Curve, _end_slope


def response(obj):
    return io.BytesIO(json.dumps(obj).encode())


class TestKalshiClient(unittest.TestCase):
    def test_drops_none_params_and_retries_429_with_backoff(self):
        err = urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO())
        calls = []

        def fake(url, timeout):
            calls.append(url)
            if len(calls) < 3:
                raise err
            return response({"ok": 1})

        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep") as sleep:
            self.assertEqual(kalshi.get("/x", a=1, b=None), {"ok": 1})
        err.close()
        self.assertEqual(calls[0], f"{kalshi.BASE}/x?a=1")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_other_errors_raise_at_once_and_the_eighth_429_raises(self):
        for code, n in ((500, 1), (429, 8)):
            err = urllib.error.HTTPError("u", code, "", {}, io.BytesIO())
            with mock.patch("urllib.request.urlopen", side_effect=err) as u, mock.patch("time.sleep"):
                with self.assertRaises(urllib.error.HTTPError):
                    kalshi.get("/x")
            self.assertEqual(u.call_count, n)
            err.close()

    def test_429_waits_as_long_as_retry_after_says_and_caps_its_own_backoff_at_a_minute(self):
        hinted = urllib.error.HTTPError("u", 429, "", {"Retry-After": "7"}, io.BytesIO())
        bare = urllib.error.HTTPError("u", 429, "", {}, io.BytesIO())
        seq = [hinted] + [bare] * 6 + [response({"ok": 1})]

        def fake(url, timeout):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x

        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep") as sleep:
            self.assertEqual(kalshi.get("/x"), {"ok": 1})
        hinted.close()
        bare.close()
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [7.0, 2, 4, 8, 16, 32, 60])

    def test_markets_follows_the_cursor_at_the_largest_page(self):
        pages = [{"markets": [1, 2], "cursor": "c1"}, {"markets": [3], "cursor": ""}]
        with mock.patch.object(kalshi, "get", side_effect=pages) as g:
            self.assertEqual(kalshi.markets(series_ticker="S"), [1, 2, 3])
        self.assertEqual(g.call_args_list[0].kwargs, {"limit": 1000, "cursor": None, "series_ticker": "S"})
        self.assertEqual(g.call_args_list[1].kwargs["cursor"], "c1")

    def test_fee_multiplier_is_read_as_its_decimal(self):
        self.assertEqual(kalshi.rate_of({"ticker": "S", "fee_type": "quadratic", "fee_multiplier": 0.1}), Fraction(7, 1000))

    def test_fee_rate(self):
        with mock.patch.object(kalshi, "get", return_value={"series": {"ticker": "S", "fee_type": "quadratic", "fee_multiplier": 0.5}}):
            self.assertEqual(kalshi.fee_rate("S"), Fraction(35, 1000))
        with mock.patch.object(kalshi, "get", return_value={"series": {"ticker": "S", "fee_type": "flat"}}):
            with self.assertRaises(ValueError):
                kalshi.fee_rate("S")

    def test_sizes(self):
        self.assertEqual(kalshi.sizes({"yes_bid_size_fp": "1.00", "yes_ask_size_fp": "11.00"}), (1.0, 11.0))
        self.assertEqual(kalshi.sizes({"yes_bid_size_fp": "", "yes_ask_size_fp": None}), (None, None))
        self.assertEqual(kalshi.sizes({}), (None, None))

    def test_quote_reads_kalshi_empty_sides(self):
        # Observed on the live API 2026-11-04: a book with bids and no asks prints ask 1.0000; a
        # book with no orders at all prints ask 0.0000 (e.g. KXPLATINUMH-26NOV0416-T1502.99).
        self.assertEqual(kalshi.quote({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000"}), (None, None))
        self.assertEqual(kalshi.quote({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000"}), (None, None))
        self.assertEqual(kalshi.quote({"yes_bid_dollars": "0.2000", "yes_ask_dollars": "1.0000"}), (0.2, None))
        self.assertEqual(kalshi.quote({}), (None, None))
        self.assertEqual(kalshi.parse_quote("1.0000", "1.0000"), (None, None))
        self.assertEqual(kalshi.quote({"yes_bid_dollars": "0.4100", "yes_ask_dollars": "0.4300"}), (0.41, 0.43))


class TestPregameQuote(unittest.TestCase):
    # Listed 200 hours before its 17:50Z snapshot, so a search back to open_time spans three
    # 72-hour windows: 72, 72 and the last 56.
    m = {"ticker": "T1", "occurrence_datetime": "2026-11-01T21:00:00Z", "open_time": "2026-10-24T09:50:00Z"}

    def candle(self, end, bid, ask):
        return {"end_period_ts": end, "yes_bid": {"close_dollars": bid}, "yes_ask": {"close_dollars": ask}}

    def test_takes_the_last_one_minute_candle_before_the_snapshot_and_caches_it(self):
        snap = backtest.snapshot_time(self.m).timestamp()
        cs = [self.candle(snap - 7200, "0.30", "0.34"), self.candle(snap - 60, "0.31", "0.35"),
              self.candle(snap + 60, "0.90", "0.95")]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d):
            with mock.patch.object(kalshi, "candles", return_value=cs) as c:
                self.assertEqual(backtest.pregame_quote("S", self.m), (0.31, 0.35))
                self.assertEqual(backtest.pregame_quote("S", self.m), (0.31, 0.35))
                files = os.listdir(d)
            self.assertEqual(c.call_count, 1)
            self.assertEqual(c.call_args.kwargs["period"], 1)
            self.assertEqual(c.call_args.args[2:4], (snap - 72 * 3600, snap))
        self.assertEqual(files, [f"T1@{int(snap)}-p1.json"])

    def test_steps_back_in_72_hour_windows_to_open_time(self):
        snap = int(backtest.snapshot_time(self.m).timestamp())
        opened = int(backtest._ts(self.m["open_time"]).timestamp())
        self.assertEqual(snap - opened, 200 * 3600)
        old = self.candle(opened + 3600, "0.0000", "0.9900")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d), \
             mock.patch.object(kalshi, "candles", side_effect=[[], [], [old]]) as c:
            self.assertEqual(backtest.pregame_quote("S", self.m), (None, 0.99))
        windows = [call.args[2:4] for call in c.call_args_list]
        self.assertEqual(windows, [(snap - 72 * 3600, snap), (snap - 144 * 3600, snap - 72 * 3600), (opened, snap - 144 * 3600)])

    def test_a_market_listed_after_its_snapshot_is_not_fetched_or_cached(self):
        late = dict(self.m, open_time="2026-11-01T18:00:00Z")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d), \
             mock.patch.object(kalshi, "candles") as c:
            self.assertEqual(backtest.pregame_quote("S", late), (None, None))
            self.assertEqual(os.listdir(d), [])
        c.assert_not_called()

    def test_a_listing_a_minute_before_the_snapshot_is_searched(self):
        snap = int(backtest.snapshot_time(self.m).timestamp())
        recent = dict(self.m, open_time="2026-11-01T17:49:00Z")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d), \
             mock.patch.object(kalshi, "candles", return_value=[self.candle(snap - 30, "0.2000", "0.3000")]) as c:
            self.assertEqual(backtest.pregame_quote("S", recent), (0.2, 0.3))
        self.assertEqual(c.call_args.args[2:4], (snap - 60, snap))

    def test_a_book_with_no_candle_since_listing_is_empty(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d), \
             mock.patch.object(kalshi, "candles", return_value=[]) as c:
            self.assertEqual(backtest.pregame_quote("S", self.m), (None, None))
        self.assertEqual(c.call_count, 3)

    def test_a_candle_closing_exactly_at_the_snapshot_is_used(self):
        snap = backtest.snapshot_time(self.m).timestamp()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d), \
             mock.patch.object(kalshi, "candles", return_value=[self.candle(snap - 300, "0.30", "0.34"), self.candle(snap, "0.31", "0.35")]):
            self.assertEqual(backtest.pregame_quote("S", self.m), (0.31, 0.35))

    def test_empty_sides(self):
        snap = backtest.snapshot_time(self.m).timestamp()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(backtest, "CACHE", d):
            with mock.patch.object(kalshi, "candles", return_value=[self.candle(snap - 10, "0.0000", "0.0000")]):
                self.assertEqual(backtest.pregame_quote("S", self.m), (None, None))


class TestSpxEdges(unittest.TestCase):
    def test_fetch_chain(self):
        with mock.patch("urllib.request.urlopen", return_value=response({"data": {}})):
            self.assertEqual(spx.fetch_chain(), {"data": {}})

    def test_bid_above_ceiling_is_flagged(self):
        c = Curve([Bound(100, 0.1, 0.2), Bound(125, 0.0, 0.05)])
        m = {"ticker": "T", "yes_sub_title": "x", "strike_type": "greater", "floor_strike": 100,
             "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.32"}
        self.assertEqual(spx.compare(c, [m], 0.07)[0]["outside"], "bid above ceiling")
        m["yes_bid_dollars"], m["yes_ask_dollars"] = "0.12", "0.18"
        self.assertIsNone(spx.compare(c, [m], 0.07)[0]["outside"])


class TestAgreement(unittest.TestCase):
    def row(self, bid, ask, point, lo=0.0, hi=1.0, wc=0.5, union=None, current=None, recorded=None):
        return {"bid": bid, "ask": ask, "point": point, "lo": lo, "hi": hi, "lo_current": 0.25, "hi_current": 0.25 + wc,
                "bid_size": 5.0 if point == 0.15 else 20.0, "ask_size": 20.0,
                "outside": union, "outside_current": current, "outside_recorded": recorded}

    def test_counts_only_two_sided_buckets(self):
        rows = [self.row(0.40, 0.45, 0.42, 0.3, 0.6, 0.2),
                self.row(0.10, 0.12, 0.15, 0.0, 0.5, 0.1, current="x"),
                self.row(None, 0.03, 0.01, recorded="x"), self.row(0.2, 0.3, None),
                self.row(0.50, 0.55, 0.50, 0.4, 0.5, 0.3, union="x", current="x", recorded="x")]
        a = spx.agreement(rows)
        self.assertEqual(a["buckets"], 3)
        self.assertAlmostEqual(a["fair_inside_quote"], 2 / 3)
        self.assertAlmostEqual(a["mean_gap_to_mid"], (0.005 + 0.04 + 0.025) / 3)
        self.assertAlmostEqual(a["max_gap_to_mid"], 0.04)
        self.assertAlmostEqual(a["mean_quote_width"], (0.05 + 0.02 + 0.05) / 3)
        self.assertAlmostEqual(a["mean_width_current"], (0.2 + 0.1 + 0.3) / 3)
        self.assertAlmostEqual(a["mean_width_union"], (0.3 + 0.5 + 0.1) / 3)
        self.assertEqual((a["flagged_union"], a["flagged_current"], a["flagged_recorded"]), (1, 2, 2))
        self.assertEqual(a["thin"], 1)
        edge_rows = [dict(self.row(0.4, 0.45, 0.42), bid_size=10.0, ask_size=10.0),
                     dict(self.row(0.4, 0.45, 0.42), bid_size=None, ask_size=50.0)]
        self.assertEqual(spx.agreement(edge_rows)["thin"], 1)

    def test_no_two_sided_buckets(self):
        with self.assertRaises(ValueError):
            spx.agreement([self.row(None, 0.03, 0.01)])


class TestPchipEnd(unittest.TestCase):
    def test_end_slope(self):
        # e = ((2 + 1)(-1) + 5) / 2 = +1, the wrong sign for a falling first interval: zeroed.
        self.assertEqual(_end_slope(1.0, 1.0, -1.0, -5.0), 0.0)
        # e = ((2 + 1)(-1) + 0.5) / 2 = -1.25: kept.
        self.assertEqual(_end_slope(1.0, 1.0, -1.0, -0.5), -1.25)
        # A flat neighbour: e = (3)(-1) / 2 = -1.5, under the 3-secant cap that never binds here.
        self.assertEqual(_end_slope(1.0, 1.0, -1.0, 0.0), -1.5)


if __name__ == "__main__":
    unittest.main()

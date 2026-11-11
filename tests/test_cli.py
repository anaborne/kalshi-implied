import contextlib
import http.client
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from unittest import mock

from implied import __main__ as cli
from implied import kalshi, nfl, spx
from tests.test_spx import CLOSE, chain

READ_AT = CLOSE - timedelta(hours=1, minutes=50)
BOOK_AT = datetime(2026, 11, 4, 15, 0, tzinfo=spx.ET)
# Away from the chain's 7700 forward, and printed at READ_AT, not at the book time, so a replay
# that ignored either would change every number the tests check.
SPOT = 7712.5
# The chain's own current_price, 2 points under its 7700 forward, so --keep-basis has a basis to keep.
CHAIN_SPOT = 7698.0


def run(*argv):
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["implied", *argv]), contextlib.redirect_stdout(out):
        cli.main()
    return out.getvalue()


def kx(kind, floor=None, cap=None, bid="0.10", ask="0.12", event="KXINX-26NOV04H1600"):
    close = datetime.strptime(event[6:13] + event[14:18], "%y%b%d%H%M").replace(tzinfo=spx.ET)
    return {"ticker": f"{event}-{floor}-{cap}", "event_ticker": event, "yes_sub_title": f"{floor} to {cap}",
            "strike_type": kind, "floor_strike": floor, "cap_strike": cap,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "close_time": close.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def spot_json(price, when=READ_AT):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "regularMarketTime": int(when.timestamp())}}]}}


SERIES = {"series": {"ticker": "KXINX", "fee_type": "quadratic", "fee_multiplier": 0.5}}


class TestSpxCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "20261104")
        os.makedirs(self.dir)
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        books = [kx("between", 7700, 7724.9999, "0.40", "0.45"), kx("between", 7900, 7924.9999, "0.0000", "0.0100"),
                 kx("between", 7700, 7724.9999, event="KXINX-26NOV05H1600")]
        for name, obj in (("spx-150000", c), ("kxinx-150000", {"markets": books}),
                          ("spot-150000", spot_json(SPOT)), ("series-KXINX", SERIES)):
            cli._write(os.path.join(self.dir, f"{name}.json"), obj)
        self.offline = mock.patch("urllib.request.urlopen", side_effect=AssertionError("replay went online"))
        self.offline.start()

    def tearDown(self):
        self.offline.stop()
        self.tmp.cleanup()

    def replay(self, *extra):
        return run("spx", "--snapshot", self.dir, "--at", "150000", *extra)

    def test_replay_takes_the_date_from_the_folder_and_prints_one_row_per_live_bucket(self):
        out = self.replay()
        self.assertIn("KXINX-26NOV04H1600  book at 15:00:00 ET  spot 7712.50 at 14:10:00 ET  close 16:00 ET", out)
        self.assertIn("option quotes as of 14:00:00 ET", out)
        self.assertNotIn("CROSSED", out)
        self.assertEqual(out.count("7700 to 7724.9999"), 1)
        self.assertNotIn("7900", out)

    def test_replay_row_is_the_curve_rolled_to_the_spot_print_with_the_recorded_fee(self):
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        current, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=READ_AT)
        bucket = kx("between", 7700, 7724.9999, "0.40", "0.45")
        [row] = spx.compare(current, [bucket], Fraction(35, 1000), recorded=recorded)
        line = next(l for l in self.replay().splitlines() if "7700 to 7724.9999" in l)
        cells = [f"{row[k]:.3f}" for k in ("point", "lo_current", "hi_current", "lo_recorded", "hi_recorded")]
        self.assertEqual(line.split()[3:10], ["0.400", "0.450"] + cells)
        self.assertIn(f"{row['side']} {row['ev'] * 100:+.2f}c", line)
        [plain] = spx.compare(current, [bucket], Fraction(7, 100), recorded=recorded)
        self.assertNotEqual(plain["ev"], row["ev"])
        stale, _, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=BOOK_AT)
        self.assertNotEqual(f"{spx.kalshi_prob(stale, bucket)[1]:.3f}", cells[0])

    def test_the_fixture_chain_has_a_basis(self):
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        _, _, fwd = spx.implied_curves(c, CLOSE)
        self.assertAlmostEqual(fwd - CHAIN_SPOT, 2.0, delta=0.05)

    def test_clock_and_basis_options(self):
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        bucket = kx("between", 7700, 7724.9999, "0.40", "0.45")
        for extra, now, keep in (((), READ_AT, False), (("--clock", "book"), BOOK_AT, False),
                                 (("--keep-basis",), READ_AT, True)):
            current, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=now, keep_basis=keep)
            [row] = spx.compare(current, [bucket], Fraction(35, 1000), recorded=recorded)
            line = next(l for l in self.replay(*extra).splitlines() if "7700 to 7724.9999" in l)
            self.assertEqual(line.split()[5], f"{row['point']:.3f}", extra)

    def test_day_report_takes_the_same_options(self):
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        books = cli._load(os.path.join(self.dir, "kxinx-150000.json"))["markets"]
        _, ms, _ = spx.day_markets(books, CLOSE.date())
        for extra, now, keep in ((("--clock", "book"), BOOK_AT, False), (("--keep-basis",), READ_AT, True)):
            current, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=now, keep_basis=keep)
            expected = spx.agreement(spx.compare(current, ms, Fraction(35, 1000), recorded=recorded))
            [snap] = json.loads(run("spx-day", "--snapshot", self.dir, *extra))["snapshots"]
            self.assertEqual({k: snap[k] for k in expected}, expected, extra)

    def test_a_chain_from_another_day_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as e:
            self.replay("--date", "2026-11-05")
        self.assertIn("only same-day chains", str(e.exception))

    def test_rows_run_from_most_to_least_likely(self):
        path = os.path.join(self.dir, "kxinx-150000.json")
        buckets = [kx("between", k, k + 24.9999, "0.10", "0.15") for k in (7600, 7625, 7650, 7675, 7700, 7725, 7750)]
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        current, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=READ_AT)
        rows = spx.compare(current, buckets, Fraction(35, 1000), recorded=recorded)
        expected = [r["label"].split()[0] for r in sorted(rows, key=lambda r: r["lo"] + r["hi"], reverse=True)]
        listed = list(reversed(sorted(buckets, key=lambda m: expected.index(str(m["floor_strike"])))))
        self.assertNotEqual([str(m["floor_strike"]) for m in listed], expected)
        cli._write(path, {"markets": listed})
        labels = [l.split()[0] for l in self.replay().splitlines() if " to 7" in l]
        self.assertEqual(labels, expected)

    def test_buckets_the_options_rule_out_and_kalshi_does_not_bid_are_hidden(self):
        path = os.path.join(self.dir, "kxinx-150000.json")
        books = cli._load(path)
        books["markets"].append(kx("between", 7725, 7749.9999, "0.0000", "0.2000"))
        cli._write(path, books)
        out = self.replay()
        self.assertNotIn("7900 to 7924.9999", out)
        self.assertIn("7725 to 7749.9999", out)
        path = os.path.join(self.dir, "kxinx-150000.json")
        books = cli._load(path)
        books["markets"][1]["yes_bid_dollars"] = "0.0100"
        cli._write(path, books)
        self.assertIn("7900 to 7924.9999", self.replay())

    def test_replay_on_a_later_day_still_uses_the_folder_date(self):
        with mock.patch.object(cli, "_now", return_value=CLOSE + timedelta(days=6)):
            self.assertIn("KXINX-26NOV04H1600", self.replay())

    def test_no_markets_for_the_day_names_the_ticker_it_looked_for(self):
        with self.assertRaises(SystemExit) as e:
            self.replay("--date", "2026-11-06")
        self.assertIn("KXINX-26NOV06H", str(e.exception))

    def test_snapshot_needs_a_time(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run("spx", "--snapshot", self.dir)

    def test_incomplete_trio_is_refused(self):
        os.remove(os.path.join(self.dir, "spot-150000.json"))
        with self.assertRaises(SystemExit) as e:
            self.replay()
        self.assertIn("spot-150000.json", str(e.exception))

    def test_trimmed_chain_is_preferred(self):
        os.rename(os.path.join(self.dir, "spx-150000.json"), os.path.join(self.dir, "spxw-150000.json"))
        self.assertIn("7700 to 7724.9999", self.replay())

    def test_crossed_options_are_announced(self):
        path = os.path.join(self.dir, "spx-150000.json")
        c = cli._load(path)
        for o in c["data"]["options"]:
            if o["option"] == "SPXW261104C07750000":
                o["bid"], o["ask"] = 60.0, 61.0
        cli._write(path, c)
        self.assertIn("CROSSED", self.replay())

    def test_day_report_scores_every_complete_trio_keeps_errors_and_reads_the_winner(self):
        for name in ("kxinx", "spx"):
            with open(os.path.join(self.dir, f"{name}-150000.json")) as f, \
                 open(os.path.join(self.dir, f"{name}-151000.json"), "w") as g:
                g.write(f.read())
        cli._write(os.path.join(self.dir, "kxinx-152000.json"),
                   {"markets": [kx("between", 7700, 7724.9999, "0.0000", "0.0000")]})
        cli._write(os.path.join(self.dir, "spot-152000.json"), spot_json(SPOT))
        with open(os.path.join(self.dir, "spx-150000.json")) as f, open(os.path.join(self.dir, "spx-152000.json"), "w") as g:
            g.write(f.read())
        settled = os.path.join(self.tmp.name, "settled.json")
        cli._write(settled, {"markets": [dict(kx("between", 7700, 7724.9999), result="yes", expiration_value="7711.00",
                                               status="finalized"),
                                          dict(kx("between", 7675, 7699.9999), result="no")]})
        out = json.loads(run("spx-day", "--snapshot", self.dir, "--settled", settled))
        self.assertEqual(out["winner"], "KXINX-26NOV04H1600-7700-7724.9999")
        self.assertEqual((out["settled_value"], out["settled_status"]), ("7711.00", "finalized"))
        cli._write(settled, {"markets": [dict(kx("between", 7700, 7724.9999, event="KXINX-26NOV05H1600"), result="yes"),
                                          dict(kx("between", 7700, 7724.9999), result="yes", status="determined")]})
        other_day = json.loads(run("spx-day", "--snapshot", self.dir, "--settled", settled))
        self.assertEqual((other_day["winner"], other_day["settled_status"]), ("KXINX-26NOV04H1600-7700-7724.9999", "determined"))
        self.assertEqual([s["at"] for s in out["snapshots"]], ["150000", "152000"])
        good, bad = out["snapshots"]
        self.assertEqual(good["buckets"], 1)
        self.assertAlmostEqual(good["winner_kalshi_mid"], 0.425)
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        curve, _, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=READ_AT)
        self.assertAlmostEqual(good["winner_options_fair"], spx.kalshi_prob(curve, kx("between", 7700, 7724.9999))[1])
        curve, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=READ_AT)
        books = cli._load(os.path.join(self.dir, "kxinx-150000.json"))["markets"]
        _, ms, _ = spx.day_markets(books, CLOSE.date())
        expected = spx.agreement(spx.compare(curve, ms, Fraction(35, 1000), recorded=recorded))
        self.assertEqual({k: good[k] for k in expected}, expected)
        self.assertNotEqual(expected, spx.agreement(spx.compare(curve, ms, Fraction(35, 1000))))
        rows = spx.compare(curve, ms, Fraction(35, 1000), recorded=recorded)
        self.assertEqual(len(good["flags"]), sum(bool(r["outside_current"]) + bool(r["outside_recorded"]) for r in rows))
        self.assertEqual(bad["error"], "ValueError: no two-sided buckets")

    def test_day_report_lists_each_flag_with_its_reading_and_bounds(self):
        path = os.path.join(self.dir, "kxinx-150000.json")
        books = cli._load(path)
        books["markets"][1]["yes_bid_dollars"], books["markets"][1]["yes_ask_dollars"] = "0.5000", "0.6000"
        cli._write(path, books)
        [snap] = json.loads(run("spx-day", "--snapshot", self.dir))["snapshots"]
        flags = {f["reading"]: f for f in snap["flags"] if f["bucket"] == "7900 to 7924.9999"}
        self.assertEqual(set(flags), {"model", "forced"})
        for f in flags.values():
            self.assertEqual((f["bid"], f["ask"], f["flag"]), (0.5, 0.6, "bid above ceiling"))
            self.assertLess(f["ceiling"], 0.5)
            self.assertLessEqual(f["floor"], f["ceiling"])

    def test_day_report_keeps_a_snapshot_whose_files_do_not_parse(self):
        for name in ("kxinx", "spx"):
            with open(os.path.join(self.dir, f"{name}-150000.json")) as f, \
                 open(os.path.join(self.dir, f"{name}-151000.json"), "w") as g:
                g.write(f.read())
        cli._write(os.path.join(self.dir, "spot-151000.json"), {"chart": {"result": None}})
        good, bad = json.loads(run("spx-day", "--snapshot", self.dir))["snapshots"]
        self.assertEqual(good["buckets"], 1)
        self.assertEqual(bad, {"at": "151000", "error": "TypeError: 'NoneType' object is not subscriptable"})

    def test_day_report_leaves_the_kalshi_mid_out_when_the_winner_is_one_sided(self):
        path = os.path.join(self.dir, "kxinx-150000.json")
        books = cli._load(path)
        books["markets"][0]["yes_bid_dollars"] = "0.0000"
        books["markets"].append(kx("between", 7675, 7699.9999, "0.30", "0.35"))
        cli._write(path, books)
        settled = os.path.join(self.tmp.name, "settled.json")
        cli._write(settled, {"markets": [dict(kx("between", 7700, 7724.9999), result="yes")]})
        [snap] = json.loads(run("spx-day", "--snapshot", self.dir, "--settled", settled))["snapshots"]
        self.assertIsNone(snap["winner_kalshi_mid"])
        self.assertIsNotNone(snap["winner_options_fair"])


class TestRecord(unittest.TestCase):
    def record(self, failures):
        ticks = iter([CLOSE - timedelta(minutes=31), CLOSE - timedelta(minutes=30),
                      CLOSE - timedelta(minutes=20), CLOSE - timedelta(minutes=10), CLOSE])
        c, _ = chain()
        spots = [spot_json(7700.0), *failures, spot_json(7701.0)]

        def spot():
            x = spots.pop(0)
            if isinstance(x, BaseException):
                raise x
            return x

        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(cli, "_now", lambda: next(ticks)), \
             mock.patch("time.sleep") as sleep, \
             mock.patch.object(kalshi, "get", return_value=SERIES), \
             mock.patch.object(kalshi, "markets", return_value=[kx("between", 7700, 7724.9999)]) as m, \
             mock.patch.object(cli, "fetch_spot", spot), \
             mock.patch("implied.spx.fetch_chain", return_value=c), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            run("record", "--out", d, "--every", "5")
            return sorted(os.listdir(d)), err.getvalue(), sleep, m

    def test_writes_whole_trios_until_kalshis_close_and_skips_failed_rounds(self):
        for failure in (urllib.error.URLError("down"), TimeoutError("read timed out"),
                        http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"x"),
                        ValueError("bad json")):
            names, err, sleep, m = self.record([failure])
            self.assertEqual(names, ["kxinx-153000.json", "kxinx-155000.json", "series-KXINX.json",
                                     "spot-153000.json", "spot-155000.json", "spx-153000.json", "spx-155000.json"], failure)
            self.assertIn("154000 fetch failed", err)
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [5, 5, 5])
            self.assertEqual(m.call_args.kwargs, {"series_ticker": "KXINX", "status": "open"})

    def test_a_round_whose_body_does_not_parse_is_skipped(self):
        names, err, _, _ = self.record([{"chart": {"result": None}}])
        self.assertNotIn("spot-154000.json", names)
        self.assertIn("154000 fetch failed", err)

    def test_refuses_a_folder_that_already_holds_a_recording(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(kalshi, "get") as g, \
             mock.patch.object(kalshi, "markets") as m:
            cli._write(os.path.join(d, "kxinx-150000.json"), {"markets": []})
            with self.assertRaises(SystemExit) as e:
                run("record", "--out", d)
        self.assertIn("already holds a recording", str(e.exception))
        g.assert_not_called()
        m.assert_not_called()

    def test_after_the_close_it_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "_now", return_value=CLOSE + timedelta(hours=3)), \
             mock.patch.object(kalshi, "markets", return_value=[]), mock.patch.object(kalshi, "get") as g:
            d = os.path.join(tmp, "out")
            with self.assertRaises(SystemExit) as e:
                run("record", "--out", d)
            self.assertFalse(os.path.exists(d))
        self.assertIn("nothing to record", str(e.exception))
        g.assert_not_called()

    def test_a_bug_is_not_swallowed(self):
        with self.assertRaises(ZeroDivisionError):
            self.record([ZeroDivisionError()])

    def test_waits_ten_minutes_between_rounds_by_default(self):
        ticks = iter([CLOSE - timedelta(minutes=5)] * 2 + [CLOSE])
        c, _ = chain()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "_now", lambda: next(ticks)), \
             mock.patch("time.sleep") as sleep, mock.patch.object(kalshi, "get", return_value=SERIES), \
             mock.patch.object(kalshi, "markets", return_value=[kx("between", 7700, 7724.9999)]), \
             mock.patch.object(cli, "fetch_spot", return_value=spot_json(7700.0, CLOSE - timedelta(minutes=5))), \
             mock.patch("implied.spx.fetch_chain", return_value=c):
            run("record", "--out", d)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [600])

    def test_stops_at_a_half_day_close(self):
        half = datetime(2026, 11, 27, 13, 0, tzinfo=spx.ET)
        ticks = iter([half - timedelta(minutes=20), half - timedelta(minutes=10), half,
                      half + timedelta(minutes=10), half + timedelta(hours=3)])
        c, _ = chain()
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(cli, "_now", lambda: next(ticks)), \
             mock.patch("time.sleep"), \
             mock.patch.object(kalshi, "get", return_value=SERIES), \
             mock.patch.object(kalshi, "markets", return_value=[kx("between", 7700, 7724.9999, event="KXINX-26NOV27H1300")]), \
             mock.patch.object(cli, "fetch_spot", return_value=spot_json(7700.0)), \
             mock.patch("implied.spx.fetch_chain", return_value=c):
            run("record", "--out", d, "--every", "5")
            self.assertEqual(sorted(n for n in os.listdir(d) if n.startswith("kxinx")), ["kxinx-125000.json"])


class TestTrim(unittest.TestCase):
    def test_keeps_only_the_folders_same_day_spxw_legs(self):
        c, _ = chain()
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "20261104")
            os.makedirs(d)
            cli._write(os.path.join(d, "spx-150000.json"), c)
            cli._write(os.path.join(d, "kxinx-150000.json"), {"markets": []})
            run("trim", "--snapshot", d)
            kept = cli._load(os.path.join(d, "spxw-150000.json"))
            self.assertEqual(sorted(os.listdir(d)), ["kxinx-150000.json", "spx-150000.json", "spxw-150000.json"])
        symbols = [o["option"] for o in kept["data"]["options"]]
        self.assertEqual(len(symbols), 2 * 81)
        self.assertTrue(all(x.startswith("SPXW261104") for x in symbols))
        self.assertEqual(kept["data"]["current_price"], c["data"]["current_price"])


class TestNflCommand(unittest.TestCase):
    def ms(self):
        return [{"yes_sub_title": f"Q B: {k}+", "strike_type": "greater", "floor_strike": k - 0.5,
                 "yes_bid_dollars": f"{b:.2f}", "yes_ask_dollars": f"{b + 0.05:.2f}"}
                for k, b in ((200, 0.70), (225, 0.50), (250, 0.30), (275, 0.15))]

    def test_range_prices_the_whole_yards_inclusive(self):
        with mock.patch.object(kalshi, "markets", return_value=self.ms()):
            out = run("nfl", "E", "--player", "Q B", "--range", "230", "260")
        c = nfl.curves(self.ms())["Q B"]
        lo, p, hi = c.range(229.5, 260.5)
        self.assertIn(f"230-260 yds  floor {lo:5.3f}  fair {p:5.3f}  ceiling {hi:5.3f}  pays {1 / p:.2f}x at fair", out)

    def test_a_range_reaching_past_the_two_sided_strikes_is_marked(self):
        mark = "reaches outside the two-sided strikes (200+ to 275+)"
        with mock.patch.object(kalshi, "markets", return_value=self.ms()):
            for low, high, marked in ((230, 260, False), (200, 274, False), (199, 260, True), (230, 275, True), (0, 210, True)):
                self.assertEqual(mark in run("nfl", "E", "--player", "Q B", "--range", str(low), str(high)), marked, (low, high))

    def test_range_and_ladder_views(self):
        with mock.patch.object(kalshi, "markets", return_value=self.ms()):
            out = run("nfl", "E", "--player", "Q B", "--range", "230", "260")
            self.assertIn("230-260 yds", out)
            self.assertIn("pays", out)
            ladder = run("nfl", "E")
        self.assertIn("strike   book bid/ask   envelope       fitted", ladder)
        row = next(l for l in ladder.splitlines() if l.strip().startswith("225+"))
        self.assertEqual(row.split()[:5], ["225+", "0.500", "0.550", "[0.50,", "0.55]"])

    def test_ladder_view_shows_the_book_apart_from_the_envelope(self):
        ms = [{"yes_sub_title": "Q B: 200+", "strike_type": "greater", "floor_strike": 199.5,
               "yes_bid_dollars": "0.70", "yes_ask_dollars": "0.75"},
              {"yes_sub_title": "Q B: 225+", "strike_type": "greater", "floor_strike": 224.5,
               "yes_bid_dollars": "0.50", "yes_ask_dollars": "0.80"}]
        with mock.patch.object(kalshi, "markets", return_value=ms):
            out = run("nfl", "E")
        row = next(l for l in out.splitlines() if l.strip().startswith("225+"))
        self.assertEqual(row.split()[:5], ["225+", "0.500", "0.800", "[0.50,", "0.75]"])

    def test_unknown_player(self):
        with mock.patch.object(kalshi, "markets", return_value=self.ms()), self.assertRaises(SystemExit):
            run("nfl", "E", "--player", "Nobody")

    def test_crossed_book_is_shown(self):
        ms = self.ms()
        ms[2]["yes_bid_dollars"], ms[2]["yes_ask_dollars"] = "0.80", "0.85"
        with mock.patch.object(kalshi, "markets", return_value=ms):
            self.assertIn("CROSSED", run("nfl", "E"))


class TestBacktestCommand(unittest.TestCase):
    def test_prints_the_score(self):
        rows = [(149.5, 0.80, 0.84, True), (174.5, 0.65, 0.70, True), (199.5, 0.48, 0.52, False),
                (224.5, 0.30, 0.34, False)]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([], f)
        try:
            with mock.patch("implied.backtest.player_games", return_value=[("E", "Q", rows)]):
                out = json.loads(run("backtest", "--settled", f.name))
        finally:
            os.unlink(f.name)
        self.assertEqual(out["score"]["n"], 2)
        self.assertIn("gap_curve_minus_line", out["bootstrap"])


class TestShippedSeries(unittest.TestCase):
    ROWS = [(149.5, 0.80, 0.84, True), (174.5, 0.65, 0.70, True), (199.5, 0.48, 0.52, False), (224.5, 0.30, 0.34, False)]

    def loads(self):
        """_load answering by file name: quotes files hold one entry naming themselves."""
        return mock.patch.object(cli, "_load", side_effect=lambda p: {"from": os.path.basename(p)} if "quotes" in p else [])

    def test_each_series_reads_its_own_settled_file_and_quotes(self):
        want = {"KXNFLPASSYDS": ("passyds_settled_20261104.json", None),
                "KXNFLRECYDS": ("recyds_settled_20261110.json", {"from": "recyds_quotes_20261110.json"}),
                "KXNFLRSHYDS": ("rshyds_settled_20261110.json", {"from": "rshyds_quotes_20261110.json"})}
        self.assertEqual(set(cli.SHIPPED), set(want))
        for series, (settled, quotes) in want.items():
            with self.loads() as load, mock.patch("implied.backtest.player_games", return_value=[("E", "Q", self.ROWS)]) as pg:
                run("backtest", "--series", series)
            self.assertEqual(load.call_args_list[0].args[0], os.path.join(cli.REPO, "data", settled))
            self.assertEqual((pg.call_args.args[0], pg.call_args.kwargs), (series, {"quotes": quotes}))

    def test_given_files_replace_the_shipped_ones(self):
        with self.loads() as load, mock.patch("implied.backtest.player_games", return_value=[("E", "Q", self.ROWS)]) as pg:
            run("backtest", "--series", "KXNFLRECYDS", "--settled", "/x/s.json", "--quotes", "/x/my_quotes.json")
        self.assertEqual([c.args[0] for c in load.call_args_list], ["/x/s.json", "/x/my_quotes.json"])
        self.assertEqual(pg.call_args.kwargs, {"quotes": {"from": "my_quotes.json"}})

    def test_a_series_that_does_not_ship_needs_a_settled_file(self):
        with self.loads(), self.assertRaises(SystemExit) as e:
            run("backtest", "--series", "KXNFLTD")
        self.assertIn("--settled", str(e.exception))
        with self.loads(), mock.patch("implied.backtest.player_games", return_value=[("E", "Q", self.ROWS)]) as pg:
            run("backtest", "--series", "KXNFLTD", "--settled", "/x/s.json")
        self.assertEqual(pg.call_args.kwargs, {"quotes": None})

    def test_extrapolate_covers_every_shipped_series(self):
        with self.loads(), mock.patch("implied.backtest.player_games", return_value=[("E", "Q", self.ROWS)]) as pg:
            out = json.loads(run("extrapolate"))
        self.assertEqual(list(out), list(cli.SHIPPED))
        self.assertEqual(out["KXNFLRECYDS"]["ladders"], 1)
        self.assertEqual([(c.args[0], c.kwargs["quotes"]) for c in pg.call_args_list],
                         [("KXNFLPASSYDS", None), ("KXNFLRECYDS", {"from": "recyds_quotes_20261110.json"}),
                          ("KXNFLRSHYDS", {"from": "rshyds_quotes_20261110.json"})])

    def test_fetch_quotes_writes_one_file_and_counts_agreement_with_the_single_market_cache(self):
        # B went through the single-market search, so its agreement with the cache proves nothing.
        fetched = {"A@1-p1": [0.1, 0.2], "B@1-p1": [None, 0.5], "C@1-p1": [0.3, 0.4], "D@1-p1": [0.6, 0.7]}
        with tempfile.TemporaryDirectory() as d, mock.patch("implied.backtest.CACHE", d), \
             mock.patch("implied.backtest.fetch_quotes", return_value=(fetched, ["B@1-p1"])) as fq:
            cli._write(os.path.join(d, "A@1-p1.json"), [0.1, 0.2])
            cli._write(os.path.join(d, "B@1-p1.json"), [None, 0.5])
            cli._write(os.path.join(d, "D@1-p1.json"), [0.6, 0.8])
            cli._write(os.path.join(d, "s.json"), [{"ticker": "A"}])
            out = run("fetch-quotes", "--series", "S", "--settled", os.path.join(d, "s.json"), "--out", os.path.join(d, "q.json"))
            self.assertEqual(cli._load(os.path.join(d, "q.json")), fetched)
        fq.assert_called_once_with("S", [{"ticker": "A"}])
        self.assertIn("4 pregame quotes for S", out)
        self.assertIn("1 came from the single-market search; of the rest, 1 of the 2 that data/nfl also holds are identical", out)


class TestOneYardCommand(unittest.TestCase):
    def test_counts_and_score(self):
        precise = [{"floor_strike": 161.0, "volume_fp": "12.00"}, {"floor_strike": 162.0, "volume_fp": "0.00"},
                   {"floor_strike": 170.0}]
        ladder = [(149.5, 0.80, 0.84, True), (174.5, 0.60, 0.64, True), (199.5, 0.40, 0.44, False)]
        with tempfile.TemporaryDirectory() as d:
            p, l = os.path.join(d, "p.json"), os.path.join(d, "l.json")
            cli._write(p, precise)
            cli._write(l, [])
            games = {"KXNFLPASSYDS": [("KXNFLPASSYDS-E", "Q", ladder)],
                     "KXNFLPASSYDSPRECISE": [("KXNFLPASSYDSPRECISE-E", "Q", [(161.5, 0.70, 0.74, True)])]}
            with mock.patch("implied.backtest.player_games", side_effect=lambda series, ms, **kw: games[series]) as pg:
                out = json.loads(run("one-yard", "--precise", p, "--settled", l))
        self.assertEqual((out["one_yard_markets"], out["one_yard_markets_traded"], out["sampled"]), (3, 1, 2))
        self.assertEqual((out["strikes"], out["with_bid"]), (1, 1))
        self.assertEqual(pg.call_args_list[1].kwargs, {"min_rows": 1, "two_sided": False})
        self.assertEqual(pg.call_args_list[1].args[1], [{"floor_strike": 162.0, "volume_fp": "0.00"}, {"floor_strike": 170.0}])


class TestLiveFetches(unittest.TestCase):
    def test_spx_live_pulls_chain_books_spot_and_fee(self):
        c, _ = chain(half=0.05, spot=CHAIN_SPOT)
        books = [kx("between", 7700, 7724.9999, "0.40", "0.45")]
        with mock.patch("implied.spx.fetch_chain", return_value=c), \
             mock.patch.object(kalshi, "markets", return_value=books) as m, \
             mock.patch.object(cli, "fetch_spot", return_value=spot_json(SPOT)), \
             mock.patch.object(cli, "_now", return_value=BOOK_AT), \
             mock.patch.object(kalshi, "fee_rate", return_value=Fraction(7, 100)) as fee:
            out = run("spx")
        self.assertIn("KXINX-26NOV04H1600  book at 15:00:00 ET  spot 7712.50 at 14:10:00 ET", out)
        current, recorded, _ = spx.implied_curves(c, CLOSE, spot=SPOT, now=READ_AT)
        [row] = spx.compare(current, books, Fraction(7, 100), recorded=recorded)
        self.assertIn(f"0.400 0.450   {row['point']:.3f}", out)
        self.assertIn("7700 to 7724.9999", out)
        self.assertEqual(m.call_args.kwargs, {"series_ticker": "KXINX", "status": "open"})
        fee.assert_called_once_with("KXINX")

    def test_fetch_settled_defaults_to_the_season_from_sep_1(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(kalshi, "markets", return_value=[]) as m:
            run("fetch-settled", "--until", "2026-11-04", "--out", os.path.join(d, "s.json"))
        self.assertEqual(m.call_args.kwargs["min_close_ts"], 1788220800)
        self.assertEqual(m.call_args.kwargs["series_ticker"], "KXNFLPASSYDS")

    def test_backtest_reads_the_25_yard_series_by_default(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([], f)
        try:
            with mock.patch("implied.backtest.player_games", return_value=[("E", "Q", [(1, .4, .5, True), (2, .3, .4, True),
                                                                                       (3, .2, .3, False)])]) as pg:
                run("backtest", "--settled", f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(pg.call_args.args[0], "KXNFLPASSYDS")

    def test_fetch_settled_window_is_utc_and_until_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(kalshi, "markets", return_value=[{"ticker": "A"}]) as m:
            out = os.path.join(d, "s.json")
            run("fetch-settled", "--since", "2026-09-01", "--until", "2026-11-04", "--out", out)
            self.assertEqual(cli._load(out), [{"ticker": "A"}])
        self.assertEqual(m.call_args.kwargs, {"series_ticker": "KXNFLPASSYDS", "status": "settled",
                                              "min_close_ts": 1788220800, "max_close_ts": 1793750399})

    def test_candles_request(self):
        with mock.patch.object(kalshi, "get", return_value={"candlesticks": [1]}) as g:
            self.assertEqual(kalshi.candles("S", "T", 10.7, 20.2, period=1), [1])
        g.assert_called_once_with("/series/S/markets/T/candlesticks", start_ts=10, end_ts=20, period_interval=1)


class TestClock(unittest.TestCase):
    def test_now_is_eastern(self):
        self.assertIs(cli._now().tzinfo, spx.ET)


class TestSpot(unittest.TestCase):
    def test_fetch_names_this_repo_as_its_user_agent(self):
        body = io.BytesIO(json.dumps(spot_json(7712.5)).encode())
        with mock.patch("urllib.request.urlopen", return_value=body) as u:
            self.assertEqual(cli.fetch_spot(), spot_json(7712.5))
        self.assertEqual(u.call_args.args[0].get_header("User-agent"), "kalshi-implied (github.com/anaborne/kalshi-implied)")

    def test_read_spot_returns_price_and_eastern_time(self):
        price, when = cli.read_spot(spot_json(7712.5, datetime(2026, 11, 4, 19, 7, 47, tzinfo=timezone.utc)))
        self.assertEqual(price, 7712.5)
        self.assertEqual((when.hour, when.minute, when.second, when.tzinfo), (14, 7, 47, spx.ET))


if __name__ == "__main__":
    unittest.main()

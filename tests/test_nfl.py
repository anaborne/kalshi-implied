import unittest
from unittest import mock
from statistics import NormalDist

from implied import backtest, nfl


def market(player, strike, bid, ask, kind="greater", result=None, open_time="2026-11-01T12:00:00Z"):
    return {"ticker": f"T-{player}-{strike}", "event_ticker": "E", "yes_sub_title": f"{player}: {strike}+",
            "strike_type": kind, "floor_strike": strike - 0.5, "yes_bid_dollars": bid,
            "yes_ask_dollars": ask, "result": result, "open_time": open_time,
            "occurrence_datetime": "2026-11-01T21:00:00Z"}


class TestBounds(unittest.TestCase):
    def test_markets_without_a_floor_are_skipped(self):
        m = market("A. One", 250, "0.40", "0.45")
        m["floor_strike"] = None
        self.assertEqual(nfl.bounds([m]), {})

    def test_groups_by_player_and_reads_empty_sides(self):
        ms = [market("A. One", 250, "0.40", "0.45"), market("A. One", 275, "0.0000", "0.30"),
              market("B. Two", 200, "0.60", "1.0000"), market("B. Two", 225, "0.5", "0.6", kind="between")]
        b = nfl.bounds(ms)
        self.assertEqual(sorted(b), ["A. One", "B. Two"])
        self.assertEqual([(x.t, x.lo, x.hi) for x in b["A. One"]], [(249.5, 0.40, 0.45), (274.5, 0.0, 0.30)])
        self.assertEqual([(x.t, x.lo, x.hi) for x in b["B. Two"]], [(199.5, 0.60, 1.0)])

    def test_yards_range_is_inclusive_of_both_ends(self):
        n = NormalDist(260, 65)
        ms = [market("Q", k, f"{1 - n.cdf(k - 0.5) - 0.01:.4f}", f"{1 - n.cdf(k - 0.5) + 0.01:.4f}")
              for k in range(150, 376, 25)]
        c = nfl.curves(ms)["Q"]
        lo, p, hi = nfl.yards_range(c, 322, 370)
        truth = n.cdf(370.5) - n.cdf(321.5)
        self.assertAlmostEqual(p, truth, delta=0.01)
        self.assertTrue(lo <= truth <= hi)
        self.assertAlmostEqual(nfl.yards_range(c, 250, 250)[1], c.point(249.5) - c.point(250.5))
        with self.assertRaises(ValueError):
            nfl.yards_range(c, 300, 299)


class TestBacktest(unittest.TestCase):
    def test_in_game_listings_and_one_sided_books_are_dropped(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (150, 175, 200, 225)]
        ms.append(market("Q", 250, "0.20", "0.25", result="no", open_time="2026-11-01T18:30:00Z"))
        ms.append(market("Q", 275, "0.10", "0.15", result=None))
        quotes = {m["ticker"]: (0.3, 0.35) for m in ms}
        quotes["T-Q-225"] = (None, 0.3)
        orig = backtest.pregame_quote
        backtest.pregame_quote = lambda series, m: quotes[m["ticker"]]
        try:
            self.assertEqual(backtest.player_games("S", ms), [])
            quotes["T-Q-225"] = (0.2, 0.25)
            games = backtest.player_games("S", ms)
        finally:
            backtest.pregame_quote = orig
        self.assertEqual(len(games), 1)
        self.assertEqual([r[0] for r in games[0][2]], [149.5, 174.5, 199.5, 224.5])

    def test_snapshot_is_ten_minutes_before_a_kickoff_three_hours_before_occurrence(self):
        # occurrence 21:00Z, kickoff 18:00Z, snapshot 17:50Z
        self.assertEqual(int(backtest.snapshot_time(market("Q", 200, "0.3", "0.35")).timestamp()), 1793555400)

    def test_listing_cutoff_is_the_snapshot(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (150, 175, 200)]
        ms.append(market("Q", 225, "0.2", "0.25", result="no", open_time="2026-11-01T17:45:00Z"))
        ms.append(market("Q", 250, "0.2", "0.25", result="no", open_time="2026-11-01T17:55:00Z"))
        with mock.patch.object(backtest, "pregame_quote", return_value=(0.3, 0.35)):
            [(_, _, rows)] = backtest.player_games("S", ms)
        self.assertEqual([r[0] for r in rows], [149.5, 174.5, 199.5, 224.5])

    def test_only_threshold_markets_enter_a_ladder(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (150, 175, 200, 225)]
        ms.append(market("Q", 250, "0.20", "0.25", kind="between", result="no"))
        with mock.patch.object(backtest, "pregame_quote", return_value=(0.3, 0.35)):
            [(_, _, rows)] = backtest.player_games("S", ms)
        self.assertNotIn(249.5, [r[0] for r in rows])

    def test_locked_books_are_dropped(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (150, 175, 200, 225)]
        quotes = {m["ticker"]: (0.3, 0.35) for m in ms}
        quotes["T-Q-150"] = (0.3, 0.3)
        with mock.patch.object(backtest, "pregame_quote", side_effect=lambda s, m: quotes[m["ticker"]]):
            self.assertEqual(backtest.player_games("S", ms), [])

    def test_line_reference_interpolates_on_uneven_ladders(self):
        rows = [(149.5, 0.80, 0.80, True), (199.5, 0.40, 0.40, False)]
        self.assertAlmostEqual(backtest._linear(rows, 174.5), 0.60)
        self.assertAlmostEqual(backtest._linear(rows, 159.5), 0.72)

    def test_player_names_are_trimmed(self):
        self.assertEqual(nfl.player_of({"yes_sub_title": "  J. Goff  : 250+"}), "J. Goff")

    def test_holdout_hides_each_interior_strike_once(self):
        rows = [(149.5, 0.80, 0.84, True), (174.5, 0.65, 0.70, True), (199.5, 0.48, 0.52, False),
                (224.5, 0.30, 0.34, False), (249.5, 0.15, 0.19, False)]
        recs = backtest.holdout([("E", "Q", rows)])
        self.assertEqual([r["t"] for r in recs], [174.5, 199.5, 224.5])
        r = recs[1]
        self.assertAlmostEqual(r["line"], (0.675 + 0.32) / 2)
        self.assertAlmostEqual(r["book"], 0.50)
        self.assertEqual((r["lo"], r["hi"]), (0.30, 0.70))

    def test_unsorted_ladders_are_sorted_before_the_holdout(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (225, 150, 200, 175)]
        with mock.patch.object(backtest, "pregame_quote", side_effect=lambda s, m: (0.9 - m["floor_strike"] / 300, 0.95 - m["floor_strike"] / 300)):
            [(_, _, rows)] = backtest.player_games("S", ms)
        self.assertEqual([r[0] for r in rows], [149.5, 174.5, 199.5, 224.5])

    def test_a_market_listed_at_the_snapshot_is_dropped(self):
        ms = [market("Q", k, "0.30", "0.35", result="yes") for k in (150, 175, 200, 225)]
        ms.append(market("Q", 250, "0.2", "0.25", result="no", open_time="2026-11-01T17:50:00Z"))
        with mock.patch.object(backtest, "pregame_quote", return_value=(0.3, 0.35)):
            [(_, _, rows)] = backtest.player_games("S", ms)
        self.assertNotIn(249.5, [r[0] for r in rows])

    def test_score_counts_games_and_player_games_apart(self):
        rec = lambda e, p: {"event": e, "player": p, "hit": True, "curve": 0.5, "line": 0.5, "book": 0.5, "lo": 0, "hi": 1}
        s = backtest.score([rec("E1", "A"), rec("E1", "B"), rec("E2", "A"), rec("E2", "A"), rec("E3", "B")])
        self.assertEqual((s["n"], s["games"], s["player_games"]), (5, 3, 4))

    def test_bootstrap_defaults_to_2000_draws(self):
        recs = [{"event": f"E{g}", "player": "Q", "hit": g % 2 == 0, "curve": 0.5, "line": 0.5,
                 "book": 0.5, "lo": 0.0, "hi": 1.0} for g in range(3)]
        with mock.patch.object(backtest, "percentile_ci", side_effect=lambda xs: (len(xs), len(xs))) as ci:
            backtest.bootstrap(recs)
        self.assertEqual({call.args[0].__len__() for call in ci.call_args_list}, {2000})

    def test_a_mid_on_the_envelope_edge_counts_as_inside_despite_rounding(self):
        self.assertLess(0.7 - 0.4, 0.3)
        rec = {"event": "E", "player": "Q", "hit": True, "curve": 0.5, "line": 0.5, "book": 0.7 - 0.4,
               "lo": 0.3, "hi": 0.6}
        self.assertEqual(backtest.score([rec])["book_mid_inside_envelope"], 1.0)

    def test_one_yard_sample_is_yards_100_to_397_ending_in_0_2_5_or_7(self):
        ms = [{"floor_strike": k - 0.5} for k in range(0, 601)] + [{"floor_strike": None}]
        expected = sorted(list(range(100, 398, 5)) + list(range(102, 398, 5)))
        self.assertEqual([int(m["floor_strike"] + 0.5) for m in backtest.one_yard_sample(ms)], expected)

    def test_one_yard_scores_strikes_strictly_inside_the_ladder(self):
        ladder = [(149.5, 0.80, 0.84, True), (174.5, 0.60, 0.64, True), (199.5, 0.40, 0.44, False),
                  (224.5, 0.20, 0.24, False)]
        precise = [(140.5, 0.85, 0.87, True), (149.5, 0.81, 0.83, True), (161.5, 0.70, 0.74, True),
                   (174.5, 0.61, 0.63, True), (211.5, 0.29, 0.33, False), (230.5, 0.15, 0.19, False)]
        recs = backtest.one_yard([("KXNFLPASSYDS-26NOV01ABC", "Q", ladder)],
                                 [("KXNFLPASSYDSPRECISE-26NOV01ABC", "Q", precise),
                                  ("KXNFLPASSYDSPRECISE-26NOV01XYZ", "Q", precise)])
        self.assertEqual([r["t"] for r in recs], [161.5, 211.5])
        first = recs[0]
        curve = backtest.Curve([backtest.Bound(k, b, a) for k, b, a, _ in ladder])
        self.assertAlmostEqual(first["curve"], curve.point(161.5))
        self.assertAlmostEqual(first["line"], 0.82 + (0.62 - 0.82) * (161.5 - 149.5) / 25)
        self.assertEqual((first["bid"], first["ask"]), (0.70, 0.74))
        self.assertEqual((first["lo"], first["hi"]), curve.envelope(161.5))

    def test_one_yard_summary(self):
        r = lambda t, bid, ask, hit, curve: {"event": f"E{t % 2}", "player": "Q", "t": t, "hit": hit, "curve": curve,
                                             "line": 0.5, "bid": bid, "ask": ask, "lo": curve - 0.1, "hi": curve + 0.1,
                                             "crossed": t == 3}
        s = backtest.one_yard_summary([r(1, None, 0.99, True, 0.6), r(2, None, 0.99, False, 0.2),
                                       r(3, None, None, False, 0.1), r(4, 0.10, 0.15, True, 0.3),
                                       dict(r(5, None, 0.20, False, 0.30), lo=0.20)])
        self.assertEqual((s["strikes"], s["games"], s["with_bid"], s["with_ask"], s["empty"]), (5, 2, 1, 4, 1))
        self.assertEqual(s["asks"], {"0.15": 1, "0.20": 1, "0.99": 2})
        self.assertEqual(s["curve_where_asked"], [0.2, 0.6])
        # 0.15 is below its floor of 0.2; 0.20 sits exactly on its floor and is not below it.
        self.assertEqual(s["ask_below_forced_floor"], 1)
        self.assertEqual(s["hit_rate"], 0.4)
        self.assertEqual(s["bids"], [{"player": "Q", "yards": 4, "bid": 0.10, "ask": 0.15, "curve": 0.3}])
        self.assertEqual(s["crossed_curves"], 1)
        self.assertAlmostEqual(s["brier"]["curve"], (0.16 + 0.04 + 0.01 + 0.49 + 0.09) / 5)
        with self.assertRaises(ValueError):
            backtest.one_yard_summary([])

    def test_score_reports_quote_hits_spreads_and_neighbour_gaps(self):
        rows = [(149.5, 0.80, 0.84, True), (174.5, 0.65, 0.70, True), (199.5, 0.48, 0.52, False),
                (249.5, 0.20, 0.21, False), (274.5, 0.10, 0.12, False)]
        recs = backtest.holdout([("E", "Q", rows)])
        s = backtest.score(recs)
        self.assertEqual(s["neighbour_gaps"], {"25/25": 1, "25/50": 1, "50/25": 1})
        self.assertAlmostEqual(s["mean_spread"], (0.05 + 0.04 + 0.01) / 3)
        expected = sum(r["bid"] <= r["curve"] <= r["ask"] for r in recs) / 3
        self.assertAlmostEqual(s["inside_quote"]["curve"], expected)
        self.assertAlmostEqual(s["inside_quote"]["line"], sum(r["bid"] <= r["line"] <= r["ask"] for r in recs) / 3)
        self.assertEqual(s["crossed_curves"], 0)
        # 149.5 quoted under the strikes above it: whichever interior strike is hidden, the rest cross.
        crossed = [(149.5, 0.30, 0.35, True), (174.5, 0.60, 0.65, True), (199.5, 0.50, 0.55, False),
                   (224.5, 0.40, 0.45, False)]
        self.assertEqual(backtest.score(backtest.holdout([("E", "Q", crossed)]))["crossed_curves"], 2)

    def test_score(self):
        recs = [{"event": "E", "player": "Q", "hit": True, "curve": 0.6, "line": 0.5, "book": 0.7,
                 "lo": 0.5, "hi": 0.8},
                {"event": "E", "player": "Q", "hit": False, "curve": 0.2, "line": 0.3, "book": 0.1,
                 "lo": 0.15, "hi": 0.3}]
        s = backtest.score(recs)
        self.assertIsNone(s["inside_quote"])
        self.assertEqual((s["n"], s["games"], s["player_games"]), (2, 1, 1))
        self.assertAlmostEqual(s["brier"]["curve"], (0.16 + 0.04) / 2)
        self.assertAlmostEqual(s["brier"]["book"], (0.09 + 0.01) / 2)
        self.assertAlmostEqual(s["mean_abs_gap_to_book"]["line"], (0.2 + 0.2) / 2)
        self.assertEqual(s["book_mid_inside_envelope"], 0.5)
        with self.assertRaises(ValueError):
            backtest.score([])

    def test_bootstrap_is_reproducible_and_resamples_whole_games(self):
        rng = __import__("random").Random(5)
        recs = []
        for g in range(40):
            shift = rng.uniform(-0.05, 0.05)
            for j in range(3):
                book = rng.uniform(0.1, 0.9)
                recs.append({"event": f"E{g}", "player": f"P{j % 2}", "hit": rng.random() < book,
                             "curve": book + shift, "line": book + 0.02, "book": book, "lo": 0.0, "hi": 1.0})
        a, b = backtest.bootstrap(recs, draws=300), backtest.bootstrap(recs, draws=300)
        self.assertEqual(a, b)
        self.assertNotEqual(a, backtest.bootstrap(recs, draws=300, seed=1))
        for v in a.values():
            self.assertLess(v["ci95"][0], v["ci95"][1])
        exact = sum(abs(r["curve"] - r["book"]) for r in recs) / len(recs)
        self.assertAlmostEqual(a["gap_curve"]["value"], exact)
        single = backtest.bootstrap([dict(r, event="E0") for r in recs], draws=50)
        self.assertEqual(single["gap_curve"]["ci95"][0], single["gap_curve"]["ci95"][1])

    def test_bootstrap_draws_one_game_per_game_per_round(self):
        recs = [{"event": f"E{g}", "player": "Q", "hit": g % 2 == 0, "curve": 0.5, "line": 0.5,
                 "book": 0.5, "lo": 0.0, "hi": 1.0} for g in range(7)]
        calls = []
        real = __import__("random").Random

        class Counting(real):
            def choice(self, seq):
                calls.append(len(seq))
                return super().choice(seq)

        with mock.patch("random.Random", Counting):
            backtest.bootstrap(recs, draws=11)
        self.assertEqual(calls, [7] * 77)

    def test_percentile_interval_indices(self):
        self.assertEqual(backtest.percentile_ci(list(range(2000))), (50, 1949))
        self.assertEqual(backtest.percentile_ci(list(range(40))), (1, 38))

if __name__ == "__main__":
    unittest.main()

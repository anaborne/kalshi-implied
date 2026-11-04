import math
import unittest
from datetime import date, datetime, timedelta
from fractions import Fraction
from statistics import NormalDist

from implied import spx
from implied.dist import Curve

N = NormalDist()
EXPIRY = date(2026, 11, 4)
CLOSE = datetime(2026, 11, 4, 16, 0, tzinfo=spx.ET)
TWO_HOURS = 2 / 24 / 365


def black(f, k, sigma, t, call):
    v = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + v * v / 2) / v
    d2 = d1 - v
    if call:
        return f * N.cdf(d1) - k * N.cdf(d2)
    return k * N.cdf(-d2) - f * N.cdf(-d1)


def chain(f=7700.0, sigma=0.12, t=TWO_HOURS, half=0.10, strikes=range(7500, 7905, 5), sides="CP",
          itm_skew=0.0, spot=None):
    """Same-day chain priced off forward f with t years left to CLOSE. itm_skew drops
    in-the-money bids by that much, so an estimator that reads in-the-money mids is visibly wrong
    while every quote still brackets the true price. spot sets current_price apart from f."""
    opts = []
    for k in strikes:
        for cp in sides:
            p = black(f, k, sigma, t, cp == "C")
            itm = k < f if cp == "C" else k > f
            opts.append({"option": f"SPXW261104{cp}{k * 1000:08d}",
                         "bid": round(max(p - half - (itm_skew if itm else 0), 0.0), 2), "ask": round(p + half, 2)})
    opts.append({"option": "SPXW261105C07700000", "bid": 1.0, "ask": 2.0})
    opts.append({"option": "SPX261104C07700000", "bid": 1.0, "ask": 2.0})
    stamp = CLOSE - timedelta(seconds=t * 365 * 86400)
    data = {"current_price": f if spot is None else spot, "options": opts,
            "last_trade_time": stamp.strftime("%Y-%m-%dT%H:%M:%S")}
    return {"data": data}, (f, sigma, t)


def true_sf(f, sigma, t, k):
    v = sigma * math.sqrt(t)
    return N.cdf((math.log(f / k) - v * v / 2) / v)


class TestLegsAndForward(unittest.TestCase):
    def test_filters_root_expiry_and_dead_quotes_and_keeps_locked_ones(self):
        c, _ = chain()
        c["data"]["options"].append({"option": "SPXW261104C07905000", "bid": 0.0, "ask": 0.0})
        c["data"]["options"].append({"option": "SPXW261104C07910000", "bid": 3.0, "ask": 2.0})
        c["data"]["options"].append({"option": "SPXW261104C07915000", "bid": 1.5, "ask": 1.5})
        lb = spx.legs(c, EXPIRY)
        self.assertEqual(min(lb), 7500)
        self.assertNotIn(7905, lb)
        self.assertNotIn(7910, lb)
        self.assertEqual(lb[7915]["C"], (1.5, 1.5))

    def test_forward_from_parity(self):
        c, (f, _, _) = chain(half=0.05)
        self.assertAlmostEqual(spx.forward(spx.legs(c, EXPIRY), f), f, delta=0.05)

    def test_forward_does_not_follow_a_stale_spot(self):
        c, (f, _, _) = chain(half=0.05, spot=7697.0)
        self.assertAlmostEqual(spx.forward(spx.legs(c, EXPIRY), 7697.0), f, delta=0.05)

    def test_forward_is_a_median_so_a_few_stale_strikes_do_not_move_it(self):
        c, (f, _, _) = chain(half=0.05)
        lb = spx.legs(c, EXPIRY)
        for k in (7700, 7705, 7695):
            lb[k]["C"] = (lb[k]["C"][0] + 30, lb[k]["C"][1] + 30)
        self.assertAlmostEqual(spx.forward(lb, f), f, delta=0.1)

    def test_forward_reads_the_n_strikes_nearest_spot(self):
        c, (f, _, _) = chain(half=0.05)
        lb = spx.legs(c, EXPIRY)
        for k in lb:
            if abs(k - f) > 5:
                lb[k]["C"] = (lb[k]["C"][0] + 5, lb[k]["C"][1] + 5)
        self.assertAlmostEqual(spx.forward(lb, f, n=3), f, delta=0.05)
        self.assertAlmostEqual(spx.forward(lb, f), f + 5, delta=0.05)

    def test_forward_refusals(self):
        two = {100: {"C": (5.0, 5.2), "P": (4.9, 5.1)}, 105: {"C": (2.4, 2.6), "P": (7.3, 7.5)}}
        with self.assertRaises(ValueError):
            spx.forward(two, 100)
        three = {**two, 95: {"C": (8.0, 8.2), "P": (2.9, 3.1)}}
        self.assertAlmostEqual(spx.forward(three, 100), 100.1)
        c, (f, _, _) = chain(half=0.05)
        with self.assertRaises(ValueError):
            spx.forward(spx.legs(c, EXPIRY), f * 1.012)
        self.assertAlmostEqual(spx.forward(spx.legs(c, EXPIRY), f * 1.008), f, delta=0.05)

    def test_forward_skips_strikes_quoted_on_one_side(self):
        c, (f, _, _) = chain(half=0.05)
        lb = spx.legs(c, EXPIRY)
        for k in (7695, 7700, 7705):
            del lb[k]["P"]
        self.assertAlmostEqual(spx.forward(lb, f), f, delta=0.05)


class TestBounds(unittest.TestCase):
    def test_envelope_contains_the_true_distribution(self):
        c, (f, sigma, t) = chain()
        curve = Curve(spx.spread_bounds(spx.legs(c, EXPIRY)))
        self.assertTrue(curve.consistent)
        for k in range(7520, 7890, 5):
            lo, hi = curve.envelope(k)
            self.assertTrue(lo - 1e-9 <= true_sf(f, sigma, t, k) <= hi + 1e-9, k)

    def test_spread_bound_values(self):
        lb = {100: {"C": (5.0, 5.4), "P": (1.0, 1.2)}, 110: {"C": (3.0, 3.2), "P": (2.6, 3.0)}}
        b = {(x.t, round(x.lo, 12), round(x.hi, 12)) for x in spx.spread_bounds(lb)}
        self.assertIn((100, round((5.0 - 3.2) / 10, 12), 1.0), b)
        self.assertIn((110, 0.0, round((5.4 - 3.0) / 10, 12)), b)
        self.assertIn((100, round(1 - (3.0 - 1.0) / 10, 12), 1.0), b)
        self.assertIn((110, 0.0, round(1 - (2.6 - 1.2) / 10, 12)), b)

    def test_puts_alone_and_calls_alone_bound_the_curve_from_both_sides(self):
        for sides in "P", "C":
            c, (f, sigma, t) = chain(sides=sides)
            curve = Curve(spx.spread_bounds(spx.legs(c, EXPIRY)))
            for k in range(7520, 7890, 5):
                lo, hi = curve.envelope(k)
                self.assertTrue(lo - 1e-9 <= true_sf(f, sigma, t, k) <= hi + 1e-9, (sides, k))
            self.assertGreater(curve.envelope(7650)[0], 0.95)
            self.assertLess(curve.envelope(7750)[1], 0.05)

    def test_wide_spreads_tighten_the_envelope_far_from_the_money(self):
        # Two-hour sigma here is 7700 * 0.12 * sqrt(2 / 8760) = 13.96 points. Measured: at 7650
        # and 7750 (3.6 sigma) 3.2x narrower with spreads up to 50 points than with 5-point
        # spreads alone; at the money the same.
        c, _ = chain(half=0.30)
        lb = spx.legs(c, EXPIRY)
        narrow, wide = Curve(spx.spread_bounds(lb, 5)), Curve(spx.spread_bounds(lb, 50))
        width = lambda cv, k: cv.envelope(k)[1] - cv.envelope(k)[0]
        for k in (7650, 7750):
            self.assertLess(width(wide, k), width(narrow, k) / 3)
        self.assertAlmostEqual(width(wide, 7700), width(narrow, 7700))

    def test_default_spread_width_is_fifty_points(self):
        c, _ = chain(half=0.30)
        lb = spx.legs(c, EXPIRY)
        default = Curve(spx.spread_bounds(lb)).envelope(7780)
        self.assertEqual(default, Curve(spx.spread_bounds(lb, 50)).envelope(7780))
        self.assertNotEqual(default, Curve(spx.spread_bounds(lb, 25)).envelope(7780))


class TestDigitals(unittest.TestCase):
    def test_value_placement_and_weight(self):
        lb = {100: {"C": (5.0, 5.4)}, 110: {"C": (3.0, 3.2)}}
        [(k, s, w)] = spx.digital_estimates(lb, 50)
        self.assertEqual(k, 105)
        self.assertAlmostEqual(s, (5.2 - 3.1) / 10)
        self.assertAlmostEqual(w, 1 / ((0.4 + 0.2) / 2 / 10))

    def test_puts_below_the_forward(self):
        lb = {100: {"P": (1.0, 1.2)}, 110: {"P": (2.6, 3.0)}}
        [(k, s, _)] = spx.digital_estimates(lb, 200)
        self.assertEqual(k, 105)
        self.assertAlmostEqual(s, 1 + (1.1 - 2.8) / 10)

    def test_pairs_missing_the_side_are_skipped(self):
        lb = {100: {"C": (5, 6)}, 105: {"C": (3, 4)}, 110: {"P": (1, 2)}}
        self.assertEqual([e[0] for e in spx.digital_estimates(lb, 50)], [102.5])
        lb = {100: {"P": (1, 2)}, 105: {"C": (3, 4)}, 110: {"C": (2, 3)}}
        self.assertEqual([e[0] for e in spx.digital_estimates(lb, 50)], [107.5])

    def test_reads_only_out_of_the_money_legs(self):
        c, (f, sigma, t) = chain(half=0.05, itm_skew=1.0)
        for k, s, _ in spx.digital_estimates(spx.legs(c, EXPIRY), f):
            self.assertAlmostEqual(s, true_sf(f, sigma, t, k), delta=0.02, msg=k)

    def test_uneven_strike_grid(self):
        strikes = list(range(7500, 7600, 25)) + list(range(7600, 7800, 5)) + list(range(7800, 7905, 25))
        c, (f, sigma, t) = chain(half=0.0, strikes=strikes)
        for k, s, _ in spx.digital_estimates(spx.legs(c, EXPIRY), f):
            self.assertAlmostEqual(s, true_sf(f, sigma, t, k), delta=0.004, msg=k)

    def test_mid_digitals_beat_the_bound_midpoints(self):
        c, (f, sigma, t) = chain(half=0.30)
        with_mids, _, _ = spx.implied_curves(c, CLOSE)
        bounds_only = Curve(spx.spread_bounds(spx.legs(c, EXPIRY)))
        err = lambda cv: max(abs(cv.point(k) - true_sf(f, sigma, t, k)) for k in range(7620, 7790, 5))
        self.assertLess(err(with_mids), 0.02)
        self.assertLess(err(with_mids), err(bounds_only))


class TestCurves(unittest.TestCase):
    def test_point_matches_truth(self):
        # Measured error 0.0021 on this chain.
        c, (f, sigma, t) = chain(half=0.05)
        curve, recorded, fwd = spx.implied_curves(c, CLOSE)
        self.assertIs(curve, recorded)
        self.assertAlmostEqual(fwd, f, delta=0.05)
        for k in range(7620, 7790, 5):
            self.assertAlmostEqual(curve.point(k), true_sf(f, sigma, t, k), delta=0.005)

    def test_spot_shift_matches_a_chain_struck_at_the_new_spot(self):
        # Measured error 0.0021.
        stale, (f, sigma, t) = chain(half=0.05)
        current, recorded, _ = spx.implied_curves(stale, CLOSE, spot=7710.0, now=spx.chain_time(stale))
        for k in range(7650, 7770, 5):
            self.assertAlmostEqual(current.point(k), true_sf(7710.0, sigma, t, k), delta=0.005)
            self.assertAlmostEqual(recorded.point(k), true_sf(f, sigma, t, k), delta=0.005)

    def test_roll_forward_in_time_matches_a_chain_struck_later(self):
        # Chain stamped 25 minutes before the close, read 10 minutes before it. Measured 0.0078.
        stale, (f, sigma, _) = chain(half=0.02, t=25 / 60 / 24 / 365)
        current, _, _ = spx.implied_curves(stale, CLOSE, spot=7705.0, now=CLOSE - timedelta(minutes=10))
        t_now = 10 / 60 / 24 / 365
        for lo, hi in ((7675, 7700), (7700, 7725), (7690, 7710)):
            truth = true_sf(7705.0, sigma, t_now, lo) - true_sf(7705.0, sigma, t_now, hi)
            self.assertAlmostEqual(current.range(lo, hi)[1], truth, delta=0.015, msg=(lo, hi))

    def test_roll_is_exactly_the_standardized_log_moneyness_map(self):
        c, _ = chain(half=0.05, t=40 / 60 / 24 / 365)
        now = CLOSE - timedelta(minutes=13)
        current, recorded, fwd = spx.implied_curves(c, CLOSE, spot=7690.0, now=now)
        power = math.sqrt(13 / 40)
        for k_then, k_now in zip(recorded.knots, current.knots):
            self.assertAlmostEqual(k_now, 7690.0 * (k_then / fwd) ** power, places=9)
        for t_then, t_now in zip(recorded.t, current.t):
            self.assertAlmostEqual(t_now, 7690.0 * (t_then / fwd) ** power, places=9)

    def test_rolled_curve_centres_on_live_spot_not_on_the_stale_basis(self):
        c, _ = chain(half=0.05, spot=7698.0)
        current, _, fwd = spx.implied_curves(c, CLOSE, spot=7690.0, now=spx.chain_time(c))
        self.assertAlmostEqual(fwd, 7700.0, delta=0.05)
        self.assertAlmostEqual(current.point(7690.0), true_sf(7690.0, 0.12, TWO_HOURS, 7690.0), delta=0.005)

    def test_refusals(self):
        c, _ = chain()
        for kw in ({"spot": 7700.0}, {"now": CLOSE - timedelta(hours=1)}):
            with self.assertRaises(ValueError):
                spx.implied_curves(c, CLOSE, **kw)
        with self.assertRaisesRegex(ValueError, "now is at or after the close"):
            spx.implied_curves(c, CLOSE, spot=7700.0, now=CLOSE)
        late = dict(c, data=dict(c["data"], last_trade_time="2026-11-04T16:00:00"))
        with self.assertRaises(ValueError):
            spx.implied_curves(late, CLOSE)
        yesterday = dict(c, data=dict(c["data"], last_trade_time="2026-11-03T15:00:00"))
        with self.assertRaises(ValueError):
            spx.implied_curves(yesterday, CLOSE)


class TestDayMarkets(unittest.TestCase):
    def mk(self, event, close="2026-11-04T21:00:00Z"):
        return {"event_ticker": event, "ticker": event + "-X", "close_time": close}

    def test_prefix(self):
        self.assertEqual(spx.event_prefix(date(2026, 11, 4)), "KXINX-26NOV04H")
        self.assertEqual(spx.event_prefix(date(2027, 1, 2)), "KXINX-27JAN02H")
        self.assertEqual(spx.event_prefix(date(2026, 12, 31)), "KXINX-26DEC31H")

    def test_full_day_and_half_day(self):
        books = [self.mk("KXINX-26NOV04H1600"), self.mk("KXINX-26NOV05H1600", "2026-11-05T21:00:00Z")]
        event, ms, close = spx.day_markets(books, date(2026, 11, 4))
        self.assertEqual((event, len(ms), close), ("KXINX-26NOV04H1600", 1, CLOSE))
        event, _, close = spx.day_markets([self.mk("KXINX-26NOV27H1300", "2026-11-27T18:00:00Z")], date(2026, 11, 27))
        self.assertEqual((event, close.hour, close.tzinfo), ("KXINX-26NOV27H1300", 13, spx.ET))

    def test_close_is_the_earliest_close_time_in_the_event(self):
        books = [self.mk("KXINX-26NOV04H1600", "2026-11-04T21:00:00Z"), self.mk("KXINX-26NOV04H1600", "2026-11-04T21:05:00Z")]
        self.assertEqual(spx.day_markets(books, date(2026, 11, 4))[2], CLOSE)

    def test_none_or_two_events(self):
        for books in ([], [self.mk("KXINX-26NOV04H1300"), self.mk("KXINX-26NOV04H1600")]):
            with self.assertRaises(ValueError):
                spx.day_markets(books, date(2026, 11, 4))


class TestKalshiMapping(unittest.TestCase):
    def setUp(self):
        c, self.params = chain(half=0.05)
        self.curve, _, _ = spx.implied_curves(c, CLOSE)

    def m(self, kind, floor=None, cap=None, bid="0.10", ask="0.12"):
        return {"ticker": "T", "yes_sub_title": "x", "strike_type": kind, "floor_strike": floor,
                "cap_strike": cap, "yes_bid_dollars": bid, "yes_ask_dollars": ask}

    def test_every_strike_type_matches_the_true_probability(self):
        f, sigma, t = self.params
        sf = lambda k: true_sf(f, sigma, t, k)
        cases = [(self.m("less", cap=7690), 1 - sf(7690)),
                 (self.m("greater", floor=7724.9999), sf(7724.9999)),
                 (self.m("greater_or_equal", floor=7710), sf(7710))]
        cases += [(self.m("between", floor=k, cap=k + 24.9999), sf(k) - sf(k + 24.9999)) for k in range(7600, 7800, 25)]
        for m, truth in cases:
            lo, p, hi = spx.kalshi_prob(self.curve, m)
            self.assertAlmostEqual(p, truth, delta=0.006, msg=m["strike_type"])
            self.assertTrue(lo - 1e-9 <= truth <= hi + 1e-9, m)

    def test_each_strike_type_is_a_difference_of_p_at_least_on_whole_cents(self):
        c = self.curve
        at = lambda k: (c.envelope(k)[0], c.point(k), c.envelope(k)[1])
        lo_a, p_a, hi_a = at(7725.0)
        lo_b, p_b, hi_b = at(7750.0)
        self.assertEqual(spx.kalshi_prob(c, self.m("between", floor=7725, cap=7749.9999)),
                         (max(lo_a - hi_b, 0.0), p_a - p_b, hi_a - lo_b))
        self.assertEqual(spx.kalshi_prob(c, self.m("greater", floor=7724.9999)), at(7725.0))
        self.assertEqual(spx.kalshi_prob(c, self.m("greater", floor=7725)), at(7725.01))
        self.assertEqual(spx.kalshi_prob(c, self.m("greater_or_equal", floor=7725)), at(7725.0))
        lo, p, hi = at(7690.0)
        self.assertEqual(spx.kalshi_prob(c, self.m("less", cap=7690)), (1 - hi, 1 - p, 1 - lo))

    def test_a_ceiling_on_an_option_strike_holds_for_a_close_exactly_on_it(self):
        # Buy the 100 call, sell the 105: the spread pays its full 5 whenever X >= 105, so its ask
        # caps P(X >= 105). Here 1.0 / 5 = 0.2, and the envelope at exactly 105 must say so.
        lb = {100: {"C": (1.9, 2.0)}, 105: {"C": (1.0, 1.1)}}
        curve = Curve(spx.spread_bounds(lb))
        self.assertAlmostEqual(curve.envelope(105)[1], (2.0 - 1.0) / 5)
        self.assertEqual(curve.envelope(104.99)[1], 1.0)

    def test_cent_above(self):
        self.assertEqual([spx._cent_above(x) for x in (7699.9999, 7700.0, 7874.9999, 7700.004)], [7700.0, 7700.01, 7875.0, 7700.01])

    def test_keep_basis_moves_the_forward_by_the_chains_basis(self):
        c, _ = chain(half=0.05, spot=7698.0)
        plain, _, _ = spx.implied_curves(c, CLOSE, spot=7690.0, now=spx.chain_time(c))
        kept, _, fwd = spx.implied_curves(c, CLOSE, spot=7690.0, now=spx.chain_time(c), keep_basis=True)
        for a, b in zip(plain.knots, kept.knots):
            self.assertAlmostEqual(b, a * (7690.0 + fwd - 7698.0) / 7690.0, places=9)

    def test_unknown_strike_type(self):
        with self.assertRaises(ValueError):
            spx.kalshi_prob(self.curve, self.m("functional", floor=1))

    def test_flag_and_edge_on_a_cheap_ask(self):
        atm = self.m("between", floor=7700, cap=7724.9999, bid="0.01", ask="0.02")
        [row] = spx.compare(self.curve, [atm], rate=Fraction(7, 100))
        self.assertEqual(row["outside"], "ask below floor")
        self.assertEqual(row["side"], "yes")
        self.assertAlmostEqual(row["ev"], float(Fraction(str(row["point"])) - Fraction(2, 100) - Fraction(14, 10000)))

    def test_fee_multiplier_reaches_the_edge(self):
        m = self.m("between", floor=7700, cap=7724.9999, bid="0.01", ask="0.02")
        [half] = spx.compare(self.curve, [m], rate=Fraction(35, 1000))
        self.assertAlmostEqual(half["ev"], float(Fraction(str(half["point"])) - Fraction(2, 100) - Fraction(7, 10000)))

    def test_fair_inside_counts_a_point_on_the_bid_and_not_just_under_it(self):
        row = lambda p: {"bid": 0.40, "ask": 0.45, "point": p, "lo": 0, "hi": 1, "lo_current": 0, "hi_current": 1,
                         "bid_size": 50.0, "ask_size": 50.0, "outside": None, "outside_current": None, "outside_recorded": None}
        self.assertEqual(spx.agreement([row(0.40)])["fair_inside_quote"], 1.0)
        self.assertEqual(spx.agreement([row(0.3999)])["fair_inside_quote"], 0.0)

    def test_thin_is_under_ten_contracts(self):
        row = lambda b, a: {"bid": 0.40, "ask": 0.45, "point": 0.42, "lo": 0, "hi": 1, "lo_current": 0, "hi_current": 1,
                            "bid_size": b, "ask_size": a, "outside": None, "outside_current": None, "outside_recorded": None}
        self.assertEqual([spx.agreement([row(b, a)])["thin"] for b, a in ((9.0, 50.0), (10.0, 50.0), (50.0, 9.99))], [1, 0, 1])

    def test_digital_weight_floor_binds_on_a_tight_spread(self):
        lb = {100: {"C": (5.00, 5.01)}, 110: {"C": (3.00, 3.01)}}
        [(_, _, w)] = spx.digital_estimates(lb, 50)
        self.assertAlmostEqual(w, 1 / 0.005)

    def test_outside_is_exact_at_the_boundary(self):
        self.assertIsNone(spx._outside(0.30, 0.40, 0.40, 0.50))
        self.assertIsNone(spx._outside(0.50, 0.60, 0.40, 0.50))
        self.assertEqual(spx._outside(0.30, 0.3999, 0.40, 0.50), "ask below floor")
        self.assertEqual(spx._outside(0.5001, 0.60, 0.40, 0.50), "bid above ceiling")

    def test_flags_under_each_reading_of_a_stale_chain(self):
        stale, _ = chain(half=0.05)
        current, recorded, _ = spx.implied_curves(stale, CLOSE, spot=7730.0, now=spx.chain_time(stale))
        bucket = self.m("between", floor=7725, cap=7749.9999)
        now_lo, _, now_hi = spx.kalshi_prob(current, bucket)
        then_lo, _, then_hi = spx.kalshi_prob(recorded, bucket)
        self.assertGreater(now_lo, then_lo + 0.05)
        bucket["yes_bid_dollars"], bucket["yes_ask_dollars"] = "0.0100", f"{(now_lo + then_lo) / 2:.4f}"
        [row] = spx.compare(current, [bucket], 0.07, recorded=recorded)
        self.assertEqual(row["outside_current"], "ask below floor")
        self.assertIsNone(row["outside_recorded"])
        self.assertIsNone(row["outside"])
        self.assertAlmostEqual(row["lo"], then_lo)
        self.assertAlmostEqual(row["hi"], max(now_hi, then_hi))
        self.assertAlmostEqual(row["lo_current"], now_lo)
        self.assertAlmostEqual(row["hi_current"], now_hi)
        self.assertAlmostEqual(row["lo_recorded"], then_lo)
        self.assertAlmostEqual(row["hi_recorded"], then_hi)
        [alone] = spx.compare(current, [bucket], 0.07)
        self.assertEqual(alone["outside"], "ask below floor")
        self.assertEqual(alone["outside_recorded"], alone["outside_current"])

    def test_union_takes_the_higher_ceiling(self):
        stale, _ = chain(half=0.05)
        current, recorded, _ = spx.implied_curves(stale, CLOSE, spot=7730.0, now=spx.chain_time(stale))
        bucket = self.m("between", floor=7675, cap=7699.9999)
        _, _, now_hi = spx.kalshi_prob(current, bucket)
        _, _, then_hi = spx.kalshi_prob(recorded, bucket)
        self.assertGreater(then_hi, now_hi + 0.05)
        bucket["yes_bid_dollars"], bucket["yes_ask_dollars"] = f"{(now_hi + then_hi) / 2:.4f}", "0.9900"
        [row] = spx.compare(current, [bucket], 0.07, recorded=recorded)
        self.assertEqual(row["outside_current"], "bid above ceiling")
        self.assertIsNone(row["outside"])


if __name__ == "__main__":
    unittest.main()

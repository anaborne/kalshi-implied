import math
import random
import unittest
from statistics import NormalDist

from implied.dist import Bound, Curve, _end_slope, _log_phi, _pava_decreasing, _pchip_slopes


def normal_bounds(mu, sigma, strikes, half_spread):
    n = NormalDist(mu, sigma)
    out = []
    for k in strikes:
        s = 1 - n.cdf(k)
        out.append(Bound(k, max(s - half_spread, 0.0), min(s + half_spread, 1.0)))
    return out


class TestInputs(unittest.TestCase):
    def test_bound_rejects_inverted_out_of_range_and_nan(self):
        nan = float("nan")
        for lo, hi in ((0.6, 0.4), (-0.1, 0.5), (0.2, 1.1), (nan, 0.5), (0.2, nan)):
            with self.assertRaises(ValueError):
                Bound(1.0, lo, hi)
        for t in (nan, float("inf")):
            with self.assertRaises(ValueError):
                Bound(t, 0.2, 0.3)

    def test_empty(self):
        with self.assertRaises(ValueError):
            Curve([])

    def test_duplicate_strikes_take_the_tightest(self):
        c = Curve([Bound(10, 0.2, 0.9), Bound(10, 0.4, 0.6)])
        self.assertEqual(c.envelope(10), (0.4, 0.6))

    def test_empty_range(self):
        c = Curve([Bound(10, 0.4, 0.6)])
        for a, b in ((5, 5), (6, 5)):
            with self.assertRaises(ValueError):
                c.range(a, b)


class TestEnvelope(unittest.TestCase):
    def test_monotonicity_propagates_bounds(self):
        c = Curve([Bound(100, 0.60, 0.70), Bound(200, 0.20, 0.30)])
        self.assertEqual(c.envelope(50), (0.60, 1.0))
        self.assertEqual(c.envelope(150), (0.20, 0.70))
        self.assertEqual(c.envelope(250), (0.0, 0.30))

    def test_range_bounds_are_the_extreme_monotone_curves(self):
        c = Curve([Bound(100, 0.60, 0.70), Bound(200, 0.20, 0.30)])
        lo, _, hi = c.range(100, 200)
        self.assertAlmostEqual(lo, 0.30)
        self.assertAlmostEqual(hi, 0.50)

    def test_crossed_books_are_reported(self):
        c = Curve([Bound(100, 0.30, 0.40), Bound(200, 0.50, 0.55)])
        self.assertFalse(c.consistent)
        self.assertEqual(len(c.crossings), 1)
        x = c.crossings[0]
        self.assertEqual((x.t_low, x.t_high), (100, 200))
        self.assertAlmostEqual(x.gap, 0.10)

    def test_crossing_against_a_non_adjacent_strike(self):
        c = Curve([Bound(100, 0.30, 0.40), Bound(150, 0.35, 0.50), Bound(200, 0.45, 0.60)])
        self.assertEqual([(x.t_low, x.t_high) for x in c.crossings], [(100, 200)])

    def test_contradictory_bounds_at_one_strike(self):
        c = Curve([Bound(10, 0.2, 0.3), Bound(10, 0.5, 0.6)])
        self.assertFalse(c.consistent)
        self.assertEqual([(x.t_low, x.t_high) for x in c.crossings], [(10, 10)])
        self.assertAlmostEqual(c.crossings[0].gap, 0.2)

    def test_touching_books_are_not_crossed(self):
        self.assertTrue(Curve([Bound(100, 0.30, 0.40), Bound(200, 0.40, 0.45)]).consistent)


class TestPoint(unittest.TestCase):
    def test_monotone_and_inside_envelope_everywhere(self):
        rng = random.Random(7)
        for _ in range(200):
            strikes = sorted(rng.sample(range(0, 500, 5), rng.randint(2, 12)))
            mids = sorted((rng.random() for _ in strikes), reverse=True)
            bounds = [Bound(k, max(m - rng.random() * 0.1, 0), min(m + rng.random() * 0.1, 1))
                      for k, m in zip(strikes, mids)]
            c = Curve(bounds)
            xs = [strikes[0] - 50 + i * 0.7 for i in range(int((strikes[-1] - strikes[0] + 100) / 0.7))]
            vals = [c.point(x) for x in xs]
            if c.tail is None:
                vals = [v for v in vals if v is not None]
            for a, b in zip(vals, vals[1:]):
                self.assertGreaterEqual(a + 1e-12, b)
            for x in xs:
                v = c.point(x)
                if v is None:
                    continue
                lo, hi = c.envelope(x)
                self.assertTrue(lo - 1e-12 <= v <= hi + 1e-12, (x, lo, v, hi))
                self.assertTrue(0.0 <= v <= 1.0)

    def test_one_sided_and_empty_quotes_shape_the_envelope_not_the_fit(self):
        n = NormalDist(260, 65)
        clean = normal_bounds(260, 65, [174.5, 199.5, 224.5, 249.5, 274.5, 299.5, 324.5], 0.02)
        for extra in ([Bound(349.5, 0.0, 1.0)], [Bound(349.5, 0.0, 0.5)], [Bound(149.5, 0.5, 1.0)]):
            c = Curve(clean + extra)
            for x in (324.5, 349.5, 374.5):
                self.assertAlmostEqual(c.point(x), 1 - n.cdf(x), delta=0.006, msg=(extra, x))
        self.assertLessEqual(Curve(clean + [Bound(349.5, 0.0, 0.05)]).envelope(349.5)[1], 0.05)

    def test_no_two_sided_quotes_means_no_point(self):
        c = Curve([Bound(100, 0.0, 0.4), Bound(200, 0.3, 1.0)])
        self.assertEqual(c.knots, [])
        self.assertIsNone(c.point(150))
        self.assertIsNone(c.range(100, 200)[1])
        self.assertEqual(c.envelope(150), (0.3, 0.4))

    def test_weights_decide_between_conflicting_mids(self):
        wide = [Bound(90, 0.0, 1.0), Bound(120, 0.0, 1.0)]
        c = Curve(wide, [(100, 0.40, 100.0), (110, 0.60, 1.0)])
        self.assertAlmostEqual(c.point(100), (0.40 * 100 + 0.60) / 101)
        c = Curve(wide, [(100, 0.40, 1.0), (110, 0.60, 100.0)])
        self.assertAlmostEqual(c.point(110), (0.40 + 0.60 * 100) / 101)

    def test_noisy_non_monotone_mids_are_repaired(self):
        c = Curve([Bound(100, 0.40, 0.50), Bound(125, 0.45, 0.55), Bound(150, 0.20, 0.30)])
        self.assertGreaterEqual(c.point(100), c.point(125))

    def test_recovers_a_normal_from_its_own_quotes(self):
        n = NormalDist(260, 65)
        c = Curve(normal_bounds(260, 65, range(150, 376, 25), 0.0))
        for x in range(140, 390, 3):
            self.assertAlmostEqual(c.point(x), 1 - n.cdf(x), delta=0.004)
        self.assertAlmostEqual(c.tail[0], 260, delta=0.5)
        self.assertAlmostEqual(c.tail[1], 65, delta=0.5)

    def test_tails_go_to_one_and_zero(self):
        c = Curve(normal_bounds(260, 65, range(200, 326, 25), 0.02))
        self.assertGreater(c.point(-200), 0.999)
        self.assertLess(c.point(800), 0.001)

    def test_tails_join_the_interpolant(self):
        c = Curve(normal_bounds(260, 65, range(200, 326, 25), 0.02))
        for edge in (200, 325):
            self.assertAlmostEqual(c.point(edge - 1e-7), c.point(edge + 1e-7), places=5)

    def test_no_tail_model_without_shape(self):
        c = Curve([Bound(100, 0.4, 0.6)])
        self.assertIsNone(c.tail)
        self.assertEqual(c.point(100), 0.5)
        self.assertAlmostEqual(Curve([Bound(100, 0.3, 0.4)]).point(100), 0.35)
        self.assertIsNone(c.point(90))
        self.assertIsNone(c.range(90, 100)[1])
        self.assertEqual(c.range(90, 100)[0], 0.0)


class TestShape(unittest.TestCase):
    def test_pchip_interior_slope_on_an_uneven_grid(self):
        # h = (1, 2), secants (-0.5, -0.2); w1 = 2*2 + 1 = 5, w2 = 2 + 2*1 = 4;
        # m1 = (5 + 4) / (5 / -0.5 + 4 / -0.2) = 9 / -30 = -0.3.
        self.assertAlmostEqual(_pchip_slopes([0, 1, 3], [1.0, 0.5, 0.1])[1], -0.3)

    def test_two_knots_interpolate_in_a_straight_line(self):
        c = Curve([Bound(100, 0.79, 0.81), Bound(200, 0.19, 0.21)])
        self.assertAlmostEqual(c.point(125), 0.65)
        self.assertAlmostEqual(c.point(150), 0.50)

    def test_crossed_curve_is_still_monotone(self):
        c = Curve([Bound(200, .5, .7), Bound(250, .9, 1), Bound(300, .1, .2), Bound(350, .5, .7)])
        self.assertFalse(c.consistent)
        xs = [200 + i * 0.5 for i in range(301)]
        vals = [c.point(x) for x in xs]
        for a, b in zip(vals, vals[1:]):
            self.assertGreaterEqual(a + 1e-12, b)
        self.assertGreaterEqual(c.range(250, 251)[1], 0.0)
        # No envelope exists to clip into, so knots keep their isotonic fit: (0.5 + 0.7) / 2.
        self.assertAlmostEqual(c.point(200), 0.6)

    def test_tail_joins_its_knot_even_where_the_normal_underflows(self):
        c = Curve([Bound(100, .89, .91), Bound(110, .09, .11), Bound(200, .003, .005)])
        self.assertAlmostEqual(c.point(200), c.point(200.001), places=4)
        self.assertGreater(c.point(200.001), 0.0)
        c = Curve([Bound(100, .995, .997), Bound(190, .89, .91), Bound(200, .09, .11)])
        self.assertAlmostEqual(c.point(100), c.point(99.999), places=4)
        self.assertLess(c.point(99.999), 1.0)

    def test_far_left_tail_stays_finite_and_joins_its_knot(self):
        c = Curve([Bound(-100, .997, .999), Bound(100, .89, .91), Bound(110, .09, .11)])
        self.assertAlmostEqual(c.point(-100.001), c.point(-100), places=4)
        self.assertLess(c.point(-100.001), 1.0)

    def test_tail_band_excludes_its_endpoints(self):
        knots = [(100, 0.995), (150, 0.70), (200, 0.30), (250, 0.005)]
        c = Curve([Bound(t, 0.0, 1.0) for t, _ in knots], [(t, v, 1.0) for t, v in knots])
        inner = Curve([Bound(t, 0.0, 1.0) for t, _ in knots[1:3]], [(t, v, 1.0) for t, v in knots[1:3]])
        self.assertAlmostEqual(c.tail[0], inner.tail[0], places=9)
        self.assertAlmostEqual(c.tail[1], inner.tail[1], places=9)

    def test_tail_ignores_knots_pinned_at_zero_or_one(self):
        base = normal_bounds(260, 65, range(200, 326, 25), 0.01)
        pinned = Curve(base + [Bound(450, 0.001, 0.003), Bound(100, 0.997, 0.999)])
        self.assertAlmostEqual(pinned.tail[0], Curve(base).tail[0], delta=0.5)
        self.assertAlmostEqual(pinned.tail[1], Curve(base).tail[1], delta=0.5)

    def test_tail_fit_is_weighted(self):
        tight = [Bound(200, 0.80, 0.81), Bound(250, 0.50, 0.51)]
        loose = [Bound(300, 0.05, 0.45)]
        mu_tight_only = Curve(tight).tail[0]
        self.assertAlmostEqual(Curve(tight + loose).tail[0], mu_tight_only, delta=3.0)

    def test_knot_weights_come_from_quote_width(self):
        c = Curve([Bound(100, 0.38, 0.42), Bound(110, 0.30, 0.90)])
        w1, w2 = 1 / 0.04, 1 / 0.60
        self.assertAlmostEqual(c.point(100), (0.40 * w1 + 0.60 * w2) / (w1 + w2))

    def test_log_phi_matches_erfc_and_stays_finite_beyond_it(self):
        for z in (-3, -10, -25, -29.99):
            self.assertAlmostEqual(_log_phi(z), math.log(0.5 * math.erfc(-z / math.sqrt(2))), places=12)
        # Beyond -30 the asymptotic series is used; measured 2e-8 from erfc at -30.01 and -31.
        for z in (-30.01, -31, -35):
            self.assertAlmostEqual(_log_phi(z), math.log(0.5 * math.erfc(-z / math.sqrt(2))), delta=1e-7)
        self.assertTrue(math.isfinite(_log_phi(-1e4)))

    def test_end_slopes_are_the_three_point_estimate_not_the_secant(self):
        c = Curve([Bound(100, .79, .81), Bound(110, .49, .51), Bound(130, .09, .11)])
        # h = (10, 20), secants (-0.03, -0.02): ((2*10 + 20)(-0.03) - 10(-0.02)) / 30 = -1/30.
        self.assertAlmostEqual(c.m[0], -1 / 30)
        self.assertAlmostEqual(c.m[-1], _end_slope(20, 10, -0.02, -0.03))

    def test_knot_values_are_clipped_into_the_envelope(self):
        c = Curve([Bound(100, 0.2, 0.3)], [(100, 0.9, 1.0), (120, 0.1, 1.0)])
        self.assertAlmostEqual(c.s[0], 0.3)

    def test_estimates_are_clamped_to_unit_interval_even_when_crossed(self):
        crossed = [Bound(100, 0.3, 0.4), Bound(200, 0.5, 0.6)]
        c = Curve(crossed, [(100, 1.4, 1.0), (150, 0.5, 1.0), (200, -0.3, 1.0)])
        self.assertFalse(c.consistent)
        self.assertEqual((c.s[0], c.s[-1]), (1.0, 0.0))

    def test_tail_fit_uses_exactly_the_knots_between_half_a_percent_and_99_5(self):
        knots = [(100, 0.999), (150, 0.97), (200, 0.75), (250, 0.40), (300, 0.04), (350, 0.003)]
        c = Curve([Bound(t, 0.0, 1.0) for t, _ in knots], [(t, v, 1.0) for t, v in knots])
        pts = [(t, NormalDist().inv_cdf(v)) for t, v in knots if 0.005 < v < 0.995]
        mt = sum(t for t, _ in pts) / len(pts)
        mz = sum(z for _, z in pts) / len(pts)
        slope = sum((t - mt) * (z - mz) for t, z in pts) / sum((t - mt) ** 2 for t, _ in pts)
        self.assertAlmostEqual(c.tail[1], -1 / slope)
        self.assertAlmostEqual(c.tail[0], mt + mz * (-1 / slope))

    def test_knot_weight_is_the_inverse_quote_width_floored_at_one_cent(self):
        bounds = [Bound(100, 0.896, 0.900), Bound(150, 0.60, 0.62), Bound(200, 0.20, 0.30)]
        c = Curve(bounds)
        pts = [(b.t, NormalDist().inv_cdf((b.lo + b.hi) / 2), 1 / max(b.hi - b.lo, 0.01)) for b in bounds]
        sw = sum(w for *_, w in pts)
        mt = sum(t * w for t, _, w in pts) / sw
        mz = sum(z * w for _, z, w in pts) / sw
        slope = sum(w * (t - mt) * (z - mz) for t, z, w in pts) / sum(w * (t - mt) ** 2 for t, _, w in pts)
        self.assertAlmostEqual(c.tail[1], -1 / slope, places=9)
        self.assertAlmostEqual(c.tail[0], mt + mz * (-1 / slope), places=9)

    def test_ties_keep_the_first_strike_and_every_crossing_is_reported(self):
        c = Curve([Bound(100, 0.30, 0.40), Bound(150, 0.30, 0.40), Bound(200, 0.45, 0.50), Bound(250, 0.42, 0.44)])
        self.assertEqual([(x.t_low, x.t_high) for x in c.crossings], [(100, 200), (100, 250)])

    def test_a_tiny_crossing_is_still_a_crossing(self):
        self.assertFalse(Curve([Bound(100, 0.30, 0.40), Bound(200, 0.400001, 0.45)]).consistent)


class TestEstimates(unittest.TestCase):
    def test_estimates_drive_the_point_but_not_the_envelope(self):
        b = [Bound(100, 0.2, 0.8), Bound(200, 0.0, 0.5)]
        c = Curve(b, [(100, 0.7, 1.0), (150, 0.45, 1.0), (200, 0.1, 1.0)])
        self.assertAlmostEqual(c.point(150), 0.45)
        self.assertEqual(c.envelope(150), (0.0, 0.8))

    def test_estimates_are_clipped_into_the_envelope(self):
        c = Curve([Bound(100, 0.2, 0.3)], [(100, 0.9, 1.0), (120, 0.1, 1.0)])
        self.assertAlmostEqual(c.point(100), 0.3)

    def test_bad_estimates(self):
        b = [Bound(100, 0.2, 0.8)]
        nan = float("nan")
        for est in ([(100, 0.5, 0.0)], [(100, 0.5, 1.0), (100, 0.4, 1.0)], [(100, nan, 1.0)],
                    [(nan, 0.5, 1.0)], [(100, 0.5, nan)]):
            with self.assertRaises(ValueError):
                Curve(b, est)


class TestRange(unittest.TestCase):
    def setUp(self):
        self.c = Curve(normal_bounds(260, 65, range(150, 376, 25), 0.03))

    def test_whole_line_is_one(self):
        self.assertAlmostEqual(self.c.range(-1e4, 1e4)[1], 1.0, places=6)

    def test_range_floor_is_zero_when_the_bounds_overlap(self):
        c = Curve([Bound(100, 0.3, 0.6), Bound(200, 0.2, 0.5)])
        lo, _, hi = c.range(100, 200)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.6 - 0.2)

    def test_point_inside_range_bounds(self):
        for a in range(140, 380, 7):
            for b in range(a + 1, 400, 11):
                lo, p, hi = self.c.range(a, b)
                self.assertTrue(lo - 1e-12 <= p <= hi + 1e-12)


def minmax_isotonic(y, w):
    """Closed form of the weighted non-increasing least-squares fit:
    s_i = min over j <= i of max over k >= i of the weighted mean of y[j..k]."""
    n = len(y)
    avg = lambda j, k: sum(y[x] * w[x] for x in range(j, k + 1)) / sum(w[j:k + 1])
    return [min(max(avg(j, k) for k in range(i, n)) for j in range(i + 1)) for i in range(n)]


class TestPava(unittest.TestCase):
    def test_small_cases_by_hand(self):
        self.assertEqual(_pava_decreasing([1, 2], [1, 1]), [1.5, 1.5])
        self.assertEqual(_pava_decreasing([3, 2, 1], [1, 1, 1]), [3, 2, 1])
        out = _pava_decreasing([0.5, 0.7, 0.1], [1, 3, 1])
        self.assertAlmostEqual(out[0], 0.65)
        self.assertAlmostEqual(out[1], 0.65)

    def test_matches_the_closed_form_on_random_weighted_inputs(self):
        rng = random.Random(11)
        for _ in range(500):
            n = rng.randint(1, 9)
            y = [rng.random() for _ in range(n)]
            w = [rng.uniform(0.1, 5) for _ in range(n)]
            for a, b in zip(_pava_decreasing(y, w), minmax_isotonic(y, w)):
                self.assertAlmostEqual(a, b, places=12)


if __name__ == "__main__":
    unittest.main()

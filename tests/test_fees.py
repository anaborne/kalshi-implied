import unittest
from fractions import Fraction

from implied.fees import edge, taker_fee


class TestTakerFee(unittest.TestCase):
    def test_matches_published_range_for_100_contracts(self):
        # kalshi.com/fee-schedule, "Most markets": $0.07 - $1.75 for 100 contracts.
        self.assertEqual(taker_fee("0.50", 100), Fraction(175, 100))
        self.assertEqual(taker_fee("0.01", 100), Fraction(7, 100))
        self.assertEqual(taker_fee("0.99", 100), Fraction(7, 100))

    def test_rounds_up_per_order_not_per_contract(self):
        self.assertEqual(taker_fee("0.50", 1), Fraction(2, 100))
        self.assertEqual(taker_fee("0.50", 4), Fraction(7, 100))

    def test_rounds_up_where_rounding_to_nearest_would_go_down(self):
        # 0.07 * 100 * 0.03 * 0.97 = 20.37 cents -> 21; 0.07 * 10 * 0.1 * 0.9 = 6.3 cents -> 7.
        self.assertEqual(taker_fee("0.03", 100), Fraction(21, 100))
        self.assertEqual(taker_fee("0.10", 10), Fraction(7, 100))

    def test_exact_cent_is_not_rounded_up(self):
        # 0.07 * 100 * 0.5 * 0.5 = 1.75 exactly; float math lands above it.
        self.assertEqual(taker_fee(0.5, 100), Fraction(175, 100))

    def test_float_rate_is_read_as_its_decimal(self):
        self.assertEqual(taker_fee("0.50", 100, rate=0.07), Fraction(175, 100))

    def test_multiplier(self):
        self.assertEqual(taker_fee("0.50", 100, rate=Fraction(35, 1000)), Fraction(88, 100))

    def test_rejects_bad_input(self):
        for p in ("0", "1", "-0.1", "1.2"):
            with self.assertRaises(ValueError):
                taker_fee(p, 10)
        for n in (0, 0.5):
            with self.assertRaises(ValueError):
                taker_fee("0.5", n)


class TestEdge(unittest.TestCase):
    def test_lifts_a_cheap_ask(self):
        side, ev, n = edge(0.40, 0.25, 0.30, contracts=100)
        self.assertEqual(n, 100)
        self.assertEqual(side, "yes")
        self.assertEqual(ev, Fraction(40, 100) - Fraction(30, 100) - Fraction(147, 10000))

    def test_hits_a_rich_bid(self):
        side, ev, n = edge(0.20, 0.30, 0.35, contracts=100)
        self.assertEqual(n, 100)
        self.assertEqual(side, "no")
        self.assertEqual(ev, Fraction(80, 100) - Fraction(70, 100) - Fraction(147, 10000))

    def test_an_edge_of_exactly_zero_is_no_trade(self):
        # fee on 100 at 0.30 is 1.47 dollars, 0.0147 a contract
        self.assertEqual(edge("0.3147", None, 0.30, contracts=100), (None, 0, 0))
        self.assertEqual(edge("0.6853", 0.70, None, contracts=100), (None, 0, 0))

    def test_fee_can_kill_a_one_cent_edge(self):
        self.assertEqual(edge(0.51, 0.49, 0.50, contracts=100), (None, 0, 0))

    def test_prices_at_zero_or_one_are_not_quotes(self):
        self.assertEqual(edge(0.9, 0.0, 1.0), (None, 0, 0))

    def test_fee_is_charged_on_the_displayed_size(self):
        # 11 lots at 0.98: 0.07 * 11 * 0.98 * 0.02 = 1.5092 cents -> 2 cents, 0.2/11 cents a contract.
        side, ev, n = edge("0.996", 0.36, 0.98, contracts=100, ask_size=11.0)
        self.assertEqual((side, n), ("yes", 11))
        self.assertEqual(ev, Fraction(996, 1000) - Fraction(98, 100) - Fraction(2, 100) / 11)
        _, _, n = edge("0.10", 0.36, 0.98, contracts=100, bid_size=1.0)
        self.assertEqual(n, 1)
        self.assertEqual(edge("0.996", None, 0.98, contracts=5, ask_size=11.0)[2], 5)
        self.assertEqual(edge("0.996", None, 0.98, contracts=5, ask_size=0.4), (None, 0, 0))
        self.assertEqual(edge("0.10", 0.36, None, contracts=5, bid_size=0.0), (None, 0, 0))

    def test_a_bid_of_one_is_not_a_quote(self):
        self.assertEqual(edge(0.1, 1.0, None), (None, 0, 0))

    def test_fractional_sizes_round_down(self):
        self.assertEqual(edge("0.996", None, 0.98, contracts=100, ask_size=10.6)[2], 10)

    def test_missing_sides(self):
        self.assertEqual(edge(0.5, None, None), (None, 0, 0))
        self.assertEqual(edge(0.9, None, 0.5)[0], "yes")


if __name__ == "__main__":
    unittest.main()

import unittest

import rates
from tests.support import example_config

CFG = example_config()
R = CFG["pseg_rs_rates"]


class DayCostTests(unittest.TestCase):
    def test_winter_is_flat(self):
        self.assertAlmostEqual(rates.day_cost(10, "2026-11-15", 900, CFG), 10 * R["winter"])

    def test_summer_below_tier(self):
        self.assertAlmostEqual(rates.day_cost(20, "2026-07-10", 100, CFG), 20 * R["summer_first_tier"])

    def test_summer_crossing_tier_splits(self):
        cost = rates.day_cost(20, "2026-07-20", 590, CFG)
        self.assertAlmostEqual(cost, 10 * R["summer_first_tier"] + 10 * R["summer_over_tier"])

    def test_summer_above_tier(self):
        self.assertAlmostEqual(rates.day_cost(20, "2026-07-25", 700, CFG), 20 * R["summer_over_tier"])

    def test_negative_kwh_costs_nothing(self):
        self.assertEqual(rates.day_cost(-5, "2026-07-25", 0, CFG), 0.0)

    def test_september_is_summer_october_is_not(self):
        self.assertTrue(rates.is_summer("2026-09-30", CFG))
        self.assertFalse(rates.is_summer("2026-10-01", CFG))


class MarginalRateTests(unittest.TestCase):
    def test_marginal_rate(self):
        self.assertEqual(rates.marginal_rate("2026-01-05", 0, CFG), R["winter"])
        self.assertEqual(rates.marginal_rate("2026-07-05", 599, CFG), R["summer_first_tier"])
        self.assertEqual(rates.marginal_rate("2026-07-05", 600, CFG), R["summer_over_tier"])

    def test_rate_label(self):
        self.assertEqual(rates.rate_label("2026-11-01", 0, 10, CFG), f"winter ${R['winter']:.4f}")
        self.assertEqual(rates.rate_label("2026-07-01", 0, 10, CFG), f"summer ${R['summer_first_tier']:.4f}")
        self.assertIn("&gt;600 kWh", rates.rate_label("2026-07-30", 650, 10, CFG))
        self.assertEqual(rates.rate_label("2026-07-20", 595, 10, CFG), "summer, crosses 600 kWh tier")


class BlendedRateTests(unittest.TestCase):
    def test_blend_sits_between_cheapest_and_dearest(self):
        blended = rates.blended_annual_rate(CFG, 11_400)
        self.assertGreater(blended, R["winter"])
        self.assertLess(blended, R["summer_over_tier"])

    def test_zero_consumption_falls_back_to_winter(self):
        self.assertEqual(rates.blended_annual_rate(CFG, 0), R["winter"])


if __name__ == "__main__":
    unittest.main()

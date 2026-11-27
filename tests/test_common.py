import unittest

import common


class PctTests(unittest.TestCase):
    def test_zero_total_is_zero(self):
        self.assertEqual(common.pct(5, 0), 0.0)

    def test_uncapped_over_100(self):
        self.assertEqual(common.pct(118, 100), 118.0)

    def test_rounds_to_one_decimal(self):
        self.assertEqual(common.pct(1, 3), 33.3)


class FormattingTests(unittest.TestCase):
    def test_fmt_date_has_no_leading_zero(self):
        self.assertEqual(common.fmt_date("2026-04-02"), "April 2, 2026")

    def test_pto_duration_steps(self):
        cases = {
            "2026-04-01": "pre-PTO",
            "2026-04-02": "PTO day",
            "2026-04-03": "1 day since PTO",
            "2026-04-15": "13 days since PTO",
            "2026-04-16": "2w since PTO",
            "2026-04-18": "2w 2d since PTO",
            "2026-06-01": "2mo since PTO",
            "2027-04-02": "1yr since PTO",
        }
        for d, expected in cases.items():
            with self.subTest(d=d):
                self.assertEqual(common.pto_duration(d), expected)


class TargetTests(unittest.TestCase):
    def test_every_month_has_a_target(self):
        self.assertEqual(sorted(common.MONTHLY_TARGETS), list(range(1, 13)))

    def test_pto_month_is_prorated(self):
        # April 2026: PTO on the 2nd → 29 of 30 days
        self.assertEqual(common.monthly_target(2026, 4), round(1260 * 29 / 30))

    def test_other_months_are_not_prorated(self):
        self.assertEqual(common.monthly_target(2026, 5), 1430)
        self.assertEqual(common.monthly_target(2027, 4), 1260)

    def test_daily_target_is_month_over_30(self):
        self.assertAlmostEqual(common.daily_target(2026, 11), 800 / 30)


class PracticalMaxTests(unittest.TestCase):
    def test_ceiling_is_reachable_but_above_the_best_day(self):
        # Nameplate x sun-hours (92 kWh) is unreachable behind 8.12 kW AC inverters;
        # the ceiling should sit just above the 70.3 kWh record, not 20 kWh past it.
        self.assertGreater(common.PRACTICAL_MAX_KWH, 70.3)
        self.assertLess(common.PRACTICAL_MAX_KWH, common.SYSTEM_CAPACITY_KW * 8)


class RatingTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [(0.0, "POOR"), (0.449, "POOR"), (0.45, "FAIR"), (0.699, "FAIR"),
                 (0.70, "GOOD"), (0.899, "GOOD"), (0.90, "EXCELLENT"), (2.5, "EXCELLENT")]
        for ratio, label in cases:
            with self.subTest(ratio=ratio):
                self.assertEqual(common.rating(ratio)[0], label)

    def test_subject_dots_follow_rating(self):
        self.assertEqual([common.rating(r)[2] for r in (0.1, 0.5, 0.8, 1.0)], ["🔴", "🟠", "🟡", "🟢"])

    def test_tier_matches_rating(self):
        self.assertEqual([common.rating_tier(r) for r in (0.1, 0.5, 0.8, 1.0)], [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()

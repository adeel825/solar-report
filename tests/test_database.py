import unittest
from unittest import mock

import database
from tests.support import TempEnvTestCase

# Keep every test date before the real bank anchor unless a test patches it,
# so the bank is plain SUM(net) from the first stored day.
WINTER = 0.248622


class MonthToDateTests(TempEnvTestCase):
    def test_sums_only_that_month_through_the_date(self):
        self.save("2026-10-31", 40, 20)
        self.save("2026-11-01", 10, 20)
        self.save("2026-11-02", 15, 20)
        self.save("2026-11-03", 30, 20)
        self.assertAlmostEqual(database.get_month_to_date("2026-11-02"), 25)
        self.assertAlmostEqual(database.get_month_to_date("2026-11-30"), 55)

    def test_backfilling_another_month_leaves_this_month_alone(self):
        # Regression: the old running counter reset to October here, so
        # November's month-to-date collapsed to a single day afterwards.
        self.save("2026-11-01", 10, 20)
        self.save("2026-11-02", 15, 20)
        self.save("2026-10-15", 40, 20)
        self.save("2026-11-03", 30, 20)
        self.assertAlmostEqual(database.get_month_to_date("2026-11-03"), 55)

    def test_lifetime_production(self):
        self.save("2026-04-01", 99, 10)   # before PTO, not counted
        self.save("2026-04-09", 50, 10)
        self.save("2026-04-10", 25, 10)
        self.assertAlmostEqual(database.get_lifetime_production("2026-04-02"), 75)


class SavingsTests(TempEnvTestCase):
    def test_positive_bank_values_the_whole_days_consumption(self):
        self.save("2026-10-20", 30, 10)            # +20 banked
        self.save("2026-10-21", 5, 20)             # -15, bank covers it
        self.assertAlmostEqual(self.row("2026-10-21")["electricity_savings"], round(20 * WINTER, 4))

    def test_draw_the_bank_cant_cover_is_excluded(self):
        self.save("2026-10-20", 30, 10)            # +20 banked
        self.save("2026-10-21", 5, 40)             # -35: 15 kWh uncovered
        self.assertAlmostEqual(self.row("2026-10-21")["electricity_savings"], round(25 * WINTER, 4))

    def test_anomalous_consumption_values_production_instead(self):
        self.save("2026-10-20", 30, 16_782.778)
        self.assertAlmostEqual(self.row("2026-10-20")["electricity_savings"], round(30 * WINTER, 4))

    def test_total_value_is_savings_plus_srec(self):
        self.save("2026-10-20", 30, 10)
        r = self.row("2026-10-20")
        self.assertAlmostEqual(r["srec_earned"], round(30 / 1000 * self.cfg["srec_rate"], 4))
        self.assertAlmostEqual(r["total_value"], round(r["electricity_savings"] + r["srec_earned"], 4))

    def test_resaving_a_day_is_idempotent(self):
        self.save("2026-10-20", 30, 10)
        first = self.row("2026-10-20")
        self.save("2026-10-20", 30, 10)
        self.assertEqual(self.row("2026-10-20"), first)

    def test_summer_tier_uses_month_to_date_consumption(self):
        r = self.cfg["pseg_rs_rates"]
        self.save("2026-07-01", 400, 290)          # 580 kWh used before the 3rd
        self.save("2026-07-02", 400, 290)
        self.save("2026-07-03", 50, 30)            # 20 kWh at the first tier, 10 above it
        expected = round(20 * r["summer_first_tier"] + 10 * r["summer_over_tier"], 4)
        self.assertAlmostEqual(self.row("2026-07-03")["electricity_savings"], expected)


class BackfillRecomputeTests(TempEnvTestCase):
    def test_saving_an_earlier_day_recomputes_later_days(self):
        # Day 2 alone: bank starts at 0, so 15 of its 20 kWh are uncovered.
        self.save("2026-10-21", 5, 20)
        self.assertAlmostEqual(self.row("2026-10-21")["electricity_savings"], round(5 * WINTER, 4))
        # Backfilling day 1 (+20 banked) means day 2 is now fully covered.
        self.save("2026-10-20", 30, 10)
        self.assertAlmostEqual(self.row("2026-10-21")["electricity_savings"], round(20 * WINTER, 4))

    def test_recompute_savings_applies_new_srec_rate(self):
        self.save("2026-10-20", 30, 10)
        self.save("2026-10-21", 40, 10)
        cfg = dict(self.cfg, srec_rate=100)
        self.assertEqual(database.recompute_savings("2026-10-21", cfg), 1)
        self.assertAlmostEqual(self.row("2026-10-21")["srec_earned"], 4.0)
        self.assertAlmostEqual(self.row("2026-10-20")["srec_earned"], round(0.030 * 85, 4))


class NetBankTests(TempEnvTestCase):
    def test_bank_is_anchor_plus_net_after_anchor_excluding_anomalies(self):
        with mock.patch.object(database, "BANK_ANCHOR_DATE", "2026-11-05"), \
             mock.patch.object(database, "BANK_ANCHOR_KWH", 1000.0):
            self.save("2026-11-05", 50, 10)        # on the anchor date: already in the anchor
            self.save("2026-11-06", 30, 10)        # +20
            self.save("2026-11-07", 5, 400)        # anomalous, excluded
            bank = database.get_net_bank("2026-04-02")
        self.assertAlmostEqual(bank["banked_kwh"], 1020.0)
        self.assertEqual(bank["anomalous_days"], 1)

    def test_period_summary_excludes_anomalous_consumption(self):
        self.save("2026-10-20", 30, 10)
        self.save("2026-10-21", 30, 900)
        s = database.get_period_summary("2026-10-20", "2026-10-21")
        self.assertEqual(s["days"], 2)
        self.assertAlmostEqual(s["produced"], 60)
        self.assertAlmostEqual(s["consumed"], 10)
        self.assertEqual(s["anomalous_days"], 1)

    def test_empty_period_summary_is_none(self):
        self.assertIsNone(database.get_period_summary("2026-01-01", "2026-01-31"))


if __name__ == "__main__":
    unittest.main()

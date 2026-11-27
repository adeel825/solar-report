"""Bar/meter/delta helpers, break-even, and full renders of every report."""
import re
import unittest
from datetime import date, timedelta

import email_builder
import monthly_report
import report_builder
import weekly_report
from report_builder import _break_even, _darken, _delta_html, _meter_scale, _overflow_split
from tests.support import TempEnvTestCase, example_config

GREEN, RED = "#1D9E75", "#D85A30"


class BarTests(unittest.TestCase):
    def test_overflow_split(self):
        self.assertEqual(_overflow_split(50), (50, 0.0))
        self.assertEqual(_overflow_split(100), (100, 0.0))
        self.assertEqual(_overflow_split(200), (50.0, 50.0))
        self.assertEqual(_overflow_split(-10), (0, 0.0))

    def test_darken(self):
        self.assertEqual(_darken("#FFFFFF"), "#999999")
        self.assertEqual(_darken("#000000"), "#000000")

    def test_inline_bar_draws_overflow_cell_only_past_100(self):
        self.assertNotIn("border-left:2px solid #ffffff", email_builder._inline_bar(80, GREEN))
        self.assertIn("border-left:2px solid #ffffff", email_builder._inline_bar(130, GREEN))

    def test_meter_uses_default_headroom(self):
        m = _meter_scale(30, 30)
        self.assertAlmostEqual(m["scale_max"], 30 * 1.35)
        self.assertEqual(m["needle_pct"], round(1 / 1.35 * 100, 1))

    def test_meter_stretches_on_huge_days(self):
        m = _meter_scale(60, 30)                     # 2× target
        self.assertLess(m["needle_pct"], 100)
        self.assertAlmostEqual(m["scale_max"], 30 * 2 * 1.05)
        self.assertLess(m["z_good"], round(0.90 / 1.35 * 100, 1))   # zones shrink


class DeltaTests(unittest.TestCase):
    def test_no_previous_is_a_dash(self):
        self.assertIn("—", _delta_html(5, None))

    def test_increase_is_green_by_default(self):
        self.assertIn(GREEN, _delta_html(12, 10))
        self.assertIn(RED, _delta_html(8, 10))

    def test_invert_makes_increase_red(self):
        # Regression: higher consumption used to show as a green improvement.
        self.assertIn(RED, _delta_html(12, 10, invert=True))
        self.assertIn(GREEN, _delta_html(8, 10, invert=True))


class BreakEvenTests(TempEnvTestCase):
    def test_projects_a_future_break_even_date(self):
        self.save("2026-06-01", 60, 40)
        be = _break_even(self.cfg, "2026-06-01")
        self.assertIsNotNone(be["break_even_date"])
        self.assertGreater(be["break_even_date"], date(2026, 6, 1))
        self.assertGreater(be["remaining"], 0)
        self.assertTrue(be["label"].startswith("~"))

    def test_paid_off(self):
        self.save("2026-06-01", 60, 40)
        be = _break_even(dict(self.cfg, net_cost=1), "2026-06-01")
        self.assertEqual(be["label"], "Paid off!")
        self.assertEqual(be["remaining"], 0)

    def test_higher_escalation_pays_off_sooner(self):
        self.save("2026-06-01", 60, 40)
        slow = _break_even(dict(self.cfg, rate_escalation=0.0), "2026-06-01")["break_even_date"]
        fast = _break_even(dict(self.cfg, rate_escalation=0.10), "2026-06-01")["break_even_date"]
        self.assertLess(fast, slow)


class RenderTests(TempEnvTestCase):
    """End-to-end renders against a small synthetic history (no network)."""

    def setUp(self):
        super().setUp()
        d = date(2026, 10, 25)
        while d <= date(2026, 11, 20):
            self.save(d.isoformat(), 20 + d.day % 7, 22)
            d += timedelta(days=1)
        self.save("2026-11-21", 30, 900)             # glitched consumption reading

    def test_daily_html_report(self):
        html = report_builder.build_report("2026-11-10").read_text(encoding="utf-8")
        mtd = sum(20 + day % 7 for day in range(1, 11))
        self.assertIn(f"{mtd:.1f} / 800 kWh", html)
        self.assertIn("Net metering bank", html)
        self.assertNotIn("Consumption data unavailable", html)

    def test_daily_email_month_to_date_is_for_the_report_date(self):
        # Regression: past-date reports showed the latest month-to-date counter.
        html = email_builder.build_email("2026-11-05")
        mtd = sum(20 + day % 7 for day in range(1, 6))
        self.assertIn(f"{mtd:.1f} / 800 kWh", html)

    def test_daily_email_average_uses_earlier_days_only(self):
        html = email_builder.build_email("2026-10-27")
        self.assertIn("(2d)", html)                  # Oct 25 and 26 only

    def test_glitched_consumption_shows_warning_in_both_daily_formats(self):
        self.assertIn("Consumption data unavailable", report_builder.build_report("2026-11-21").read_text(encoding="utf-8"))
        self.assertIn("Consumption data unavailable", email_builder.build_email("2026-11-21"))

    def test_weekly_report(self):
        path, subject = weekly_report.build_weekly_report(date(2026, 11, 9))
        html = path.read_text(encoding="utf-8")
        self.assertRegex(subject, r"^[🔴🟠🟡🟢] Weekly Solar — Nov 9–15, 2026")
        self.assertEqual(len(re.findall(r">(Mon|Tue|Wed|Thu|Fri|Sat|Sun)<", html)), 7)   # one bar per day

    def test_monthly_report(self):
        path, subject = monthly_report.build_monthly_report(2026, 11)
        html = path.read_text(encoding="utf-8")
        self.assertIn("Monthly Solar — November 2026", subject)
        self.assertIn("(excludes 1 day — missing data)", html)
        self.assertIn("Week-by-week", html)


if __name__ == "__main__":
    unittest.main()

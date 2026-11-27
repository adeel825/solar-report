"""Enphase fetch edge cases, failure alerts, and the scheduled job entry points."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

import enphase_api
import gats_reminder
import monthly_report
import send_email
import solar_report
import weekly_report
from tests.support import TempEnvTestCase, reading


def lifetime(start: str, key: str, values: list) -> dict:
    return {"start_date": start, key: values}


class ExtractLifetimeTests(unittest.TestCase):
    def test_returns_the_days_value(self):
        data = lifetime("2026-11-01", "production", [100, 200, 300])
        self.assertEqual(enphase_api._extract_lifetime_value(data, "2026-11-03", "production"), 300.0)

    def test_day_past_the_end_raises_instead_of_zero(self):
        # Regression: a not-yet-published day used to be saved as 0 kWh.
        data = lifetime("2026-11-01", "production", [100, 200])
        with self.assertRaises(enphase_api.MissingDataError):
            enphase_api._extract_lifetime_value(data, "2026-11-03", "production")

    def test_empty_response_raises(self):
        with self.assertRaises(enphase_api.MissingDataError):
            enphase_api._extract_lifetime_value({}, "2026-11-03", "production")


class FetchDayTests(TempEnvTestCase):
    def _fake_api(self, production, consumption):
        responses = [
            (lifetime("2026-11-01", "production", production), self.cfg),
            (lifetime("2026-11-01", "consumption", consumption), self.cfg),
        ]
        return mock.patch.object(enphase_api, "api_get", side_effect=responses)

    def test_builds_reading(self):
        with self._fake_api([0, 30_000], [0, 12_500]):
            r = enphase_api.fetch_day("2026-11-02")
        self.assertEqual(r, reading("2026-11-02", 30.0, 12.5))

    def test_zero_production_and_consumption_means_unpublished(self):
        with self._fake_api([10, 0], [10, 0]):
            with self.assertRaises(enphase_api.MissingDataError):
                enphase_api.fetch_day("2026-11-02")


class FailureAlertTests(unittest.TestCase):
    def test_alert_is_sent_with_job_name_and_error(self):
        with mock.patch.object(send_email, "send_raw") as send_raw:
            self.assertTrue(send_email.send_failure_alert("Daily solar report", ValueError("boom <x>"), {}))
        subject, body, _ = send_raw.call_args.args
        self.assertIn("Daily solar report FAILED", subject)
        self.assertIn("boom &lt;x&gt;", body)           # escaped

    def test_alert_never_raises(self):
        with mock.patch.object(send_email, "send_raw", side_effect=RuntimeError("resend down")), \
             redirect_stderr(io.StringIO()):
            self.assertFalse(send_email.send_failure_alert("Daily solar report", ValueError("x"), {}))


class DailyJobTests(TempEnvTestCase):
    def run_job(self, **patches):
        out = io.StringIO()
        with mock.patch.multiple(solar_report.enphase_api, **patches.pop("enphase", {})), \
             mock.patch.object(solar_report.send_email, "send", **patches.pop("send", {})) as send, \
             mock.patch.object(solar_report.send_email, "send_failure_alert", return_value=True) as alert, \
             redirect_stdout(out), redirect_stderr(out):
            code = solar_report.run("2026-11-02")
        return code, send, alert, out.getvalue()

    def test_success(self):
        code, send, alert, out = self.run_job(
            enphase={"fetch_day": mock.Mock(return_value=reading("2026-11-02", 30, 20))})
        self.assertEqual(code, 0)
        send.assert_called_once_with("2026-11-02")
        alert.assert_not_called()
        self.assertIn("Month-to-date: 30.0 kWh / 800 kWh", out)

    def test_fetch_failure_sends_alert_and_exits_nonzero(self):
        code, send, alert, _ = self.run_job(
            enphase={"fetch_day": mock.Mock(side_effect=enphase_api.MissingDataError("not yet"))})
        self.assertEqual(code, 1)
        send.assert_not_called()
        alert.assert_called_once()
        self.assertIsNone(self.row("2026-11-02"))      # nothing saved

    def test_email_failure_exits_nonzero(self):
        # Regression: an email failure used to be printed and the task still exited 0.
        code, _, _, out = self.run_job(
            enphase={"fetch_day": mock.Mock(return_value=reading("2026-11-02", 30, 20))},
            send={"side_effect": RuntimeError("resend down")})
        self.assertEqual(code, 1)
        self.assertIn("Email failed", out)
        self.assertIsNotNone(self.row("2026-11-02"))   # data still saved


class PeriodJobTests(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        for day in range(1, 31):
            self.save(f"2026-11-{day:02d}", 25, 20)

    def test_weekly_sends_via_shared_sender(self):
        with mock.patch.object(weekly_report.send_email, "send_raw", return_value="id") as send_raw, \
             redirect_stdout(io.StringIO()):
            weekly_report.send_weekly(date(2026, 11, 9))
        self.assertIn("Weekly Solar", send_raw.call_args.args[0])

    def test_monthly_sends_via_shared_sender(self):
        with mock.patch.object(monthly_report.send_email, "send_raw", return_value="id") as send_raw, \
             redirect_stdout(io.StringIO()):
            monthly_report.send_monthly(2026, 11)
        self.assertIn("Monthly Solar", send_raw.call_args.args[0])


class GatsTests(unittest.TestCase):
    def test_certificates_count_whole_mwh_above_baseline(self):
        self.assertEqual(gats_reminder._certificates_to_date(2000), 0)
        self.assertEqual(gats_reminder._certificates_to_date(3989), 0)
        self.assertEqual(gats_reminder._certificates_to_date(3990), 1)
        self.assertEqual(gats_reminder._certificates_to_date(10_617), 7)

    def test_default_period_is_previous_month_end(self):
        self.assertEqual(gats_reminder._reading_period(date(2026, 12, 12), None), date(2026, 11, 30))
        self.assertEqual(gats_reminder._reading_period(date(2027, 1, 9), None), date(2026, 12, 31))
        self.assertEqual(gats_reminder._reading_period(date(2026, 12, 12), "2026-02"), date(2026, 2, 28))

    def test_history_month_ends(self):
        ends = gats_reminder._history_month_ends(date(2026, 9, 30), 6)
        self.assertEqual(ends[0], date(2026, 3, 31))   # 6 months plus one earlier baseline
        self.assertEqual(ends[-1], date(2026, 9, 30))
        self.assertEqual(len(ends), 7)

    def test_prior_reading_for_pto_month_is_day_before_pto(self):
        readings = {"2026-04-01": 2990, "2026-04-30": 4387, "2026-08-31": 10_617}
        self.assertEqual(gats_reminder._prior_reading(date(2026, 4, 30), readings), 2990)
        with self.assertRaises(gats_reminder.GatsReadingError):
            gats_reminder._prior_reading(date(2026, 9, 30), {})
        with self.assertRaises(gats_reminder.GatsReadingError):
            gats_reminder._prior_reading(date(2026, 3, 31), readings)

    def test_decreasing_reading_sends_error_email(self):
        readings = {"2026-04-01": 2990, "2026-08-31": 10_617, "2026-09-30": 10_000}
        with mock.patch.object(gats_reminder, "load_config", return_value={"srec_rate": 85}), \
             mock.patch.object(gats_reminder, "_fetch_readings", return_value=readings), \
             mock.patch.object(gats_reminder.send_email, "send_raw") as send_raw, \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                gats_reminder.remind("2026-09")
        self.assertIn("FAILED", send_raw.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

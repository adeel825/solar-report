import unittest

import weather
from common import PRACTICAL_MAX_KWH


def daily(cloud_pct):
    return {"weather_code": [0], "temperature_2m_max": [20], "temperature_2m_min": [10],
            "precipitation_probability_max": [0], "cloud_cover_mean": [cloud_pct],
            "time": ["2026-11-28"]}


class ForecastEstimateTests(unittest.TestCase):
    def test_clear_sky_estimate_is_capped_at_practical_max(self):
        f = weather._extract_forecast_day(daily(0), 0)
        self.assertEqual(f["mid_est"], PRACTICAL_MAX_KWH)
        self.assertEqual(f["high_est"], PRACTICAL_MAX_KWH)

    def test_overcast_estimate_scales_from_practical_max(self):
        f = weather._extract_forecast_day(daily(100), 0)
        self.assertAlmostEqual(f["mid_est"], round(PRACTICAL_MAX_KWH * weather._cloud_factor(100), 1))


if __name__ == "__main__":
    unittest.main()

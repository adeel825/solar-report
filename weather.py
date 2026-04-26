import requests
from datetime import date, timedelta

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → (label, outlook)
# outlook: "good" | "fair" | "poor"
_WMO_MAP = {
    0:  ("Clear sky",       "good"),
    1:  ("Mainly clear",    "good"),
    2:  ("Partly cloudy",   "fair"),
    3:  ("Overcast",        "fair"),
    45: ("Foggy",           "fair"),
    48: ("Icy fog",         "fair"),
    51: ("Light drizzle",   "poor"),
    53: ("Drizzle",         "poor"),
    55: ("Heavy drizzle",   "poor"),
    61: ("Light rain",      "poor"),
    63: ("Rain",            "poor"),
    65: ("Heavy rain",      "poor"),
    71: ("Light snow",      "poor"),
    73: ("Snow",            "poor"),
    75: ("Heavy snow",      "poor"),
    77: ("Snow grains",     "poor"),
    80: ("Rain showers",    "poor"),
    81: ("Showers",         "poor"),
    82: ("Heavy showers",   "poor"),
    85: ("Snow showers",    "poor"),
    86: ("Heavy snow showers", "poor"),
    95: ("Thunderstorm",    "poor"),
    96: ("Thunderstorm",    "poor"),
    99: ("Thunderstorm",    "poor"),
}

OUTLOOK_COLOR = {
    "good": "#1D9E75",
    "fair": "#EF9F27",
    "poor": "#D85A30",
}

SYSTEM_CAPACITY_KW = 11.2
PEAK_SUN_HOURS = 8
CLOUD_PENALTY = 0.75  # at 100% cloud cover, production drops to 25% of theoretical max


def _cloud_factor(cloud_pct: float) -> float:
    return max(0.10, 1.0 - CLOUD_PENALTY * (cloud_pct / 100.0))


def fetch_tomorrow_forecast(latitude: float, longitude: float, report_date: str | None = None) -> dict:
    """
    Fetch the forecast for the day after report_date from Open-Meteo.
    Defaults to the day after yesterday (i.e. today) when report_date is omitted.
    Returns None on any network/parse error so the report can still build without it.
    """
    base = date.fromisoformat(report_date) if report_date else date.today() - timedelta(days=1)
    forecast_date = (base + timedelta(days=1)).isoformat()

    # Open-Meteo only serves forecasts for today and forward; clamp to today minimum
    today_str = date.today().isoformat()
    if forecast_date < today_str:
        forecast_date = today_str

    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "weathercode,temperature_2m_max,precipitation_probability_max,cloud_cover_mean",
                "timezone": "America/New_York",
                "forecast_days": 7,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [weather fetch failed: {e}]")
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])

    try:
        idx = dates.index(forecast_date)
    except ValueError:
        print(f"  [weather: {forecast_date} not found in response]")
        return None

    wmo_code   = int(daily["weathercode"][idx])
    temp_max   = daily["temperature_2m_max"][idx]         # °C
    precip_pct = daily["precipitation_probability_max"][idx]  # %
    cloud_pct  = daily["cloud_cover_mean"][idx]           # %

    temp_f = round(temp_max * 9 / 5 + 32)

    label, outlook = _WMO_MAP.get(wmo_code, ("Unknown", "fair"))
    factor = _cloud_factor(cloud_pct)

    theoretical_max = SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS  # kWh
    mid_est = round(theoretical_max * factor, 1)
    low_est = round(mid_est * 0.85, 1)
    high_est = round(min(mid_est * 1.15, theoretical_max), 1)

    return {
        "date":        forecast_date,
        "condition":   label,
        "outlook":     outlook,
        "color":       OUTLOOK_COLOR[outlook],
        "temp_f":      temp_f,
        "precip_pct":  precip_pct,
        "cloud_pct":   cloud_pct,
        "low_est":     low_est,
        "high_est":    high_est,
        "mid_est":     mid_est,
    }

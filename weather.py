import requests
from datetime import date, timedelta
from statistics import mode

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

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

_WMO_EMOJI = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️", 56: "🌦️", 57: "🌦️",
    61: "🌧️", 63: "🌧️", 65: "🌧️", 66: "🌧️", 67: "🌧️",
    71: "❄️", 73: "❄️", 75: "❄️", 77: "❄️",
    80: "🌦️", 81: "🌦️", 82: "🌦️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}

_WMO_DESC = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Foggy",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Rain showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ hail",
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


def fetch_day_weather(d: str, lat: float, lon: float) -> dict | None:
    """Fetch actual weather for date d using peak solar hours (9am–2pm) for the condition code.
    Tries the archive API first (for past dates), falls back to forecast API."""
    params = dict(
        latitude=lat, longitude=lon,
        start_date=d, end_date=d,
        hourly="weathercode,temperature_2m",
        daily="temperature_2m_max,temperature_2m_min",
        temperature_unit="fahrenheit",
        timezone="America/New_York",
    )
    for url in [
        "https://archive-api.open-meteo.com/v1/archive",
        "https://api.open-meteo.com/v1/forecast",
    ]:
        try:
            r = requests.get(url, params=params, timeout=6)
            if not r.ok:
                continue
            data   = r.json()
            hourly = data.get("hourly", {})
            daily  = data.get("daily", {})
            times  = hourly.get("time", [])
            codes  = hourly.get("weathercode") or hourly.get("weather_code", [])
            highs  = daily.get("temperature_2m_max", [])
            lows   = daily.get("temperature_2m_min", [])
            if not (times and codes and highs and lows):
                continue
            solar_codes = [
                int(c) for t, c in zip(times, codes)
                if c is not None and 9 <= int(t.split("T")[1][:2]) <= 14
            ]
            if not solar_codes:
                solar_codes = [int(c) for c in codes if c is not None]
            code = mode(solar_codes)
            return {
                "code":  code,
                "emoji": _WMO_EMOJI.get(code, "🌡️"),
                "desc":  _WMO_DESC.get(code, ""),
                "high":  round(highs[0]),
                "low":   round(lows[0]),
            }
        except Exception:
            pass
    return None


def fetch_tomorrow_forecast(latitude: float, longitude: float, report_date: str | None = None) -> dict | None:
    """Fetch forecast for the day after report_date.
    Uses forecast_days=7 and finds the target date by index — more reliable than start_date/end_date params.
    Returns None on any network/parse error."""
    base = date.fromisoformat(report_date) if report_date else date.today() - timedelta(days=1)
    forecast_date = (base + timedelta(days=1)).isoformat()

    # Open-Meteo only serves forecasts for today and forward
    today_str = date.today().isoformat()
    if forecast_date < today_str:
        forecast_date = today_str

    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude":  latitude,
                "longitude": longitude,
                "daily": "weathercode,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,cloud_cover_mean",
                "timezone":     "America/New_York",
                "forecast_days": 7,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [weather forecast failed: {e}]")
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])

    try:
        idx = dates.index(forecast_date)
    except ValueError:
        print(f"  [weather: {forecast_date} not in forecast response]")
        return None

    wmo_code   = int(daily["weathercode"][idx])
    temp_max   = daily["temperature_2m_max"][idx]
    temp_min   = daily["temperature_2m_min"][idx]
    precip_pct = daily["precipitation_probability_max"][idx]
    cloud_pct  = daily["cloud_cover_mean"][idx]

    temp_f     = round(temp_max * 9 / 5 + 32)
    temp_f_low = round(temp_min * 9 / 5 + 32)

    label, outlook = _WMO_MAP.get(wmo_code, ("Unknown", "fair"))
    factor = _cloud_factor(cloud_pct)

    theoretical_max = SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS
    mid_est  = round(theoretical_max * factor, 1)
    low_est  = round(mid_est * 0.85, 1)
    high_est = round(min(mid_est * 1.15, theoretical_max), 1)

    return {
        "date":       forecast_date,
        "code":       wmo_code,
        "emoji":      _WMO_EMOJI.get(wmo_code, "🌡️"),
        "desc":       _WMO_DESC.get(wmo_code, ""),
        "condition":  label,
        "outlook":    outlook,
        "color":      OUTLOOK_COLOR[outlook],
        "high":       temp_f,
        "low":        temp_f_low,
        "temp_f":     temp_f,
        "precip_pct": precip_pct,
        "cloud_pct":  cloud_pct,
        "low_est":    low_est,
        "high_est":   high_est,
        "mid_est":    mid_est,
    }


def build_headline_daily(produced: float, prev, weather, forecast, daily_target: float,
                         rank: int = 0, rank_total: int = 0) -> str:
    """One-sentence daily summary: rating, kWh, rank, weather context, vs yesterday, tomorrow forecast."""
    ratio = produced / daily_target if daily_target else 0
    if ratio >= 0.90:
        opener = "Excellent day"
    elif ratio >= 0.70:
        opener = "Good day"
    elif ratio >= 0.45:
        opener = "Decent output"
    else:
        opener = "Tough day"

    wx_part = ""
    if weather:
        code = weather["code"]
        if code == 0:
            wx_part = f" under clear skies ({weather['high']}°F)"
        elif code in (1, 2):
            wx_part = f" with some clouds ({weather['high']}°F)"
        elif code == 3:
            wx_part = f" under overcast skies ({weather['high']}°F)"
        elif code in (45, 48):
            wx_part = f" through morning fog ({weather['high']}°F)"
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            wx_part = f" in rainy conditions ({weather['high']}°F)"
        elif code in (71, 73, 75, 77, 85, 86):
            wx_part = f" through snowy weather ({weather['high']}°F)"
        elif code in (95, 96, 99):
            wx_part = f" with thunderstorms ({weather['high']}°F)"
        else:
            wx_part = f" ({weather['high']}°F)"

    delta_part = ""
    if prev and prev["produced"]:
        diff = produced - prev["produced"]
        pct  = round(abs(diff) / prev["produced"] * 100)
        if abs(diff) >= 0.3:
            direction = "up" if diff > 0 else "down"
            delta_part = f", {direction} {pct}% from yesterday's {prev['produced']:.1f} kWh"

    tmrw_part = ""
    if forecast:
        code      = forecast["code"]
        hi        = forecast["high"]
        cloud_pct = forecast.get("cloud_pct")
        est_str   = ""
        if cloud_pct is not None:
            _max    = SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS
            factor  = max(0.10, 1.0 - 0.75 * (cloud_pct / 100))
            mid     = _max * factor
            low     = round(mid * 0.85)
            high    = round(min(mid * 1.15, _max))
            est_str = f", est. {low}–{high} kWh"
        if code == 0:
            tmrw_part = f" — {forecast['emoji']} Clear skies tomorrow ({hi}°F){est_str}."
        elif code in (1, 2):
            tmrw_part = f" — {forecast['emoji']} Partly cloudy tomorrow ({hi}°F){est_str}."
        elif code == 3:
            tmrw_part = f" — {forecast['emoji']} Overcast tomorrow ({hi}°F){est_str}."
        elif code in (45, 48):
            tmrw_part = f" — {forecast['emoji']} Foggy tomorrow ({hi}°F){est_str}."
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            tmrw_part = f" — {forecast['emoji']} Rain tomorrow ({hi}°F){est_str}."
        elif code in (71, 73, 75, 77, 85, 86):
            tmrw_part = f" — {forecast['emoji']} Snow tomorrow ({hi}°F){est_str}."
        elif code in (95, 96, 99):
            tmrw_part = f" — {forecast['emoji']} Storms tomorrow ({hi}°F){est_str}."
        else:
            tmrw_part = f" — {forecast['emoji']} {forecast['desc']} tomorrow ({hi}°F){est_str}."

    rank_part = ""
    if rank and rank_total:
        if rank == 1:
            opener = "🏆 New record"
        elif rank == rank_total:
            rank_part = " (lowest day yet)"
        else:
            pct = round(rank / rank_total * 100)
            rank_part = f" (top {pct}%)"

    return f"{opener} — {produced:.1f} kWh{rank_part}{wx_part}{delta_part}{tmrw_part}"

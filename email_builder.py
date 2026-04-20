"""
Generates email-safe HTML for the daily solar report.
Uses table-based layout with fully inline styles — compatible with
Outlook desktop, webmail, Gmail, and mobile clients.
"""
import json
import math
from pathlib import Path
from datetime import date, timedelta

import requests
import database

# WMO weather code → emoji
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


def _fetch_weather(d: str, lat: float, lon: float) -> dict | None:
    """Fetch daily high/low temp (°F) and WMO weather code from Open-Meteo."""
    params = dict(
        latitude=lat, longitude=lon,
        start_date=d, end_date=d,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        temperature_unit="fahrenheit",
        timezone="America/New_York",
    )
    for url in [
        "https://archive-api.open-meteo.com/v1/archive",
        "https://api.open-meteo.com/v1/forecast",
    ]:
        try:
            r = requests.get(url, params=params, timeout=6)
            if r.ok:
                daily = r.json().get("daily", {})
                codes = daily.get("weathercode") or daily.get("weather_code", [])
                highs = daily.get("temperature_2m_max", [])
                lows  = daily.get("temperature_2m_min", [])
                if codes and highs and lows:
                    code = int(codes[0])
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


def _fetch_forecast(d: str, lat: float, lon: float) -> dict | None:
    """Fetch forecast for a future date from Open-Meteo forecast API."""
    params = dict(
        latitude=lat, longitude=lon,
        start_date=d, end_date=d,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        temperature_unit="fahrenheit",
        timezone="America/New_York",
    )
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=6)
        if r.ok:
            daily = r.json().get("daily", {})
            codes = daily.get("weathercode") or daily.get("weather_code", [])
            highs = daily.get("temperature_2m_max", [])
            lows  = daily.get("temperature_2m_min", [])
            if codes and highs and lows:
                code = int(codes[0])
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


def _headline_daily(produced: float, prev, weather, forecast, daily_target: float) -> str:
    """Build a punchy one-sentence headline for the daily email."""
    # Rating opener
    ratio = produced / daily_target if daily_target else 0
    if ratio >= 0.90:
        opener = "Excellent day"
    elif ratio >= 0.70:
        opener = "Good day"
    elif ratio >= 0.45:
        opener = "Decent output"
    else:
        opener = "Tough day"

    # Weather context
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

    # vs yesterday
    delta_part = ""
    if prev and prev["produced"]:
        diff = produced - prev["produced"]
        pct = round(abs(diff) / prev["produced"] * 100)
        if abs(diff) >= 0.3:
            direction = "up" if diff > 0 else "down"
            delta_part = f", {direction} {pct}% from yesterday's {prev['produced']:.1f} kWh"

    # Tomorrow's forecast
    tmrw_part = ""
    if forecast:
        code = forecast["code"]
        hi = forecast["high"]
        if code == 0:
            tmrw_part = f" — {forecast['emoji']} clear skies tomorrow ({hi}°F) should bring strong output."
        elif code in (1, 2):
            tmrw_part = f" — {forecast['emoji']} partly cloudy tomorrow ({hi}°F), moderate production likely."
        elif code == 3:
            tmrw_part = f" — {forecast['emoji']} overcast forecast tomorrow ({hi}°F) will cap output."
        elif code in (45, 48):
            tmrw_part = f" — {forecast['emoji']} foggy tomorrow ({hi}°F), expect reduced production."
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            tmrw_part = f" — {forecast['emoji']} rain in the forecast tomorrow ({hi}°F), expect a low-output day."
        elif code in (71, 73, 75, 77, 85, 86):
            tmrw_part = f" — {forecast['emoji']} snow tomorrow ({hi}°F), minimal output expected."
        elif code in (95, 96, 99):
            tmrw_part = f" — {forecast['emoji']} storms tomorrow ({hi}°F), output will be very low."
        else:
            tmrw_part = f" — {forecast['emoji']} {forecast['desc'].lower()} tomorrow ({hi}°F)."

    return f"{opener} — {produced:.1f} kWh{wx_part}{delta_part}{tmrw_part}"

CONFIG_PATH = Path(__file__).parent / "config.json"

MONTHLY_TARGETS = {
    1: 800, 2: 870, 3: 1100, 4: 1260, 5: 1430,
    6: 1420, 7: 1490, 8: 1430, 9: 1180, 10: 1050,
    11: 800, 12: 700,
}
SYSTEM_CAPACITY_KW = 11.2
PEAK_SUN_HOURS = 8
PTO_DATE = "2026-04-09"

# Colours
C_GREEN  = "#1D9E75"
C_BLUE   = "#378ADD"
C_AMBER  = "#EF9F27"
C_RED    = "#D85A30"
C_PURPLE = "#9B59B6"
C_GREY   = "#888888"
C_BG     = "#f8f8f8"
C_BORDER = "#e8e8e8"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def _fmt_date(d: str) -> str:
    dt = date.fromisoformat(d)
    return dt.strftime("%B") + " " + str(dt.day) + ", " + str(dt.year)


def _pct(val, total) -> float:
    if not total:
        return 0.0
    return min(round(val / total * 100, 1), 100.0)


def _pto_duration(target_date: str) -> str:
    pto = date.fromisoformat(PTO_DATE)
    target = date.fromisoformat(target_date)
    delta = (target - pto).days
    if delta <= 0:
        return "PTO day"
    if delta < 14:
        return f"{delta} day{'s' if delta != 1 else ''} since PTO"
    if delta < 60:
        weeks = delta // 7
        days = delta % 7
        return f"{weeks}w{f' {days}d' if days else ''} since PTO"
    if delta < 365:
        months = delta // 30
        weeks = (delta % 30) // 7
        return f"{months}mo{f' {weeks}w' if weeks else ''} since PTO"
    years = delta // 365
    months = (delta % 365) // 30
    return f"{years}yr{f' {months}mo' if months else ''} since PTO"


def _break_even(cfg: dict, as_of_date: str) -> dict:
    conn = database.get_conn()
    row = conn.execute(
        "SELECT SUM(total_value) as earned, COUNT(*) as days FROM daily_readings WHERE date <= ?",
        (as_of_date,),
    ).fetchone()
    conn.close()
    net_cost = cfg.get("net_cost", 16610)
    total_earned = row["earned"] or 0.0
    days_tracked = row["days"] or 1
    remaining = max(net_cost - total_earned, 0)
    avg_daily = total_earned / days_tracked if days_tracked > 0 else 0
    if avg_daily > 0 and remaining > 0:
        days_left = math.ceil(remaining / avg_daily)
        years = days_left // 365
        months = (days_left % 365) // 30
        label = f"~{years}yr" + (f" {months}mo" if months else "") if years >= 1 else (f"~{months}mo" if months >= 1 else f"~{days_left}d")
    elif remaining <= 0:
        label = "Paid off!"
    else:
        label = "N/A"
    pct_paid = _pct(total_earned, net_cost)
    return {"label": label, "pct_paid": pct_paid, "total_earned": total_earned, "remaining": remaining}


def _inline_bar(fill_pct: float, color: str, height: int = 8) -> str:
    """Render a progress bar using a two-cell table (email-safe)."""
    fill_pct = min(max(fill_pct, 0), 100)
    empty_pct = 100 - fill_pct
    filled_td = f'<td width="{fill_pct}%" style="height:{height}px;background:{color};border-radius:{height//2}px;line-height:0;font-size:0">&nbsp;</td>'
    empty_td  = f'<td width="{empty_pct}%" style="height:{height}px;background:{C_BORDER};line-height:0;font-size:0">&nbsp;</td>' if empty_pct > 0 else ""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-radius:{height//2}px;overflow:hidden;background:{C_BORDER}">'
        f'<tr>{filled_td}{empty_td}</tr></table>'
    )


def _section_label(text: str) -> str:
    return (
        f'<p style="margin:20px 0 8px;font-size:11px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.08em;color:#999;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">{text}</p>'
    )


def _card(label: str, value: str, sub: str, value_color: str = "#1a1a1a") -> str:
    return f"""<td style="padding:4px">
  <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px">
    <tr><td style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
      <div style="font-size:11px;color:#666;margin-bottom:4px">{label}</div>
      <div style="font-size:20px;font-weight:600;color:{value_color};margin-bottom:2px">{value}</div>
      <div style="font-size:11px;color:#999">{sub}</div>
    </td></tr>
  </table>
</td>"""


def build_email(target_date: str) -> str:
    cfg = load_config()
    row = database.get_reading(target_date)
    if row is None:
        raise ValueError(f"No reading found for {target_date}")
    cum = database.get_cumulative()

    d            = row["date"]
    produced     = row["produced"]
    consumed     = row["consumed"]
    imported     = row["imported"]
    exported     = row["exported"]
    net          = row["net"]
    srec_earned  = row["srec_earned"]
    electricity_savings    = row["electricity_savings"]
    total_value  = row["total_value"]
    monthly_kwh  = cum["monthly_kwh"]
    srec_progress = cum["srec_progress_kwh"]

    month          = date.fromisoformat(d).month
    monthly_target = MONTHLY_TARGETS[month]
    month_name     = date.fromisoformat(d).strftime("%B")
    date_display   = _fmt_date(d)
    pto_label      = _pto_duration(d)
    be             = _break_even(cfg, d)

    # Net metering bank since PTO
    BANK_TARGET  = 500  # kWh — roughly one peak summer month
    _bc = database.get_conn()
    _br = _bc.execute(
        "SELECT SUM(net) as banked_kwh, AVG(consumed) as avg_consumed "
        "FROM daily_readings WHERE date >= ?", (PTO_DATE,)
    ).fetchone()
    _bc.close()
    banked_kwh   = _br["banked_kwh"] or 0.0
    avg_consumed = _br["avg_consumed"] or 1.0
    bank_value   = round(banked_kwh * cfg["pseg_rate"], 2)
    days_covered = round(banked_kwh / avg_consumed, 1) if banked_kwh > 0 else 0
    bank_pct     = _pct(banked_kwh, BANK_TARGET)

    # Weather — silently skipped if API unavailable
    _lat = cfg.get("latitude")
    _lon = cfg.get("longitude")
    weather = _fetch_weather(d, _lat, _lon) if (_lat and _lon) else None

    # Tomorrow's forecast for headline prediction
    tomorrow_str = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    forecast = _fetch_forecast(tomorrow_str, _lat, _lon) if (_lat and _lon) else None

    # Previous day for change indicators
    prev_date = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    prev = database.get_reading(prev_date)

    def _delta(current, key, unit=" kWh", fmt=".1f", invert=False):
        """Change badge. invert=True for metrics where up is bad (consumption, imports)."""
        if prev is None:
            return ""
        previous = prev[key]
        diff = current - previous
        if abs(diff) < 0.01:
            return ""
        arrow = "▲" if diff > 0 else "▼"
        good = diff < 0 if invert else diff > 0
        color = C_GREEN if good else C_RED
        return (
            f'<span style="font-size:10px;font-weight:600;color:{color};'
            f'margin-left:4px;white-space:nowrap">'
            f'{arrow} {diff:+{fmt}}{unit}</span>'
        )

    srec_pct   = _pct(srec_progress, 1000)
    mtd_pct    = _pct(monthly_kwh, monthly_target)
    perf_pct   = _pct(produced, SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS)
    be_pct     = be["pct_paid"]
    srec_days_left = math.ceil((1000 - srec_progress) / produced) if produced > 0 else "?"
    net_sign  = "+" if net >= 0 else ""
    net_color = C_GREEN if net >= 0 else C_RED

    # Performance rating
    daily_target = monthly_target / 30
    ratio = produced / daily_target if daily_target else 0
    if ratio >= 0.90:
        rating, rating_color = "EXCELLENT", C_GREEN
    elif ratio >= 0.70:
        rating, rating_color = "GOOD", "#5BAD6F"
    elif ratio >= 0.45:
        rating, rating_color = "FAIR", C_AMBER
    else:
        rating, rating_color = "POOR", C_RED

    # Headline summary
    headline = _headline_daily(produced, prev, weather, forecast, daily_target)

    # Historical context line
    conn = database.get_conn()
    hist = conn.execute(
        "SELECT AVG(produced) as avg, MAX(produced) as best, COUNT(*) as cnt "
        "FROM daily_readings WHERE date < ?", (d,)
    ).fetchone()
    conn.close()
    hist_line = ""
    if hist and hist["cnt"] and hist["cnt"] > 0:
        hist_line = f" &nbsp;·&nbsp; Avg {hist['avg']:.1f} kWh ({hist['cnt']}d) &nbsp;·&nbsp; Best {hist['best']:.1f} kWh"

    # ------------------------------------------------------------------ #
    # Flow row cells
    def flow_cell(val, label, color):
        return f"""<td align="center" style="padding:6px">
  <table cellpadding="10" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;min-width:70px">
    <tr><td align="center" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
      <div style="font-size:18px;font-weight:600;color:{color}">{val}</div>
      <div style="font-size:11px;color:#666;margin-top:2px;white-space:nowrap">{label}</div>
    </td></tr>
  </table>
</td>"""

    def arrow_cell(sym):
        return f'<td align="center" style="font-size:18px;color:#bbb;padding:0 2px;font-family:sans-serif">{sym}</td>'

    net_color  = C_GREEN if net >= 0 else C_RED
    net_label  = "kWh net export" if net >= 0 else "kWh net import"
    net_sign   = "+" if net >= 0 else ""
    flow_row = (
        flow_cell(f"{produced:.1f}", f"kWh produced{_delta(produced, 'produced')}", C_GREEN) +
        arrow_cell("vs") +
        f'<td align="center" style="padding:6px">'
        f'<table cellpadding="10" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;min-width:70px;border:2px solid #ddd">'
        f'<tr><td align="center" style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'<div style="font-size:18px;font-weight:600;color:{C_RED}">{consumed:.1f}</div>'
        f'<div style="font-size:11px;color:#666;margin-top:2px">kWh consumed{_delta(consumed, "consumed", invert=True)}</div>'
        f'</td></tr></table></td>' +
        arrow_cell("=") +
        f'<td align="center" style="padding:6px">'
        f'<table cellpadding="10" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;min-width:70px;border:2px solid {net_color}">'
        f'<tr><td align="center" style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'<div style="font-size:18px;font-weight:600;color:{net_color}">{net_sign}{net:.1f}</div>'
        f'<div style="font-size:11px;color:#666;margin-top:2px">{net_label}{_delta(net, "net")}</div>'
        f'</td></tr></table></td>'
    )

    # Bar rows
    def bar_row(label, right, pct, color):
        return f"""<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;margin-bottom:8px">
  <tr><td style="padding:10px 14px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px">
      <tr>
        <td style="font-size:12px;color:#444">{label}</td>
        <td align="right" style="font-size:12px;color:#444;white-space:nowrap"><strong>{right}</strong></td>
      </tr>
    </table>
    {_inline_bar(pct, color)}
  </td></tr>
</table>"""

    # ------------------------------------------------------------------ #
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solar Report — {date_display}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f5;padding:20px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:#ffffff;border-radius:16px;padding:28px;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
<tr><td>

  <!-- Header -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px">
    <tr>
      <td style="font-size:20px;font-weight:600;color:#1a1a1a">{date_display}</td>
      {'<td align="right" style="vertical-align:middle">' +
       f'<span style="font-size:22px;line-height:1">{weather["emoji"]}</span>' +
       f'<span style="font-size:13px;font-weight:600;color:#1a1a1a;margin-left:6px">{weather["high"]}°</span>' +
       f'<span style="font-size:12px;color:#999;margin-left:3px">/ {weather["low"]}°F</span>' +
       f'<div style="font-size:10px;color:#aaa;text-align:right;margin-top:2px">{weather["desc"]}</div>' +
       '</td>'
       if weather else '<td></td>'}
    </tr>
  </table>
  <div style="display:inline-block;background:#E1F5EE;color:#085041;border-radius:20px;font-size:12px;font-weight:600;padding:3px 12px;margin-bottom:12px">{pto_label}</div>

  <!-- Headline summary -->
  <p style="margin:0 0 16px;font-size:14px;color:#333;font-style:italic;line-height:1.5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">{headline}</p>

  <!-- Performance meter -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C_BG};border-radius:10px;margin-bottom:16px">
    <tr><td style="padding:14px 16px">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px">
        <tr>
          <td style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:#999">Today's performance</td>
          <td align="right" style="font-size:14px;font-weight:700;color:{rating_color};letter-spacing:0.04em">{rating}</td>
        </tr>
      </table>
      <!-- Segmented colour bar -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:6px;overflow:hidden;margin-bottom:4px">
        <tr>
          <td width="33%" style="height:12px;background:#FFCDD2;line-height:0;font-size:0">&nbsp;</td>
          <td width="19%" style="height:12px;background:#FFE0B2;line-height:0;font-size:0">&nbsp;</td>
          <td width="14%" style="height:12px;background:#C8E6C9;line-height:0;font-size:0">&nbsp;</td>
          <td width="34%" style="height:12px;background:#A5D6A7;line-height:0;font-size:0">&nbsp;</td>
        </tr>
      </table>
      <!-- Needle indicator as text marker -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="{max(perf_pct - 4, 0):.0f}%" style="font-size:0">&nbsp;</td>
          <td style="font-size:12px;color:#1a1a1a;font-weight:700;white-space:nowrap">&#9650; {produced:.1f} kWh</td>
        </tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:2px">
        <tr>
          <td style="font-size:10px;color:#bbb;width:33%">Poor</td>
          <td style="font-size:10px;color:#bbb;width:19%">Fair</td>
          <td style="font-size:10px;color:#bbb;width:14%">Good</td>
          <td style="font-size:10px;color:#bbb">Excellent</td>
        </tr>
      </table>
      <p style="font-size:11px;color:#888;margin:6px 0 0">Today {produced:.1f} kWh &nbsp;·&nbsp; Monthly daily target {monthly_target//30:.0f} kWh{hist_line}</p>
    </td></tr>
  </table>

  <!-- Energy flow -->
  {_section_label("Energy flow")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px">
    <tr>{flow_row}</tr>
  </table>

  <!-- Key metrics -->
  {_section_label("Key metrics")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px">
    <tr>
      {_card("Net metering credit", f"{net_sign}{net:.1f} kWh", "net today", net_color)}
      {_card("Month-to-date", f"{monthly_kwh:.1f} kWh", f"of {monthly_target:,} {month_name} target")}
      {_card("Break-even", be['label'], f"${be['total_earned']:,.0f} of ${cfg['net_cost']:,} earned", C_BLUE)}
    </tr>
  </table>

  <!-- Financial value -->
  {_section_label("Financial value today")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px">
    <tr>
      {_card("Electricity savings", f"${electricity_savings:.2f}{_delta(electricity_savings, 'electricity_savings', '', '.2f')}", f"{min(produced, consumed):.2f} kWh \u00d7 ${cfg['pseg_rate']:.3f}", C_GREEN)}
      {_card("SREC preview (pending)", f"${srec_earned:.2f}", "not counted until approved", C_GREY)}
      {_card("Total value", f"${total_value:.2f}{_delta(total_value, 'total_value', '', '.2f')}", "electricity savings only", C_GREEN)}
    </tr>
  </table>

  <!-- System performance bars -->
  {_section_label("System performance")}
  {bar_row(
      f"Production vs theoretical max ({SYSTEM_CAPACITY_KW} kW \u00d7 {PEAK_SUN_HOURS} hrs)",
      f"{perf_pct:.0f}% &mdash; {produced:.1f} / {SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS:.0f} kWh",
      perf_pct, C_GREEN
  )}
  {bar_row(
      f"Month-to-date vs {month_name} target",
      f"{mtd_pct:.1f}% &mdash; {monthly_kwh:.1f} / {monthly_target:,} kWh",
      mtd_pct, C_BLUE
  )}
  {bar_row(
      f"Break-even progress &mdash; ${cfg['net_cost']:,} installation cost",
      f"{be_pct:.2f}% &mdash; ${be['total_earned']:,.2f} / ${cfg['net_cost']:,}",
      be_pct, C_PURPLE
  )}
  {bar_row(
      "Net metering bank since PTO",
      f"{banked_kwh:.1f} kWh &nbsp;&middot;&nbsp; ${bank_value:.2f} &nbsp;&middot;&nbsp; ~{days_covered:.0f} days coverage",
      bank_pct, C_BLUE
  )}

</td></tr>
</table>
</td></tr>
</table>

</body>
</html>"""

    return html


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    html = build_email(target)
    out = Path(__file__).parent / "reports" / f"{target}-email.html"
    out.write_text(html, encoding="utf-8")
    print(f"Email preview written to: {out}")

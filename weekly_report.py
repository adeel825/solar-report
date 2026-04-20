"""
Generates and emails the weekly solar report.
Covers Mon–Sun of the previous week.
Run every Monday at 6am — reports on the week just completed.
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mode

import requests
import resend

import database
from report_builder import (
    MONTHLY_TARGETS, PTO_DATE, REPORTS_DIR,
    _fmt_date, _pct, _delta_html, _break_even, _pto_duration, load_config
)

# WMO weather code → emoji (shared with email_builder)
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


def _fetch_week_forecast(start: date, lat: float, lon: float) -> dict | None:
    """Fetch 7-day forecast summary from Open-Meteo (avg high, dominant weather code)."""
    end = start + timedelta(days=6)
    params = dict(
        latitude=lat, longitude=lon,
        start_date=start.isoformat(), end_date=end.isoformat(),
        daily="temperature_2m_max,weathercode",
        temperature_unit="fahrenheit",
        timezone="America/New_York",
    )
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=6)
        if r.ok:
            daily = r.json().get("daily", {})
            codes = daily.get("weathercode") or daily.get("weather_code", [])
            highs = daily.get("temperature_2m_max", [])
            if codes and highs:
                codes_int = [int(c) for c in codes if c is not None]
                highs_flt = [h for h in highs if h is not None]
                dominant = mode(codes_int) if codes_int else 3
                avg_high = round(sum(highs_flt) / len(highs_flt)) if highs_flt else 65
                return {
                    "dominant_code": dominant,
                    "emoji": _WMO_EMOJI.get(dominant, "🌡️"),
                    "desc":  _WMO_DESC.get(dominant, ""),
                    "avg_high": avg_high,
                }
    except Exception:
        pass
    return None


def _headline_weekly(this_week: dict, prev_week, daily_tgt: float,
                     next_week_forecast) -> str:
    """Build a punchy one-sentence headline for the weekly report."""
    produced   = this_week["produced"]
    days_count = this_week["days"] or 1
    daily_avg  = produced / days_count

    # Rating based on daily average vs target
    ratio = daily_avg / daily_tgt if daily_tgt else 0
    if ratio >= 0.90:
        opener = "Strong week"
    elif ratio >= 0.70:
        opener = "Solid week"
    elif ratio >= 0.45:
        opener = "Average week"
    else:
        opener = "Slow week"

    # vs previous week
    delta_part = ""
    if prev_week and prev_week.get("produced"):
        diff = produced - prev_week["produced"]
        pct = round(abs(diff) / prev_week["produced"] * 100)
        if abs(diff) >= 0.5:
            direction = "up" if diff > 0 else "down"
            delta_part = f", {direction} {pct}% from last week's {prev_week['produced']:.1f} kWh"

    # Next week outlook
    outlook = ""
    if next_week_forecast:
        code = next_week_forecast["dominant_code"]
        hi   = next_week_forecast["avg_high"]
        emoji = next_week_forecast["emoji"]
        if code == 0:
            outlook = f" — {emoji} clear skies ahead next week (avg {hi}°F), strong output expected."
        elif code in (1, 2):
            outlook = f" — {emoji} mostly clear next week (avg {hi}°F), should be a good week."
        elif code == 3:
            outlook = f" — {emoji} overcast conditions forecast next week (avg {hi}°F), moderate output likely."
        elif code in (45, 48):
            outlook = f" — {emoji} foggy outlook next week (avg {hi}°F), production may vary."
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            outlook = f" — {emoji} rainy week ahead (avg {hi}°F), expect reduced output."
        elif code in (71, 73, 75, 77, 85, 86):
            outlook = f" — {emoji} snow in the forecast next week (avg {hi}°F), minimal output expected."
        elif code in (95, 96, 99):
            outlook = f" — {emoji} stormy week ahead (avg {hi}°F), output will be very low."
        else:
            outlook = f" — {emoji} {next_week_forecast['desc'].lower()} forecast next week (avg {hi}°F)."

    return f"{opener} — {produced:.1f} kWh total{delta_part}{outlook}"

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def week_bounds(for_date: date) -> tuple[date, date]:
    """Return (monday, sunday) of the week containing for_date."""
    monday = for_date - timedelta(days=for_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def prev_week_bounds(for_date: date) -> tuple[date, date]:
    monday, _ = week_bounds(for_date)
    prev_sunday = monday - timedelta(days=1)
    return week_bounds(prev_sunday)


# ---------------------------------------------------------------------------
# Sparkline bar (email-safe table)
# ---------------------------------------------------------------------------

def _spark_bars(days: list[dict], metric: str = "produced") -> str:
    """7-cell mini bar chart. Each cell is a coloured rectangle scaled to max."""
    vals = [d[metric] for d in days]
    max_val = max(vals) if vals else 1
    cells = []
    for d in days:
        v = d[metric]
        h = max(4, round(v / max_val * 40))  # 4–40px tall
        day_lbl = date.fromisoformat(d["date"]).strftime("%a")
        color = "#1D9E75" if metric == "produced" else "#378ADD"
        cells.append(
            f'<td align="center" style="padding:0 2px;vertical-align:bottom;'
            f'font-family:sans-serif">'
            f'<div style="background:{color};width:24px;height:{h}px;'
            f'border-radius:3px 3px 0 0;margin:0 auto"></div>'
            f'<div style="font-size:9px;color:#999;margin-top:2px">{day_lbl}</div>'
            f'</td>'
        )
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto">'
        f'<tr>{"".join(cells)}</tr></table>'
    )


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _stat_row(label: str, value: str, delta_html: str, sub: str = "",
              color: str = "#1a1a1a", label_color: str = "#555") -> str:
    sub_td = f'<div style="font-size:11px;color:#999;margin-top:1px">{sub}</div>' if sub else ""
    return (
        f'<tr style="border-bottom:1px solid #f0f0f0">'
        f'<td style="padding:8px 12px;font-size:13px;color:{label_color};'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">{label}</td>'
        f'<td style="padding:8px 12px;font-size:13px;font-weight:600;color:{color};text-align:right;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'{value}{delta_html}{sub_td}</td>'
        f'</tr>'
    )


def build_weekly_report(week_start: date | None = None) -> tuple[Path, str]:
    """
    Build the weekly HTML report.
    week_start: the Monday of the week to report on.
    Defaults to last Monday (i.e. the completed week).
    Returns (html_path, subject_line).
    """
    cfg = load_config()

    if week_start is None:
        today = date.today()
        monday, _ = week_bounds(today)
        week_start = monday - timedelta(weeks=1)  # last full week

    week_end   = week_start + timedelta(days=6)
    pw_start, pw_end = prev_week_bounds(week_start)

    # Clamp to PTO — no data exists before April 9
    pto = date.fromisoformat(PTO_DATE)
    week_start = max(week_start, pto)
    pw_start   = max(pw_start, pto)

    start_str  = week_start.isoformat()
    end_str    = week_end.isoformat()
    pw_start_s = pw_start.isoformat()
    pw_end_s   = pw_end.isoformat()

    this_week  = database.get_period_summary(start_str, end_str)
    prev_week  = database.get_period_summary(pw_start_s, pw_end_s)
    days       = database.get_readings_in_range(start_str, end_str)

    if not this_week:
        raise ValueError(f"No data for week {start_str} – {end_str}")

    # Metrics
    produced   = this_week["produced"]
    consumed   = this_week["consumed"]
    exported   = this_week["exported"]
    imported   = this_week["imported"]
    elec_sav   = this_week["electricity_savings"]
    srec_earned = this_week["srec_earned"]
    total_val  = this_week["total_value"]
    best_day   = this_week["best_day"]
    worst_day  = this_week["worst_day"]
    days_count = this_week["days"]

    pw = prev_week  # may be None

    month       = week_start.month
    mtarget     = MONTHLY_TARGETS[month]
    daily_tgt   = mtarget / 30

    week_label  = f"{week_start.strftime('%b %-d')}–{week_end.strftime('%-d, %Y')}" \
                  if sys.platform != "win32" else \
                  f"{week_start.strftime('%b')} {week_start.day}–{week_end.day}, {week_end.year}"

    pto_label   = _pto_duration(end_str)
    be          = _break_even(cfg, end_str)

    # Next-week forecast for headline
    _lat = cfg.get("latitude")
    _lon = cfg.get("longitude")
    next_week_start = week_end + timedelta(days=1)  # the Monday after this week's Sunday
    next_week_forecast = _fetch_week_forecast(next_week_start, _lat, _lon) if (_lat and _lon) else None

    # Headline
    headline = _headline_weekly(this_week, prev_week, daily_tgt, next_week_forecast)

    # Week-over-week deltas
    d_prod  = _delta_html(produced,    pw["produced"]    if pw else None, " kWh")
    d_cons  = _delta_html(consumed,    pw["consumed"]    if pw else None, " kWh")
    d_elec  = _delta_html(elec_sav,    pw["electricity_savings"] if pw else None, "", ".2f")
    d_srec  = _delta_html(srec_earned, pw["srec_earned"] if pw else None, "", ".2f")
    d_total = _delta_html(total_val,   pw["total_value"] if pw else None, "", ".2f")

    spark = _spark_bars(days) if days else ""

    # Day-by-day table rows
    day_rows = []
    for dy in days:
        dt = date.fromisoformat(dy["date"])
        day_rows.append(
            f'<tr style="border-bottom:1px solid #f5f5f5">'
            f'<td style="padding:6px 10px;font-size:12px;color:#666;font-family:sans-serif">{dt.strftime("%a %b")} {dt.day}</td>'
            f'<td style="padding:6px 10px;font-size:12px;text-align:right;color:#1D9E75;font-family:sans-serif">{dy["produced"]:.1f}</td>'
            f'<td style="padding:6px 10px;font-size:12px;text-align:right;color:#555;font-family:sans-serif">{dy["consumed"]:.1f}</td>'
            f'<td style="padding:6px 10px;font-size:12px;text-align:right;color:#1D9E75;font-family:sans-serif">${dy["total_value"]:.2f}</td>'
            f'</tr>'
        )
    day_table = "\n".join(day_rows)

    section = lambda t: (
        f'<p style="margin:20px 0 8px;font-size:11px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.08em;color:#999;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">{t}</p>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Solar Report — {week_label}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f5;padding:20px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
  style="max-width:640px;background:#ffffff;border-radius:16px;padding:28px;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
<tr><td>

  <!-- Header -->
  <div style="font-size:22px;font-weight:700;color:#1a1a1a;margin-bottom:4px">Weekly Solar Report</div>
  <div style="font-size:15px;color:#555;margin-bottom:6px">{week_label}</div>
  <div style="display:inline-block;background:#E1F5EE;color:#085041;border-radius:20px;
    font-size:12px;font-weight:600;padding:3px 12px;margin-bottom:12px">{pto_label}</div>

  <!-- Headline summary -->
  <p style="margin:0 0 16px;font-size:14px;color:#333;font-style:italic;line-height:1.5;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">{headline}</p>

  <!-- Sparkline -->
  {section("Daily production this week")}
  <div style="background:#f8f8f8;border-radius:10px;padding:16px;margin-bottom:4px;text-align:center">
    {spark}
    <div style="font-size:11px;color:#999;margin-top:8px">
      Best: <strong>{best_day:.1f} kWh</strong> &nbsp;·&nbsp;
      Worst: <strong>{worst_day:.1f} kWh</strong> &nbsp;·&nbsp;
      Daily target: <strong>{daily_tgt:.0f} kWh</strong>
    </div>
  </div>

  <!-- Energy summary -->
  {section("Week totals vs previous week")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {_stat_row("Produced", f"{produced:.1f} kWh", d_prod)}
    {_stat_row("Consumed", f"{consumed:.1f} kWh", d_cons)}
    {_stat_row("Exported", f"{exported:.1f} kWh", _delta_html(exported, pw["exported"] if pw else None, " kWh"))}
    {_stat_row("Imported", f"{imported:.1f} kWh", _delta_html(imported, pw["imported"] if pw else None, " kWh"))}
  </table>

  <!-- Financial -->
  {section("Financial value this week")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {_stat_row("Electricity savings", f"${elec_sav:.2f}", d_elec,
               f"{min(produced, consumed):.1f} kWh covered × ${cfg['pseg_rate']:.3f}")}
    {_stat_row("SREC preview (pending)", f"${srec_earned:.2f}", "",
               f"{produced/1000:.3f} MWh × ${cfg['srec_rate']:.2f} — not counted until approved",
               color="#aaaaaa", label_color="#aaaaaa")}
    {_stat_row("Total value", f"${total_val:.2f}", d_total)}
  </table>

  <!-- Break-even -->
  {section("Break-even progress")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {_stat_row("Projected payoff", be["label"], "",
               f"${be['total_earned']:,.0f} earned · ${be['remaining']:,.0f} left")}
    {_stat_row("% recovered", f"{be['pct_paid']:.2f}%", "")}
  </table>

  <!-- Day-by-day -->
  {section("Day-by-day breakdown")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    <tr style="border-bottom:2px solid #eee">
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:left;font-weight:600">Day</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Produced</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Consumed</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Value</th>
    </tr>
    {day_table}
  </table>

</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"week-{start_str}.html"
    out_path.write_text(html, encoding="utf-8")

    subject = (
        f"Weekly Solar — {week_label}  |  "
        f"{produced:.1f} kWh  /  ${total_val:.2f}"
    )
    return out_path, subject


def send_weekly(week_start: date | None = None):
    cfg = load_config()
    for field in ("email_from", "email_to", "resend_api_key"):
        if not cfg.get(field):
            raise ValueError(f"Missing '{field}' in config.json")

    path, subject = build_weekly_report(week_start)
    html = path.read_text(encoding="utf-8")

    resend.api_key = cfg["resend_api_key"]
    params: resend.Emails.SendParams = {
        "from": cfg["email_from"],
        "to": [cfg["email_to"]],
        "subject": subject,
        "html": html,
    }
    resp = resend.Emails.send(params)
    print(f"Weekly email sent (id: {resp['id']})")
    return path


if __name__ == "__main__":
    # Optional: pass a Monday date as YYYY-MM-DD, otherwise defaults to last full week
    ws = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    path = send_weekly(ws)
    print(f"Report: {path}")

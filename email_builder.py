"""
Generates email-safe HTML for the daily solar report.
Uses table-based layout with fully inline styles — compatible with
Outlook desktop, webmail, Gmail, and mobile clients.
"""
from datetime import date, timedelta

import common
import database
import weather as _wx
from common import PEAK_SUN_HOURS, PTO_DATE, REPORTS_DIR, SPRING_DRAW, SUMMER_DRAW, SYSTEM_CAPACITY_KW, WINTER_DRAW
from report_builder import (
    _break_even, _darken, _meter_scale, _overflow_split,
    bank_coverage, history_stats, production_rank, weather_context,
)

# Colours
C_GREEN  = "#1D9E75"
C_BLUE   = "#378ADD"
C_AMBER  = "#EF9F27"
C_RED    = "#D85A30"
C_PURPLE = "#9B59B6"
C_GREY   = "#888888"
C_BG     = "#f8f8f8"
C_BORDER = "#e8e8e8"


def _inline_bar(fill_pct: float, color: str, height: int = 8) -> str:
    """
    Render a progress bar using table cells (email-safe). Over 100%, the bar fills
    the whole track: the 0–100% part in `color`, then a darker overflow segment
    (separated by a white 100% marker) for the amount past the target.
    """
    base, over = _overflow_split(fill_pct)
    cell = f"height:{height}px;line-height:0;font-size:0"
    if over:
        cells = (
            f'<td width="{base}%" style="{cell};background:{color}">&nbsp;</td>'
            f'<td width="{over}%" style="{cell};background:{_darken(color)};border-left:2px solid #ffffff">&nbsp;</td>'
        )
    else:
        empty = round(100 - base, 1)
        cells = f'<td width="{base}%" style="{cell};background:{color};border-radius:{height//2}px">&nbsp;</td>' if base > 0 else ""
        cells += f'<td width="{empty}%" style="{cell};background:{C_BORDER}">&nbsp;</td>' if empty > 0 else ""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-radius:{height//2}px;overflow:hidden;background:{C_BORDER}">'
        f'<tr>{cells}</tr></table>'
    )


def _section_label(text: str) -> str:
    return (
        f'<p style="margin:20px 0 8px;font-size:11px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.08em;color:#999;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">{text}</p>'
    )


def stat_row(label: str, value: str, delta_html: str, sub: str = "",
             color: str = "#1a1a1a", label_color: str = "#555") -> str:
    """Label / value table row used by the weekly and monthly reports."""
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


def missing_days_note(anomalous_days: int) -> str:
    """Red '(excludes N days — missing data)' tag for period totals."""
    if not anomalous_days:
        return ""
    return (
        f' <span style="color:#D85A30;font-weight:600;font-size:10px">'
        f'(excludes {anomalous_days} day{"s" if anomalous_days != 1 else ""} — missing data)</span>'
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
    cfg = common.load_config()
    row = database.get_reading(target_date)
    if row is None:
        raise ValueError(f"No reading found for {target_date}")

    d            = row["date"]
    produced     = row["produced"]
    consumed     = row["consumed"]
    net          = row["net"]
    electricity_savings = row["electricity_savings"]
    srec_earned  = row["srec_earned"]
    total_value  = row["total_value"]
    monthly_kwh  = database.get_month_to_date(d)
    lifetime_produced = database.get_lifetime_production(PTO_DATE)

    report_dt      = date.fromisoformat(d)
    monthly_target = common.monthly_target(report_dt.year, report_dt.month)
    daily_target   = common.daily_target(report_dt.year, report_dt.month)
    month_name     = report_dt.strftime("%B")
    date_display   = common.fmt_date(d)
    pto_label      = common.pto_duration(d)
    be             = _break_even(cfg, d)

    bank = bank_coverage()
    banked_kwh, bank_excluded = bank["banked_kwh"], bank["excluded"]
    winter_days, spring_days, summer_days = bank["winter_days"], bank["spring_days"], bank["summer_days"]

    # Weather — silently skipped if API unavailable
    weather, forecast = weather_context(d, cfg)

    # Previous day for change indicators
    prev = database.get_reading((report_dt - timedelta(days=1)).isoformat())

    # Consumption sanity check — a meter/comm glitch can dump a fabricated
    # reading into a single day. Treat it as missing data rather than display it.
    consumption_ok = not database.is_consumption_anomalous(consumed)
    prev_consumption_ok = prev is not None and not database.is_consumption_anomalous(prev["consumed"])

    def _delta(current, key, unit=" kWh", fmt=".1f", invert=False):
        """Change badge. invert=True for metrics where up is bad (consumption, imports)."""
        if prev is None:
            return ""
        if key in ("consumed", "net", "imported", "exported") and not prev_consumption_ok:
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

    mtd_pct    = common.pct(monthly_kwh, monthly_target)
    perf_pct   = common.pct(produced, SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS)
    be_pct     = be["pct_paid"]

    # Performance rating
    ratio = produced / daily_target if daily_target else 0
    meter = _meter_scale(produced, daily_target)
    zw = (meter["z_poor"], round(meter["z_fair"] - meter["z_poor"], 1),
          round(meter["z_good"] - meter["z_fair"], 1), round(100 - meter["z_good"], 1))
    rating, rating_color, _ = common.rating(ratio)

    # Historical context line + rank
    hist = history_stats(d)
    hist_line = ""
    if hist["count"]:
        hist_line = f" &nbsp;·&nbsp; Avg {hist['avg']:.1f} kWh ({hist['count']}d) &nbsp;·&nbsp; Best {hist['best']:.1f} kWh"
    rank, rank_total = production_rank(produced, d)

    # Headline summary
    headline = _wx.build_headline_daily(produced, prev, weather, forecast, daily_target, rank, rank_total)

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

    elec_sub_html = database.savings_basis_label(d, produced, consumed, cfg)
    warning_banner_html = ""
    if consumption_ok:
        consumed_cell_html = (
            f'<div style="font-size:18px;font-weight:600;color:{C_RED}">{consumed:.1f}</div>'
            f'<div style="font-size:11px;color:#666;margin-top:2px">kWh consumed{_delta(consumed, "consumed", invert=True)}</div>'
        )
        net_cell_border = net_color
        net_cell_html = (
            f'<div style="font-size:18px;font-weight:600;color:{net_color}">{net_sign}{net:.1f}</div>'
            f'<div style="font-size:11px;color:#666;margin-top:2px">{net_label}{_delta(net, "net")}</div>'
        )
        net_card_value, net_card_color = f"{net_sign}{net:.1f} kWh", net_color
    else:
        warning_banner_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FFF3E0;border:1px solid #FFCC80;'
            f'border-radius:10px;margin-bottom:14px"><tr><td style="padding:12px 14px;font-size:13px;color:#7A4A00;line-height:1.5;'
            f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
            f'⚠️ <strong>Consumption data unavailable for {date_display}</strong> — Enphase reported an implausible reading '
            f'({consumed:,.1f} kWh), most likely a meter/communication glitch. Consumption, net import/export, and '
            f'net-metering bank figures are omitted below until the reading is corrected.</td></tr></table>'
        )
        consumed_cell_html = '<div style="font-size:14px;font-weight:600;color:#bbb">No data</div><div style="font-size:11px;color:#666;margin-top:2px">kWh consumed</div>'
        net_cell_border = "#ddd"
        net_cell_html = '<div style="font-size:14px;font-weight:600;color:#bbb">No data</div><div style="font-size:11px;color:#666;margin-top:2px">kWh net</div>'
        net_card_value, net_card_color = "No data", C_GREY

    flow_row = (
        flow_cell(f"{produced:.1f}", f"kWh produced{_delta(produced, 'produced')}", C_GREEN) +
        arrow_cell("vs") +
        f'<td align="center" style="padding:6px">'
        f'<table cellpadding="10" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;min-width:70px;border:2px solid #ddd">'
        f'<tr><td align="center" style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'{consumed_cell_html}'
        f'</td></tr></table></td>' +
        arrow_cell("=") +
        f'<td align="center" style="padding:6px">'
        f'<table cellpadding="10" cellspacing="0" border="0" style="background:{C_BG};border-radius:8px;min-width:70px;border:2px solid {net_cell_border}">'
        f'<tr><td align="center" style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'{net_cell_html}'
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
  {warning_banner_html}

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
          <td width="{zw[0]}%" style="height:12px;background:#FFCDD2;line-height:0;font-size:0">&nbsp;</td>
          <td width="{zw[1]}%" style="height:12px;background:#FFE0B2;line-height:0;font-size:0">&nbsp;</td>
          <td width="{zw[2]}%" style="height:12px;background:#C8E6C9;line-height:0;font-size:0">&nbsp;</td>
          <td width="{zw[3]}%" style="height:12px;background:#A5D6A7;line-height:0;font-size:0">&nbsp;</td>
        </tr>
      </table>
      <!-- Needle indicator as text marker -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="{max(meter['needle_pct'] - 4, 0):.0f}%" style="font-size:0">&nbsp;</td>
          <td style="font-size:12px;color:#1a1a1a;font-weight:700;white-space:nowrap">&#9650; {produced:.1f} kWh</td>
        </tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:2px">
        <tr>
          <td style="font-size:10px;color:#bbb;width:{zw[0]}%">Poor</td>
          <td style="font-size:10px;color:#bbb;width:{zw[1]}%">Fair</td>
          <td style="font-size:10px;color:#bbb;width:{zw[2]}%">Good</td>
          <td style="font-size:10px;color:#bbb">Excellent</td>
        </tr>
      </table>
      <p style="font-size:11px;color:#888;margin:6px 0 0">Today {produced:.1f} kWh &nbsp;·&nbsp; Monthly daily target {daily_target:.0f} kWh{hist_line}</p>
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
      {_card("Net metering credit", net_card_value, "net today", net_card_color)}
      {_card("Month-to-date", f"{monthly_kwh:.1f} kWh", f"of {monthly_target:,} {month_name} target")}
      {_card("Break-even", be['label'], f"${be['total_earned']:,.0f} of ${cfg['net_cost']:,} earned", C_BLUE)}
      {_card("Lifetime production", f"{lifetime_produced:,.0f} kWh", f"since PTO ({common.fmt_date(PTO_DATE)})", C_GREEN)}
    </tr>
  </table>

  <!-- Financial value -->
  {_section_label("Financial value today")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px">
    <tr>
      {_card("Electricity savings", f"${electricity_savings:.2f}{_delta(electricity_savings, 'electricity_savings', '', '.2f')}", elec_sub_html, C_GREEN)}
      {_card("SREC income", f"${srec_earned:.2f}", f"{produced/1000:.3f} MWh × ${cfg['srec_rate']:.2f}", C_GREEN)}
      {_card("Total value", f"${total_value:.2f}{_delta(total_value, 'total_value', '', '.2f')}", "electricity savings + SREC income", C_GREEN)}
    </tr>
  </table>

  <!-- System performance bars -->
  {_section_label("System performance")}
  {bar_row(
      f"Production vs theoretical max ({SYSTEM_CAPACITY_KW} kW × {PEAK_SUN_HOURS} hrs)",
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
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px">
    <tr>
      <td style="padding-bottom:6px">
        <span style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:#999">Net metering bank since PTO</span>
        &nbsp;&nbsp;<span style="font-size:13px;font-weight:700;color:#378ADD">{banked_kwh:.1f} kWh banked</span>
        {f'&nbsp;&nbsp;<span style="font-size:10px;font-weight:600;color:{C_RED}">(excludes {bank_excluded} day{"s" if bank_excluded != 1 else ""} — missing data)</span>' if bank_excluded else ''}
      </td>
    </tr>
    <tr>
      <td>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td width="33%" style="background:#f0f4ff;border-radius:8px;padding:10px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
              <div style="font-size:10px;color:#666;margin-bottom:3px">❄️ Winter day</div>
              <div style="font-size:18px;font-weight:700;color:#378ADD">~{winter_days} days</div>
              <div style="font-size:10px;color:#999">at {WINTER_DRAW} kWh/day</div>
            </td>
            <td width="2%"></td>
            <td width="33%" style="background:#f0f4ff;border-radius:8px;padding:10px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
              <div style="font-size:10px;color:#666;margin-bottom:3px">🌸 Spring day</div>
              <div style="font-size:18px;font-weight:700;color:#378ADD">~{spring_days} days</div>
              <div style="font-size:10px;color:#999">at {SPRING_DRAW} kWh/day</div>
            </td>
            <td width="2%"></td>
            <td width="33%" style="background:#fff8ed;border-radius:8px;padding:10px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
              <div style="font-size:10px;color:#666;margin-bottom:3px">☀️ Summer day</div>
              <div style="font-size:18px;font-weight:700;color:#EF9F27">~{summer_days} days</div>
              <div style="font-size:10px;color:#999">at {SUMMER_DRAW} kWh/day</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>


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
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{target}-email.html"
    out.write_text(html, encoding="utf-8")
    print(f"Email preview written to: {out}")

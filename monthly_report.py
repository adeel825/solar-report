"""
Generates and emails the monthly solar report.
Covers the full previous calendar month.
Run on the 1st of each month at 6am.
"""
import calendar
import os
import sys
from datetime import date, timedelta
from pathlib import Path

os.chdir(Path(__file__).parent)

import common
import database
import send_email
from common import PTO_DATE, REPORTS_DIR
from email_builder import _inline_bar, _section_label, missing_days_note, stat_row
from report_builder import _break_even, _delta_html



# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    last  = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def prev_month_bounds(year: int, month: int) -> tuple[date, date]:
    first, _ = month_bounds(year, month)
    prev_last = first - timedelta(days=1)
    return month_bounds(prev_last.year, prev_last.month)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_rows(year: int, month: int, days: list[dict]) -> str:
    """Build week-by-week summary rows for the month."""
    first, last = month_bounds(year, month)
    rows = []
    cur = first
    while cur <= last:
        week_end = min(cur + timedelta(days=6 - cur.weekday()), last)
        week_days = [d for d in days if cur.isoformat() <= d["date"] <= week_end.isoformat()]
        if week_days:
            produced = sum(d["produced"] for d in week_days)
            total    = sum(d["total_value"] for d in week_days)
            lbl = f"{cur.strftime('%b')} {cur.day}–{week_end.day}"
            rows.append(
                f'<tr style="border-bottom:1px solid #f5f5f5">'
                f'<td style="padding:6px 10px;font-size:12px;color:#666;font-family:sans-serif">{lbl}</td>'
                f'<td style="padding:6px 10px;font-size:12px;text-align:right;'
                f'color:#1D9E75;font-family:sans-serif">{produced:.1f} kWh</td>'
                f'<td style="padding:6px 10px;font-size:12px;text-align:right;'
                f'color:#1D9E75;font-family:sans-serif">${total:.2f}</td>'
                f'<td style="padding:6px 10px;font-size:12px;text-align:right;'
                f'color:#999;font-family:sans-serif">{len(week_days)}d</td>'
                f'</tr>'
            )
        cur = week_end + timedelta(days=1)
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_monthly_report(year: int | None = None, month: int | None = None) -> tuple[Path, str]:
    """
    Build the monthly HTML report.
    Defaults to the previous calendar month.
    Returns (html_path, subject_line).
    """
    cfg = common.load_config()

    if year is None or month is None:
        today = date.today()
        first_of_month = date(today.year, today.month, 1)
        prev = first_of_month - timedelta(days=1)
        year, month = prev.year, prev.month

    start, end   = month_bounds(year, month)
    ps, pe       = prev_month_bounds(year, month)
    # Clamp to PTO — no data exists before April 9
    pto   = date.fromisoformat(PTO_DATE)
    start = max(start, pto)
    ps    = max(ps, pto)

    start_s      = start.isoformat()
    end_s        = end.isoformat()

    this_month   = database.get_period_summary(start_s, end_s)
    prev_month   = database.get_period_summary(ps.isoformat(), pe.isoformat())
    days         = database.get_readings_in_range(start_s, end_s)

    if not this_month:
        raise ValueError(f"No data for {year}-{month:02d}")

    produced     = this_month["produced"]
    consumed     = this_month["consumed"]
    exported     = this_month["exported"]
    imported     = this_month["imported"]
    elec_sav     = this_month["electricity_savings"]
    srec_earned  = this_month["srec_earned"]
    total_val    = this_month["total_value"]
    lifetime_produced = database.get_lifetime_production(PTO_DATE)
    best_day     = this_month["best_day"]
    worst_day    = this_month["worst_day"]
    days_count   = this_month["days"]
    anomalous_days = this_month["anomalous_days"]
    missing_note = missing_days_note(anomalous_days)

    pm = prev_month

    # Target — prorated for the PTO month
    m_target       = common.monthly_target(year, month)
    days_in_month  = calendar.monthrange(year, month)[1]
    operating_days = days_in_month - (start.day - 1)
    mtd_pct        = common.pct(produced, m_target)
    month_name     = start.strftime("%B")

    # YTD — clamp to PTO
    ytd_start    = max(date(year, 1, 1), pto).isoformat()
    ytd          = database.get_period_summary(ytd_start, end_s)
    ytd_produced = ytd["produced"] if ytd else 0
    annual_target = cfg.get("annual_target_kwh", 13400)
    ytd_pct      = common.pct(ytd_produced, annual_target)

    pto_label    = common.pto_duration(end_s)
    be           = _break_even(cfg, end_s)

    # Deltas
    d_prod  = _delta_html(produced,   pm["produced"]    if pm else None, " kWh")
    d_cons  = _delta_html(consumed,   pm["consumed"]    if pm else None, " kWh", invert=True)
    d_elec  = _delta_html(elec_sav,   pm["electricity_savings"] if pm else None, "", ".2f")
    d_srec  = _delta_html(srec_earned,pm["srec_earned"] if pm else None, "", ".2f")
    d_total = _delta_html(total_val,  pm["total_value"] if pm else None, "", ".2f")

    week_rows_html = _week_rows(year, month, days)


    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monthly Solar Report — {month_name} {year}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f5;padding:20px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
  style="max-width:640px;background:#ffffff;border-radius:16px;padding:28px;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
<tr><td>

  <!-- Header -->
  <div style="font-size:22px;font-weight:700;color:#1a1a1a;margin-bottom:4px">Monthly Solar Report</div>
  <div style="font-size:15px;color:#555;margin-bottom:6px">{month_name} {year}</div>
  <div style="display:inline-block;background:#E1F5EE;color:#085041;border-radius:20px;
    font-size:12px;font-weight:600;padding:3px 12px;margin-bottom:20px">{pto_label}</div>

  <!-- Monthly target progress -->
  {_section_label("Production vs monthly target")}
  <div style="background:#f8f8f8;border-radius:10px;padding:14px;margin-bottom:4px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px">
      <tr>
        <td style="font-size:13px;color:#444;font-family:sans-serif">{month_name} target: {m_target:,} kWh{"" if operating_days == days_in_month else f" ({operating_days}/{days_in_month} days)"}</td>
        <td align="right" style="font-size:13px;font-weight:700;color:#1D9E75;font-family:sans-serif">
          {produced:.1f} kWh ({mtd_pct:.1f}%)
        </td>
      </tr>
    </table>
    {_inline_bar(mtd_pct, "#1D9E75", 12)}
    <div style="font-size:11px;color:#999;margin-top:6px">
      Best day: <strong>{best_day:.1f} kWh</strong> &nbsp;·&nbsp;
      Worst day: <strong>{worst_day:.1f} kWh</strong> &nbsp;·&nbsp;
      {days_count} days recorded
    </div>
  </div>

  <!-- YTD progress -->
  {_section_label("Year-to-date vs annual target")}
  <div style="background:#f8f8f8;border-radius:10px;padding:14px;margin-bottom:4px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px">
      <tr>
        <td style="font-size:13px;color:#444;font-family:sans-serif">Annual target: {annual_target:,} kWh</td>
        <td align="right" style="font-size:13px;font-weight:700;color:#378ADD;font-family:sans-serif">
          {ytd_produced:.1f} kWh ({ytd_pct:.1f}%)
        </td>
      </tr>
    </table>
    {_inline_bar(ytd_pct, "#378ADD", 12)}
  </div>

  <!-- Energy summary -->
  {_section_label("Month totals vs previous month")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {stat_row("Produced", f"{produced:.1f} kWh", d_prod)}
    {stat_row("Consumed", f"{consumed:.1f} kWh", d_cons, missing_note)}
    {stat_row("Exported", f"{exported:.1f} kWh", _delta_html(exported, pm["exported"] if pm else None, " kWh"))}
    {stat_row("Imported", f"{imported:.1f} kWh", _delta_html(imported, pm["imported"] if pm else None, " kWh", invert=True))}
  </table>

  <!-- Financial -->
  {_section_label("Financial value this month")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {stat_row("Electricity savings", f"${elec_sav:.2f}", d_elec,
               f"{consumed:.1f} kWh used × ${(elec_sav / consumed) if consumed else 0:.3f} avg RS price")}
    {stat_row("SREC income", f"${srec_earned:.2f}", d_srec,
               f"{produced/1000:.3f} MWh × ${cfg['srec_rate']:.2f}")}
    {stat_row("Total value", f"${total_val:.2f}", d_total)}
  </table>

  <!-- Break-even -->
  {_section_label("Break-even progress")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {stat_row("Projected payoff", be["label"], "",
               f"${be['total_earned']:,.0f} earned · ${be['remaining']:,.0f} left")}
    {stat_row("% recovered", f"{be['pct_paid']:.2f}%", "")}
    {stat_row("Break-even target", be["break_even_date"].strftime("%b %Y") if be["break_even_date"] else "TBD", "")}
    {stat_row("Lifetime production", f"{lifetime_produced:,.0f} kWh", "", f"since PTO ({common.fmt_date(PTO_DATE)})")}
  </table>

  <!-- Week-by-week -->
  {_section_label("Week-by-week breakdown")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    <tr style="border-bottom:2px solid #eee">
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:left;font-weight:600">Week</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Produced</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Value</th>
      <th style="padding:7px 10px;font-size:11px;color:#999;text-align:right;font-weight:600">Days</th>
    </tr>
    {week_rows_html}
  </table>

</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"month-{year}-{month:02d}.html"
    out_path.write_text(html, encoding="utf-8")

    ratio   = produced / m_target if m_target else 0
    _, _, dot = common.rating(ratio)
    subject = (
        f"{dot} Monthly Solar — {month_name} {year}  |  "
        f"{produced:.1f} kWh  /  ${total_val:.2f}"
    )
    return out_path, subject


def send_monthly(year: int | None = None, month: int | None = None):
    path, subject = build_monthly_report(year, month)
    message_id = send_email.send_raw(subject, path.read_text(encoding="utf-8"))
    print(f"Monthly email sent (id: {message_id})")
    return path


if __name__ == "__main__":
    # Optional: pass YYYY MM as args, otherwise defaults to previous calendar month
    if len(sys.argv) == 3:
        y, m = int(sys.argv[1]), int(sys.argv[2])
    else:
        y, m = None, None
    try:
        print(f"Report: {send_monthly(y, m)}")
    except Exception as e:
        print(f"Monthly report FAILED: {e}", file=sys.stderr)
        send_email.send_failure_alert("Monthly solar report", e)
        sys.exit(1)

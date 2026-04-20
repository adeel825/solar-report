"""
Generates and emails the monthly solar report.
Covers the full previous calendar month.
Run on the 1st of each month at 6am.
"""
import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import resend

import database
from report_builder import (
    MONTHLY_TARGETS, PTO_DATE, REPORTS_DIR,
    _fmt_date, _pct, _delta_html, _break_even, _pto_duration, load_config
)

CONFIG_PATH = Path(__file__).parent / "config.json"


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


def _inline_bar(pct: float, color: str, height: int = 8) -> str:
    pct = min(max(pct, 0), 100)
    empty = 100 - pct
    filled = (
        f'<td width="{pct}%" style="height:{height}px;background:{color};'
        f'border-radius:{height//2}px;line-height:0;font-size:0">&nbsp;</td>'
    )
    empty_td = (
        f'<td width="{empty}%" style="height:{height}px;background:#e8e8e8;'
        f'line-height:0;font-size:0">&nbsp;</td>'
    ) if empty > 0 else ""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-radius:{height//2}px;overflow:hidden;background:#e8e8e8">'
        f'<tr>{filled}{empty_td}</tr></table>'
    )


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
    cfg = load_config()

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
    best_day     = this_month["best_day"]
    worst_day    = this_month["worst_day"]
    days_count   = this_month["days"]

    pm = prev_month

    # Targets
    m_target     = MONTHLY_TARGETS[month]
    mtd_pct      = _pct(produced, m_target)
    month_name   = start.strftime("%B")

    # YTD — clamp to PTO
    ytd_start    = max(date(year, 1, 1), pto).isoformat()
    ytd          = database.get_period_summary(ytd_start, end_s)
    ytd_produced = ytd["produced"] if ytd else 0
    annual_target = cfg.get("annual_target_kwh", 13400)
    ytd_pct      = _pct(ytd_produced, annual_target)

    pto_label    = _pto_duration(end_s)
    be           = _break_even(cfg, end_s)

    # Deltas
    d_prod  = _delta_html(produced,   pm["produced"]    if pm else None, " kWh")
    d_cons  = _delta_html(consumed,   pm["consumed"]    if pm else None, " kWh")
    d_elec  = _delta_html(elec_sav,   pm["electricity_savings"] if pm else None, "", ".2f")
    d_srec  = _delta_html(srec_earned,pm["srec_earned"] if pm else None, "", ".2f")
    d_total = _delta_html(total_val,  pm["total_value"] if pm else None, "", ".2f")

    week_rows_html = _week_rows(year, month, days)

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
  {section("Production vs monthly target")}
  <div style="background:#f8f8f8;border-radius:10px;padding:14px;margin-bottom:4px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px">
      <tr>
        <td style="font-size:13px;color:#444;font-family:sans-serif">{month_name} target: {m_target:,} kWh</td>
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
  {section("Year-to-date vs annual target")}
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
  {section("Month totals vs previous month")}
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background:#f8f8f8;border-radius:10px;margin-bottom:4px">
    {_stat_row("Produced", f"{produced:.1f} kWh", d_prod)}
    {_stat_row("Consumed", f"{consumed:.1f} kWh", d_cons)}
    {_stat_row("Exported", f"{exported:.1f} kWh", _delta_html(exported, pm["exported"] if pm else None, " kWh"))}
    {_stat_row("Imported", f"{imported:.1f} kWh", _delta_html(imported, pm["imported"] if pm else None, " kWh"))}
  </table>

  <!-- Financial -->
  {section("Financial value this month")}
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
    {_stat_row("Break-even target", be["break_even_date"].strftime("%b %Y") if be["break_even_date"] else "TBD", "")}
  </table>

  <!-- Week-by-week -->
  {section("Week-by-week breakdown")}
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

    subject = (
        f"Monthly Solar — {month_name} {year}  |  "
        f"{produced:.1f} kWh  /  ${total_val:.2f}"
    )
    return out_path, subject


def send_monthly(year: int | None = None, month: int | None = None):
    cfg = load_config()
    for field in ("email_from", "email_to", "resend_api_key"):
        if not cfg.get(field):
            raise ValueError(f"Missing '{field}' in config.json")

    path, subject = build_monthly_report(year, month)
    html = path.read_text(encoding="utf-8")

    resend.api_key = cfg["resend_api_key"]
    params: resend.Emails.SendParams = {
        "from": cfg["email_from"],
        "to": [cfg["email_to"]],
        "subject": subject,
        "html": html,
    }
    resp = resend.Emails.send(params)
    print(f"Monthly email sent (id: {resp['id']})")
    return path


if __name__ == "__main__":
    # Optional: pass YYYY MM as args, otherwise defaults to previous calendar month
    if len(sys.argv) == 3:
        y, m = int(sys.argv[1]), int(sys.argv[2])
    else:
        y, m = None, None
    path = send_monthly(y, m)
    print(f"Report: {path}")

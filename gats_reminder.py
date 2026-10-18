"""
Monthly reminder for submitting your PJM-EIS GATS "Meter Reading (kWh)"
generation entry for NJ SREC-II. Run around the 2nd Saturday of the month,
separate from the daily/weekly/monthly production reports.

GATS computes deltas and certificates itself from whatever cumulative
reading you submit — it issues one SREC-II certificate per whole MWh of
difference between readings, carrying fractional remainders forward.
This script's job is just to hand you the correct cumulative number to
paste in, plus the estimated certificate/dollar impact for your records.

The reading is Enphase's raw cumulative production, unadjusted, as of the
LAST DAY OF THE PREVIOUS MONTH (month-end, like the hand-entered Apr-Aug
readings). GATS entries are per generation month with a meter date inside
that month's window, so a live snapshot taken mid-month would pull the next
month's production into the wrong period. Pass YYYY-MM to target another month.

Everything in the email (delta, certificates, running totals) is derived from
Enphase's month-end history, so re-running for a month always gives the same
email and nothing is stored between runs.
"""
import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import enphase_api
import send_email
from report_builder import PTO_DATE

HISTORY_MONTHS = 6

CONFIG_PATH = Path(__file__).parent / "config.json"

# GATS issues 1 certificate per whole MWh of production ABOVE the generator's
# initial meter reading, carrying fractional remainders forward automatically.
# The initial reading registered in GATS is 2,990 kWh dated 04/02/2026 (Enphase
# production before PTO, which is not eligible), so certificates-to-date is
# floor((reading_kwh - GATS_BASELINE_KWH) / 1000), not floor(reading_kwh / 1000).
KWH_PER_CERTIFICATE = 1000
GATS_BASELINE_KWH = 2990

# Sanity bounds for fetched readings (see README "GATS Monthly Reminder").
MAX_PLAUSIBLE_MONTHLY_JUMP_KWH = 3000


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


class GatsReadingError(Exception):
    """Raised when the fetched reading fails a sanity check."""


def _reading_period(today: date, period: str | None) -> date:
    """Last day of the generation month being reported (default: previous month)."""
    if period:
        year, month = map(int, period.split("-"))
    else:
        prev = today.replace(day=1) - timedelta(days=1)
        year, month = prev.year, prev.month
    return date(year, month, calendar.monthrange(year, month)[1])


def _history_month_ends(reading_date: date, n: int = HISTORY_MONTHS) -> list[date]:
    """Month-end dates for the n months ending with reading_date's month, oldest first,
    plus one earlier month-end so the oldest row's delta can be computed."""
    ends = []
    y, m = reading_date.year, reading_date.month
    for _ in range(n + 1):
        ends.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return ends[::-1]


def _fetch_readings(reading_date: date) -> dict[str, int]:
    """One Enphase call covering the entry month, the history table, and the PTO baseline."""
    pre_pto = date.fromisoformat(PTO_DATE) - timedelta(days=1)
    dates = [pre_pto] + _history_month_ends(reading_date)
    return enphase_api.fetch_cumulative_kwh_on([d.isoformat() for d in dates])


def _compute_gats_reading(readings: dict[str, int], reading_date: date) -> int:
    gats_kwh = readings.get(reading_date.isoformat())
    if gats_kwh is None:
        raise GatsReadingError(f"Enphase energy_lifetime has no data for {reading_date}.")
    if gats_kwh < 0:
        raise GatsReadingError(
            f"Computed GATS reading is negative ({gats_kwh} kWh) as of {reading_date}."
        )
    return gats_kwh


def _certificates_to_date(reading_kwh: int) -> int:
    """Whole SREC-II certificates GATS has issued once this cumulative reading is entered."""
    return max(reading_kwh - GATS_BASELINE_KWH, 0) // KWH_PER_CERTIFICATE


def _prior_reading(reading_date: date, readings: dict[str, int]) -> int:
    """Reading GATS already holds before this entry: the prior month-end, or for the
    PTO month the day before PTO."""
    pre_pto = date.fromisoformat(PTO_DATE) - timedelta(days=1)
    if reading_date <= pre_pto:
        raise GatsReadingError(f"{reading_date} is before PTO ({PTO_DATE}); nothing to report.")
    baseline = max(reading_date.replace(day=1) - timedelta(days=1), pre_pto)
    prev = readings.get(baseline.isoformat())
    if prev is None:
        raise GatsReadingError(f"Enphase energy_lifetime has no data for {baseline}.")
    return prev


def _history_rows_html(reading_date: date, readings: dict[str, int]) -> str:
    """Last HISTORY_MONTHS month-end readings, newest first, clamped to the PTO month.
    The PTO month's baseline is the day before PTO (matches GATS), not the prior month-end."""
    pto = date.fromisoformat(PTO_DATE)
    ends = _history_month_ends(reading_date)
    rows = []
    for prev_end, end in zip(ends, ends[1:]):
        cur = readings.get(end.isoformat())
        if end < pto.replace(day=1) or cur is None:
            continue
        prev = readings.get(max(prev_end, pto - timedelta(days=1)).isoformat())
        gen = f"{cur - prev:,}" if prev is not None else "—"
        current = end == reading_date
        bg = "background:#E1F5EE;" if current else ""
        weight = "700" if current else "600"
        rows.append(
            f'<tr style="{bg}border-bottom:1px solid #f0f0f0">'
            f'<td style="padding:5px 8px;color:#666">{end.strftime("%b %Y")}</td>'
            f'<td align="right" style="padding:5px 8px;color:#999">{end.strftime("%m/%d/%Y")}</td>'
            f'<td align="right" style="padding:5px 8px;font-weight:{weight};color:#1a1a1a">{cur:,}</td>'
            f'<td align="right" style="padding:5px 8px;color:#1D9E75">{gen}</td>'
            f'</tr>'
        )
    return "\n    ".join(reversed(rows))


def _send_error_email(cfg: dict, message: str) -> None:
    subject = "\U0001F534 GATS reminder FAILED — needs manual attention"
    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px">
  <h2 style="color:#D85A30">GATS reminder could not run</h2>
  <p style="font-size:14px;color:#333">The monthly GATS reading job failed validation and did <strong>not</strong> send a reading:</p>
  <pre style="background:#f8f8f8;border-radius:8px;padding:12px;font-size:13px;white-space:pre-wrap">{message}</pre>
  <p style="font-size:13px;color:#666">Check the Enphase API directly before submitting anything to GATS this month.</p>
</div>
"""
    send_email.send_raw(subject, html, cfg)


def remind(period: str | None = None) -> None:
    cfg = load_config()
    reading_date = _reading_period(date.today(), period)

    try:
        readings = _fetch_readings(reading_date)
        gats_kwh = _compute_gats_reading(readings, reading_date)
        history_rows_html = _history_rows_html(reading_date, readings)
        prev_kwh = _prior_reading(reading_date, readings)

        if gats_kwh < prev_kwh:
            raise GatsReadingError(
                f"New reading ({gats_kwh} kWh) is lower than the prior reading "
                f"({prev_kwh} kWh). Enphase data may have regressed — do not submit this to GATS."
            )

        delta_kwh = gats_kwh - prev_kwh
        if delta_kwh > MAX_PLAUSIBLE_MONTHLY_JUMP_KWH:
            raise GatsReadingError(
                f"Delta since the prior reading ({delta_kwh} kWh) exceeds the "
                f"{MAX_PLAUSIBLE_MONTHLY_JUMP_KWH} kWh sanity bound. Prior reading was "
                f"{prev_kwh} kWh, new reading is {gats_kwh} kWh."
            )

        new_total_certs = _certificates_to_date(gats_kwh)
        certs_this_period = new_total_certs - _certificates_to_date(prev_kwh)
        srec_rate = cfg["srec_rate"]
        dollars_this_period = round(certs_this_period * srec_rate, 2)
        new_cumulative_dollars = round(new_total_certs * srec_rate, 2)

        gats_url = cfg.get("gats_entry_url", "").strip()
        gats_link_html = (
            f'<a href="{gats_url}" style="color:#378ADD">{gats_url}</a>'
            if gats_url else
            '<span style="color:#aaa">(add gats_entry_url to config.json)</span>'
        )

        subject = f"☀️ GATS reading due — {gats_kwh:,} kWh cumulative"
        html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px">
  <h2 style="color:#1a1a1a;margin-bottom:4px">GATS Meter Reading — {reading_date.strftime('%B %Y')}</h2>
  <p style="font-size:13px;color:#666;margin-top:0">Enter this in the <strong>{reading_date.strftime('%b %Y')}</strong> column of PJM-EIS GATS's Unprocessed Generation.</p>

  <div style="background:#E1F5EE;border-radius:10px;padding:16px;margin:16px 0;text-align:center">
    <div style="font-size:11px;color:#085041;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Cumulative reading to enter</div>
    <div style="font-size:36px;font-weight:700;color:#085041;font-family:'SF Mono',Consolas,monospace">{gats_kwh}</div>
    <div style="font-size:12px;color:#085041;margin-top:4px">Meter Reading Date: {reading_date.strftime('%m/%d/%Y')}</div>
  </div>

  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;color:#333;margin-bottom:16px">
    <tr><td style="padding:4px 0;color:#666">Delta since last reading</td><td align="right" style="font-weight:600">{delta_kwh:,} kWh</td></tr>
    <tr><td style="padding:4px 0;color:#666">Certificates this period</td><td align="right" style="font-weight:600">{certs_this_period}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Value this period (${srec_rate:.2f}/cert)</td><td align="right" style="font-weight:600;color:#1D9E75">${dollars_this_period:,.2f}</td></tr>
    <tr><td colspan="2" style="border-top:1px solid #eee;padding-top:8px"></td></tr>
    <tr><td style="padding:4px 0;color:#666">Running total certificates (since Apr 2026)</td><td align="right" style="font-weight:600">{new_total_certs}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Running total value</td><td align="right" style="font-weight:600;color:#1D9E75">${new_cumulative_dollars:,.2f}</td></tr>
  </table>

  <p style="margin:20px 0 6px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#999">Recent month-end readings</p>
  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;background:#f8f8f8;border-radius:8px;margin-bottom:16px">
    <tr style="border-bottom:2px solid #eee">
      <th align="left" style="padding:6px 8px;font-size:11px;color:#999;font-weight:600">Month</th>
      <th align="right" style="padding:6px 8px;font-size:11px;color:#999;font-weight:600">Reading date</th>
      <th align="right" style="padding:6px 8px;font-size:11px;color:#999;font-weight:600">Reading (kWh)</th>
      <th align="right" style="padding:6px 8px;font-size:11px;color:#999;font-weight:600">Generated</th>
    </tr>
    {history_rows_html}
  </table>

  <p style="font-size:14px;margin-bottom:16px"><strong>GATS entry page:</strong> {gats_link_html}</p>

  <p style="font-size:12px;color:#999;border-top:1px solid #eee;padding-top:10px">
    Reminder: this is a <strong>cumulative</strong> reading, not a monthly total — GATS computes the delta itself.
  </p>
</div>
"""
        send_email.send_raw(subject, html, cfg)

        print(f"GATS reminder sent: {gats_kwh} kWh, +{certs_this_period} certs, +${dollars_this_period:.2f}")

    except Exception as e:
        _send_error_email(cfg, str(e))
        print(f"GATS reminder FAILED (error email sent): {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    remind(sys.argv[1] if len(sys.argv) > 1 else None)

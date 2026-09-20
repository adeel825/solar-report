"""
Monthly reminder for submitting your PJM-EIS GATS "Meter Reading (kWh)"
generation entry for NJ SREC-II. Run around the 2nd Saturday of the month,
separate from the daily/weekly/monthly production reports.

GATS computes deltas and certificates itself from whatever cumulative
reading you submit — it issues one SREC-II certificate per whole MWh of
difference between readings, carrying fractional remainders forward.
This script's job is just to hand you the correct cumulative number to
paste in, plus the estimated certificate/dollar impact for your records.

The reading is Enphase's raw lifetime production counter, unadjusted —
GATS wants the literal cumulative meter reading, not a value zeroed at
some registration date. Confirmed against readings already submitted by
hand (see README).
"""
import json
import math
import sys
from datetime import date
from pathlib import Path

import enphase_api
import send_email

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "gats_state.json"

# GATS issues 1 certificate per whole MWh of cumulative reading, carrying
# fractional remainder forward automatically — so certificates-to-date is
# always floor(reading_kwh / 1000); no separate remainder bookkeeping needed.
KWH_PER_CERTIFICATE = 1000

# Sanity bounds for fetched readings (see README "GATS Monthly Reminder").
MAX_PLAUSIBLE_MONTHLY_JUMP_KWH = 3000


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


class GatsReadingError(Exception):
    """Raised when the fetched reading fails a sanity check."""


def _compute_gats_reading() -> int:
    raw_kwh = enphase_api.fetch_lifetime_production_kwh()
    gats_kwh = math.floor(raw_kwh)
    if gats_kwh < 0:
        raise GatsReadingError(
            f"Computed GATS reading is negative ({gats_kwh} kWh) — "
            f"raw Enphase lifetime was {raw_kwh} kWh."
        )
    return gats_kwh


def _send_error_email(cfg: dict, message: str) -> None:
    subject = "\U0001F534 GATS reminder FAILED — needs manual attention"
    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px">
  <h2 style="color:#D85A30">GATS reminder could not run</h2>
  <p style="font-size:14px;color:#333">The monthly GATS reading job failed validation and did <strong>not</strong> send a reading or update its state:</p>
  <pre style="background:#f8f8f8;border-radius:8px;padding:12px;font-size:13px;white-space:pre-wrap">{message}</pre>
  <p style="font-size:13px;color:#666">Check <code>solar.db</code>/<code>gats_state.json</code> and the Enphase API directly before submitting anything to GATS this month.</p>
</div>
"""
    send_email.send_raw(subject, html, cfg)


def remind() -> None:
    cfg = load_config()
    state = load_state()
    today = date.today()

    try:
        gats_kwh = _compute_gats_reading()

        has_prior = "last_reading_kwh" in state
        prev_kwh = state["last_reading_kwh"] if has_prior else 0
        prev_certs = state.get("cumulative_certificates", 0)
        prev_dollars = state.get("cumulative_dollars", 0.0)

        if gats_kwh < prev_kwh:
            raise GatsReadingError(
                f"New reading ({gats_kwh} kWh) is lower than last submitted reading "
                f"({prev_kwh} kWh on {state.get('last_reading_date')}). Enphase data may have "
                f"regressed — do not submit this to GATS."
            )

        delta_kwh = gats_kwh - prev_kwh

        # Skip the jump check on the very first run — the initial delta covers the
        # whole backlog since the interconnection date, which is expected to be large.
        if has_prior and delta_kwh > MAX_PLAUSIBLE_MONTHLY_JUMP_KWH:
            raise GatsReadingError(
                f"Delta since last reading ({delta_kwh} kWh) exceeds the "
                f"{MAX_PLAUSIBLE_MONTHLY_JUMP_KWH} kWh sanity bound. Last reading was "
                f"{prev_kwh} kWh on {state.get('last_reading_date')}, new reading is {gats_kwh} kWh."
            )

        new_total_certs = gats_kwh // KWH_PER_CERTIFICATE
        certs_this_period = new_total_certs - prev_certs
        srec_rate = cfg["srec_rate"]
        dollars_this_period = round(certs_this_period * srec_rate, 2)
        new_cumulative_dollars = round(prev_dollars + dollars_this_period, 2)

        gats_url = cfg.get("gats_entry_url", "").strip()
        gats_link_html = (
            f'<a href="{gats_url}" style="color:#378ADD">{gats_url}</a>'
            if gats_url else
            '<span style="color:#aaa">(add gats_entry_url to config.json)</span>'
        )

        subject = f"☀️ GATS reading due — {gats_kwh:,} kWh cumulative"
        html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px">
  <h2 style="color:#1a1a1a;margin-bottom:4px">GATS Meter Reading — {today.strftime('%B %Y')}</h2>
  <p style="font-size:13px;color:#666;margin-top:0">Enter this in PJM-EIS GATS at your convenience.</p>

  <div style="background:#E1F5EE;border-radius:10px;padding:16px;margin:16px 0;text-align:center">
    <div style="font-size:11px;color:#085041;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Cumulative reading to enter</div>
    <div style="font-size:36px;font-weight:700;color:#085041;font-family:'SF Mono',Consolas,monospace">{gats_kwh}</div>
    <div style="font-size:12px;color:#085041;margin-top:4px">reading date: {today.isoformat()}</div>
  </div>

  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;color:#333;margin-bottom:16px">
    <tr><td style="padding:4px 0;color:#666">Delta since last reading</td><td align="right" style="font-weight:600">{delta_kwh:,} kWh</td></tr>
    <tr><td style="padding:4px 0;color:#666">Certificates this period</td><td align="right" style="font-weight:600">{certs_this_period}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Value this period (${srec_rate:.2f}/cert)</td><td align="right" style="font-weight:600;color:#1D9E75">${dollars_this_period:,.2f}</td></tr>
    <tr><td colspan="2" style="border-top:1px solid #eee;padding-top:8px"></td></tr>
    <tr><td style="padding:4px 0;color:#666">Running total certificates (since Apr 2026)</td><td align="right" style="font-weight:600">{new_total_certs}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Running total value</td><td align="right" style="font-weight:600;color:#1D9E75">${new_cumulative_dollars:,.2f}</td></tr>
  </table>

  <p style="font-size:14px;margin-bottom:16px"><strong>GATS entry page:</strong> {gats_link_html}</p>

  <p style="font-size:12px;color:#999;border-top:1px solid #eee;padding-top:10px">
    Reminder: this is a <strong>cumulative</strong> reading, not a monthly total — GATS computes the delta itself.
  </p>
</div>
"""
        send_email.send_raw(subject, html, cfg)

        state.update({
            "last_reading_date": today.isoformat(),
            "last_reading_kwh": gats_kwh,
            "cumulative_certificates": new_total_certs,
            "cumulative_dollars": new_cumulative_dollars,
            "last_reminder_sent_month": today.strftime("%Y-%m"),
        })
        save_state(state)
        print(f"GATS reminder sent: {gats_kwh} kWh, +{certs_this_period} certs, +${dollars_this_period:.2f}")

    except Exception as e:
        _send_error_email(cfg, str(e))
        print(f"GATS reminder FAILED (error email sent): {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    remind()

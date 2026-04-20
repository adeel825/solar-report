"""
Sends the daily solar report via Resend (resend.com).
Reads email_from, email_to, resend_api_key from config.json.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import resend

import database
import email_builder

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def send(target_date: str | None = None):
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    cfg = load_config()

    for field in ("email_from", "email_to", "resend_api_key"):
        if not cfg.get(field):
            raise ValueError(f"Missing '{field}' in config.json")

    row = database.get_reading(target_date)
    if row is None:
        raise ValueError(f"No DB entry for {target_date} — run solar_report.py first")

    produced  = row["produced"]
    net       = row["net"]
    electricity_savings = row["electricity_savings"]
    srec      = row["srec_earned"]
    total     = row["total_value"]

    month        = date.fromisoformat(target_date).month
    daily_target = email_builder.MONTHLY_TARGETS[month] / 30
    ratio        = produced / daily_target if daily_target else 0
    dot          = "🟢" if ratio >= 0.90 else "🟡" if ratio >= 0.70 else "🟠" if ratio >= 0.45 else "🔴"

    subject = f"{dot} Solar Report \u2014 {email_builder._fmt_date(target_date)}  |  {produced:.1f} kWh  /  ${total:.2f}"
    html_body = email_builder.build_email(target_date)

    resend.api_key = cfg["resend_api_key"]

    params: resend.Emails.SendParams = {
        "from": cfg["email_from"],
        "to": [cfg["email_to"]],
        "subject": subject,
        "html": html_body,
    }

    response = resend.Emails.send(params)
    print(f"Email sent to {cfg['email_to']} for {target_date} (id: {response['id']})")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    send(target)

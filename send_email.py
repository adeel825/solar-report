"""
Email delivery via Resend (resend.com). Reads email_from, email_to and
resend_api_key from config.json. send_raw() is shared by every report;
send_failure_alert() is how scheduled jobs report a failed run.
"""
import html
import sys
import traceback
from datetime import date, timedelta

import resend

import common
import database
import email_builder


def send_raw(subject: str, html_body: str, cfg: dict | None = None) -> str:
    """Sends a pre-built HTML email via Resend. Returns the Resend message id."""
    if cfg is None:
        cfg = common.load_config()

    for field in ("email_from", "email_to", "resend_api_key"):
        if not cfg.get(field):
            raise ValueError(f"Missing '{field}' in config.json")

    resend.api_key = cfg["resend_api_key"]

    params: resend.Emails.SendParams = {
        "from": cfg["email_from"],
        "to": [cfg["email_to"]],
        "subject": subject,
        "html": html_body,
    }

    response = resend.Emails.send(params)
    return response["id"]


def send_failure_alert(job: str, error: BaseException, cfg: dict | None = None) -> bool:
    """
    Emails a "job failed" alert with the error and traceback. Returns True if the
    alert was sent. Never raises — if email itself is what failed, the caller
    still exits non-zero and the error is in the job's log.
    """
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    body = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;max-width:640px">'
        f'<p style="font-size:15px;color:#D85A30;font-weight:600">{html.escape(job)} did not complete.</p>'
        f'<p style="font-size:13px;color:#333">{html.escape(str(error))}</p>'
        f'<pre style="font-size:11px;background:#f5f5f5;padding:12px;border-radius:8px;white-space:pre-wrap">{html.escape(tb)}</pre>'
        '<p style="font-size:12px;color:#888">Re-run it by hand once the cause is fixed (see README "Running Reports Manually").</p>'
        '</div>'
    )
    try:
        send_raw(f"\U0001F534 {job} FAILED — needs attention", body, cfg)
        return True
    except Exception as alert_error:
        print(f"      Failure alert could not be sent: {alert_error}", file=sys.stderr)
        return False


def send(target_date: str | None = None):
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    cfg = common.load_config()

    row = database.get_reading(target_date)
    if row is None:
        raise ValueError(f"No DB entry for {target_date} — run solar_report.py first")

    produced = row["produced"]
    total = row["total_value"]
    d = date.fromisoformat(target_date)
    _, _, dot = common.rating(produced / common.daily_target(d.year, d.month))

    subject = f"{dot} Solar Report — {common.fmt_date(target_date)}  |  {produced:.1f} kWh  /  ${total:.2f}"
    html_body = email_builder.build_email(target_date)

    message_id = send_raw(subject, html_body, cfg)
    print(f"Email sent to {cfg['email_to']} for {target_date} (id: {message_id})")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    send(target)

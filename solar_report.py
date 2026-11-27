import sys
import os
from pathlib import Path
from datetime import date, timedelta

os.chdir(Path(__file__).parent)

import common
import database
import enphase_api
import report_builder
import send_email


def _chg(current: float, previous: float | None) -> str:
    """Plain-text change indicator: ▲ +4.2 or ▼ -1.1 or blank."""
    if previous is None:
        return ""
    diff = current - previous
    if abs(diff) < 0.01:
        return ""
    arrow = "▲" if diff > 0 else "▼"
    return f"  {arrow} {diff:+.2f}"


def print_summary(row: dict):
    """Console summary of a saved day (ends up in logs/daily.log)."""
    d = row["date"]
    dt = date.fromisoformat(d)
    monthly_kwh = database.get_month_to_date(d)
    monthly_target = common.monthly_target(dt.year, dt.month)
    net = row["net"]

    prev = database.get_reading((dt - timedelta(days=1)).isoformat())
    pp = lambda key: prev[key] if prev else None

    print(f"☀️  Solar Summary — {d}")
    print("─────────────────────────")
    print(f"⚡ Produced:   {row['produced']:.2f} kWh{_chg(row['produced'], pp('produced'))}")
    print(f"🏠 Consumed:   {row['consumed']:.2f} kWh{_chg(row['consumed'], pp('consumed'))}")
    print(f"📤 Exported:   {row['exported']:.2f} kWh{_chg(row['exported'], pp('exported'))}")
    print(f"📥 Imported:   {row['imported']:.2f} kWh{_chg(row['imported'], pp('imported'))}")
    print(f"📊 Net:        {'+' if net >= 0 else ''}{net:.2f} kWh{_chg(net, pp('net'))}")
    print("─────────────────────────")
    print(f"💰 Value today: ${row['total_value']:.2f}{_chg(row['total_value'], pp('total_value'))}")
    print(f"   └ Electricity savings: ${row['electricity_savings']:.2f}{_chg(row['electricity_savings'], pp('electricity_savings'))}")
    print("─────────────────────────")
    print(f"📅 Month-to-date: {monthly_kwh:.1f} kWh / {monthly_target} kWh")


def run(target_date: str | None = None) -> int:
    """
    Fetch → save → report → email for one day. Returns the process exit code:
    0 on success, 1 if any step failed. A failure before the email step sends a
    failure alert instead, so a broken run is never silent.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    try:
        print(f"[1/4] Fetching Enphase data for {target_date}...")
        reading = enphase_api.fetch_day(target_date)

        print("[2/4] Saving to database...")
        database.init_db()
        database.save_reading(reading)

        print("[3/4] Building HTML report...")
        report_path = report_builder.build_report(target_date)
        print(f"      -> {report_path}")
    except Exception as e:
        print(f"      FAILED: {e}", file=sys.stderr)
        sent = send_email.send_failure_alert(f"Daily solar report for {target_date}", e)
        print(f"      Failure alert {'sent' if sent else 'NOT sent'}.", file=sys.stderr)
        return 1

    exit_code = 0
    print("[4/4] Sending email...")
    try:
        send_email.send(target_date)
    except Exception as e:
        print(f"      Email failed: {e}", file=sys.stderr)
        exit_code = 1

    print()
    print_summary(database.get_reading(target_date))
    return exit_code


if __name__ == "__main__":
    # UTF-8 so emoji render on Windows terminals and in logs/daily.log
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    target = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(target))

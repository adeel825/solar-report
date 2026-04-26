import sys
import io
from datetime import date, timedelta

# Force UTF-8 output so emoji render correctly on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import database
import enphase_api
import report_builder
import send_email
import weather

MONTHLY_TARGETS = {
    1: 800, 2: 870, 3: 1100, 4: 1260, 5: 1430,
    6: 1420, 7: 1490, 8: 1430, 9: 1180, 10: 1050,
    11: 800, 12: 700,
}


def _chg(current: float, previous: float | None) -> str:
    """Plain-text change indicator: ▲ +4.2 or ▼ -1.1 or blank."""
    if previous is None:
        return ""
    diff = current - previous
    if abs(diff) < 0.01:
        return ""
    arrow = "▲" if diff > 0 else "▼"
    return f"  {arrow} {diff:+.2f}"


def print_telegram_summary(row: dict, cum: dict):
    d = row["date"]
    produced = row["produced"]
    consumed = row["consumed"]
    imported = row["imported"]
    exported = row["exported"]
    net = row["net"]
    electricity_savings = row["electricity_savings"]
    total_value = row["total_value"]

    monthly_kwh = cum["monthly_kwh"]
    month = date.fromisoformat(d).month
    monthly_target = MONTHLY_TARGETS[month]

    net_sign = "+" if net >= 0 else ""

    # Previous day for comparison
    prev_date = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    prev = database.get_reading(prev_date)

    pp = lambda key: prev[key] if prev else None

    print(f"☀️  Solar Summary — {d}")
    print("─────────────────────────")
    print(f"⚡ Produced:   {produced:.2f} kWh{_chg(produced, pp('produced'))}")
    print(f"🏠 Consumed:   {consumed:.2f} kWh{_chg(consumed, pp('consumed'))}")
    print(f"📤 Exported:   {exported:.2f} kWh{_chg(exported, pp('exported'))}")
    print(f"📥 Imported:   {imported:.2f} kWh{_chg(imported, pp('imported'))}")
    print(f"📊 Net:        {net_sign}{net:.2f} kWh{_chg(net, pp('net'))}")
    print("─────────────────────────")
    print(f"💰 Value today: ${total_value:.2f}{_chg(total_value, pp('total_value'))}")
    print(f"   └ Electricity savings: ${electricity_savings:.2f}{_chg(electricity_savings, pp('electricity_savings'))}")
    print("─────────────────────────")
    print(f"📅 Month-to-date: {monthly_kwh:.1f} kWh / {monthly_target} kWh")


def run(target_date: str | None = None):
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    print(f"[1/3] Fetching Enphase data for {target_date}...")
    reading = enphase_api.fetch_day(target_date)

    print("[2/3] Saving to database...")
    database.init_db()
    database.save_reading(reading)

    print("[3/4] Building HTML report...")
    cfg = database.load_config()
    forecast = weather.fetch_tomorrow_forecast(cfg["latitude"], cfg["longitude"], report_date=target_date)
    report_path = report_builder.build_report(target_date, tomorrow_forecast=forecast)
    print(f"      -> {report_path}")

    print("[4/4] Sending email...")
    try:
        send_email.send(target_date)
    except Exception as e:
        print(f"      Email failed: {e}")

    print()
    row = database.get_reading(target_date)
    cum = database.get_cumulative()
    print_telegram_summary(row, cum)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)

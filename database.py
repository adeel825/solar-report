import json
import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path(__file__).parent / "solar.db"
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_readings (
            date          TEXT PRIMARY KEY,
            produced      REAL,
            consumed      REAL,
            imported      REAL,
            exported      REAL,
            net           REAL,
            self_consumed REAL,
            srec_earned   REAL,
            electricity_savings     REAL,
            total_value   REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cumulative (
            key   TEXT PRIMARY KEY,
            value REAL
        )
    """)
    # Seed cumulative rows if missing
    seeds = [
        ("lifetime_kwh", 0),
        ("monthly_kwh", 0),
        ("srec_progress_kwh", 0),
        ("monthly_reset_month", 0),
    ]
    for key, val in seeds:
        c.execute("INSERT OR IGNORE INTO cumulative (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


def save_reading(reading: dict, target_date: str | None = None):
    """
    Saves a daily reading and updates cumulative counters.
    reading must contain: date, produced, consumed, imported, exported, net
    """
    cfg = load_config()
    pseg_rate = cfg["pseg_rate"]
    srec_rate = cfg["srec_rate"]

    d = reading["date"]
    produced = reading["produced"]
    exported = reading["exported"]
    consumed = reading["consumed"]
    imported = reading["imported"]
    net = reading["net"]

    self_consumed = round(produced - exported, 3)
    srec_earned = round((produced / 1000) * srec_rate, 4)
    # Electricity savings = consumption covered by solar at retail rate.
    # If produced >= consumed: whole bill is wiped out → save consumed × rate
    # If produced <  consumed: solar covers produced kWh → save produced × rate
    electricity_savings = round(min(produced, consumed) * pseg_rate, 4)
    total_value = round(electricity_savings, 4)  # SRECs excluded until approval

    conn = get_conn()
    c = conn.cursor()

    # Grab the previously stored produced value for this date (0 if first time)
    existing = c.execute("SELECT produced FROM daily_readings WHERE date=?", (d,)).fetchone()
    prev_produced = existing["produced"] if existing else 0.0

    # Insert or replace the daily reading
    c.execute("""
        INSERT OR REPLACE INTO daily_readings
            (date, produced, consumed, imported, exported, net,
             self_consumed, srec_earned, electricity_savings, total_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (d, produced, consumed, imported, exported, net,
          self_consumed, srec_earned, electricity_savings, total_value))

    # --- Cumulative updates ---
    def get_cum(key):
        row = c.execute("SELECT value FROM cumulative WHERE key=?", (key,)).fetchone()
        return row["value"] if row else 0.0

    def set_cum(key, value):
        c.execute("UPDATE cumulative SET value=? WHERE key=?", (value, key))

    # Net change in produced for this date (0 on re-runs with same data)
    delta = produced - prev_produced

    current_month = date.fromisoformat(d).month

    # Monthly reset check
    reset_month = int(get_cum("monthly_reset_month"))
    if current_month != reset_month:
        set_cum("monthly_kwh", 0.0)
        set_cum("monthly_reset_month", float(current_month))

    # Apply only the delta so re-runs are idempotent
    set_cum("lifetime_kwh", round(get_cum("lifetime_kwh") + delta, 3))
    set_cum("monthly_kwh", round(get_cum("monthly_kwh") + delta, 3))

    # SREC progress: apply delta, reset remainder when crossing 1000
    srec_progress = get_cum("srec_progress_kwh") + delta
    if srec_progress >= 1000:
        srec_progress = srec_progress % 1000
    set_cum("srec_progress_kwh", round(srec_progress, 3))

    conn.commit()
    conn.close()
    return {
        "self_consumed": self_consumed,
        "srec_earned": srec_earned,
        "electricity_savings": electricity_savings,
        "total_value": total_value,
    }


def get_reading(d: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_readings WHERE date=?", (d,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_readings_in_range(start: str, end: str) -> list[dict]:
    """All daily rows between start and end inclusive, ordered by date."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM daily_readings WHERE date >= ? AND date <= ? ORDER BY date",
        (start, end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_period_summary(start: str, end: str) -> dict | None:
    """Summed/averaged metrics for a date range. Returns None if no rows."""
    conn = get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)              as days,
            SUM(produced)         as produced,
            SUM(consumed)         as consumed,
            SUM(imported)         as imported,
            SUM(exported)         as exported,
            SUM(net)              as net,
            SUM(electricity_savings) as electricity_savings,
            SUM(srec_earned)      as srec_earned,
            SUM(total_value)      as total_value,
            AVG(produced)         as avg_produced,
            MAX(produced)         as best_day,
            MIN(produced)         as worst_day
        FROM daily_readings
        WHERE date >= ? AND date <= ?
    """, (start, end)).fetchone()
    conn.close()
    if not row or not row["days"]:
        return None
    return dict(row)


def get_cumulative() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM cumulative").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)

    # Test with yesterday's data from enphase_api
    from enphase_api import fetch_day
    reading = fetch_day()
    print("\nFetched:", reading)

    extras = save_reading(reading)
    print("\nSaved reading. Derived values:")
    for k, v in extras.items():
        print(f"  {k:<20} {v}")

    print("\nCumulative:")
    for k, v in get_cumulative().items():
        print(f"  {k:<25} {v}")

    print("\nStored daily reading:")
    row = get_reading(reading["date"])
    for k, v in row.items():
        print(f"  {k:<20} {v}")

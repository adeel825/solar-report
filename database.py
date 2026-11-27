import json
import sqlite3
from pathlib import Path
from datetime import date

import rates

DB_PATH = Path(__file__).parent / "solar.db"
CONFIG_PATH = Path(__file__).parent / "config.json"

# Consumption readings above this are treated as missing/bad data rather than
# real usage — Enphase meter/comm glitches occasionally dump a garbage pulse
# count into a single day (e.g. 16,782 kWh vs a normal ~30-70 kWh/day).
CONSUMPTION_SANITY_CAP_KWH = 300

# Net metering bank calibration. Enphase's day-level SUM(produced - consumed)
# drifts from PSE&G's revenue-grade bidirectional meter over time (normal CT
# calibration difference, plus PSE&G's net-metering banking window starting
# slightly before Enphase's PTO_DATE cutoff) — by ~150 kWh over 4 months as of
# Aug 2026, and another ~50 kWh high over Aug 7 – Nov 5, 2026 (individual billing
# periods within ~30–50 kWh; part of that is meter-read time vs Enphase's
# midnight day boundary). Re-anchor these two values whenever a new bill
# arrives: open the bill's "Net Metering Program" table, take the latest row's
# meter-reading date and "Cumulative kWh" figure, and set BANK_ANCHOR_DATE/BANK_ANCHOR_KWH
# to that date and its sign-flipped value (PSE&G's convention is IN - OUT;
# this project's convention is produced - consumed = -(IN - OUT)).
BANK_ANCHOR_DATE = "2026-11-05"   # PSE&G meter-reading date last calibrated against
BANK_ANCHOR_KWH  = 1904.0         # PSE&G cumulative kWh as of BANK_ANCHOR_DATE, sign-flipped


def is_consumption_anomalous(consumed: float | None) -> bool:
    return consumed is not None and consumed > CONSUMPTION_SANITY_CAP_KWH


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
        ("monthly_reset_month", 0),
    ]
    for key, val in seeds:
        c.execute("INSERT OR IGNORE INTO cumulative (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


def _mtd_consumed_before(c, d: str) -> float:
    """Calendar-month consumption before date d (anomalous days excluded) — drives the RS summer tier."""
    month_start = d[:8] + "01"
    row = c.execute(
        "SELECT SUM(consumed) AS kwh FROM daily_readings WHERE date >= ? AND date < ? AND consumed <= ?",
        (month_start, d, CONSUMPTION_SANITY_CAP_KWH),
    ).fetchone()
    return (row["kwh"] or 0.0) if row else 0.0


def _bank_before(c, d: str) -> float:
    """Net metering bank (kWh credit) at the start of date d, on the same basis as get_net_bank."""
    if d > BANK_ANCHOR_DATE:
        row = c.execute(
            "SELECT SUM(net) AS kwh FROM daily_readings WHERE date > ? AND date < ? AND consumed <= ?",
            (BANK_ANCHOR_DATE, d, CONSUMPTION_SANITY_CAP_KWH),
        ).fetchone()
        return BANK_ANCHOR_KWH + ((row["kwh"] or 0.0) if row else 0.0)
    row = c.execute(
        "SELECT SUM(net) AS kwh FROM daily_readings WHERE date < ? AND consumed <= ?",
        (d, CONSUMPTION_SANITY_CAP_KWH),
    ).fetchone()
    return (row["kwh"] or 0.0) if row else 0.0


def compute_electricity_savings(c, d: str, produced: float, consumed: float, net: float, cfg: dict) -> float:
    """
    What the day's electricity would have cost from PSE&G without solar, at RS
    season/tier prices (see rates.py). Under net metering every kWh drawn from the
    grid is paid for with banked export credit, so while the bank stays positive the
    whole day's consumption is avoided cost — not just the part covered same-day.
    Any draw the bank can't cover is billed and excluded. A glitched consumption
    reading falls back to valuing the day's production.
    """
    if is_consumption_anomalous(consumed):
        basis = produced
    else:
        bank_after = _bank_before(c, d) + net
        uncovered = min(max(-bank_after, 0.0), max(-net, 0.0))
        basis = consumed - uncovered
    return round(rates.day_cost(basis, d, _mtd_consumed_before(c, d), cfg), 4)


def savings_basis_label(d: str, produced: float, consumed: float) -> str:
    """Report sub-line for a day's electricity savings: kWh valued and the RS price applied."""
    cfg = load_config()
    conn = get_conn()
    mtd = _mtd_consumed_before(conn, d)
    conn.close()
    if is_consumption_anomalous(consumed):
        return f"{produced:.2f} kWh (solar output) · {rates.rate_label(d, mtd, produced, cfg)}"
    return f"{consumed:.2f} kWh used · {rates.rate_label(d, mtd, consumed, cfg)}"


def marginal_rate_on(d: str) -> float:
    """RS price of the next kWh on date d (used to value a day's net export credit)."""
    cfg = load_config()
    conn = get_conn()
    mtd = _mtd_consumed_before(conn, d)
    conn.close()
    return rates.marginal_rate(d, mtd, cfg)


def save_reading(reading: dict, target_date: str | None = None):
    """
    Saves a daily reading and updates cumulative counters.
    reading must contain: date, produced, consumed, imported, exported, net
    """
    cfg = load_config()
    srec_rate = cfg["srec_rate"]

    d = reading["date"]
    produced = reading["produced"]
    exported = reading["exported"]
    consumed = reading["consumed"]
    imported = reading["imported"]
    net = reading["net"]

    self_consumed = round(produced - exported, 3)
    srec_earned = round((produced / 1000) * srec_rate, 4)

    conn = get_conn()
    c = conn.cursor()

    electricity_savings = compute_electricity_savings(c, d, produced, consumed, net, cfg)
    total_value = round(electricity_savings + srec_earned, 4)

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
    """
    Summed/averaged metrics for a date range. Returns None if no rows.
    Days with an anomalous consumption reading (see is_consumption_anomalous)
    are excluded from consumed/imported/exported/net sums — a fabricated
    meter reading would otherwise blow out the whole period's totals.
    `anomalous_days` reports how many days were excluded.
    """
    conn = get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)              as days,
            SUM(produced)         as produced,
            SUM(CASE WHEN consumed <= ? THEN consumed END)      as consumed,
            SUM(CASE WHEN consumed <= ? THEN imported END)      as imported,
            SUM(CASE WHEN consumed <= ? THEN exported END)      as exported,
            SUM(CASE WHEN consumed <= ? THEN net END)           as net,
            SUM(electricity_savings) as electricity_savings,
            SUM(srec_earned)      as srec_earned,
            SUM(total_value)      as total_value,
            AVG(produced)         as avg_produced,
            MAX(produced)         as best_day,
            MIN(produced)         as worst_day,
            SUM(CASE WHEN consumed > ? THEN 1 ELSE 0 END) as anomalous_days
        FROM daily_readings
        WHERE date >= ? AND date <= ?
    """, (CONSUMPTION_SANITY_CAP_KWH, CONSUMPTION_SANITY_CAP_KWH, CONSUMPTION_SANITY_CAP_KWH,
          CONSUMPTION_SANITY_CAP_KWH, CONSUMPTION_SANITY_CAP_KWH, start, end)).fetchone()
    conn.close()
    if not row or not row["days"]:
        return None
    result = dict(row)
    result["consumed"] = result["consumed"] or 0.0
    result["imported"] = result["imported"] or 0.0
    result["exported"] = result["exported"] or 0.0
    result["net"]      = result["net"] or 0.0
    return result


def get_net_bank(since_date: str) -> dict:
    """
    Net metering bank: BANK_ANCHOR_KWH (PSE&G's own cumulative figure as of
    BANK_ANCHOR_DATE) plus Enphase's day-level net for every day after the
    anchor. Anchoring to the latest bill keeps the figure accurate — summing
    Enphase's SUM(net) from since_date alone drifts from PSE&G's meter over
    time (see BANK_ANCHOR_KWH comment). Assumes since_date <= BANK_ANCHOR_DATE
    (true for the current caller, PTO_DATE). Days with an anomalous
    consumption reading are excluded so a bad meter glitch doesn't corrupt it.
    Returns banked_kwh and how many days were excluded.
    """
    conn = get_conn()
    anchor_date = max(since_date, BANK_ANCHOR_DATE)
    row = conn.execute("""
        SELECT
            SUM(CASE WHEN consumed <= ? THEN net END) as delta_kwh,
            SUM(CASE WHEN consumed > ? THEN 1 ELSE 0 END) as anomalous_days
        FROM daily_readings
        WHERE date > ?
    """, (CONSUMPTION_SANITY_CAP_KWH, CONSUMPTION_SANITY_CAP_KWH, anchor_date)).fetchone()
    conn.close()
    return {
        "banked_kwh": BANK_ANCHOR_KWH + ((row["delta_kwh"] or 0.0) if row else 0.0),
        "anomalous_days": (row["anomalous_days"] or 0) if row else 0,
    }


def get_lifetime_production(since_date: str) -> float:
    """Total produced kWh from since_date (inclusive) to the latest reading."""
    conn = get_conn()
    row = conn.execute(
        "SELECT SUM(produced) as total FROM daily_readings WHERE date >= ?",
        (since_date,),
    ).fetchone()
    conn.close()
    return (row["total"] or 0.0) if row else 0.0


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

"""
Shared constants and helpers used across the daily, weekly, monthly and GATS
reports: config loading, system facts, monthly targets, date formatting and
the production rating scale. Keep this module free of project imports so any
other module can depend on it.
"""
import calendar
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
REPORTS_DIR = ROOT / "reports"

PTO_DATE = "2026-04-02"
SYSTEM_CAPACITY_KW = 11.48  # kW DC nameplate (28 x 410 W); AC rating is 8.12 kW
PEAK_SUN_HOURS = 8

# Expected production by calendar month (installer estimate), kWh.
MONTHLY_TARGETS = {
    1: 800, 2: 870, 3: 1100, 4: 1260, 5: 1430,
    6: 1420, 7: 1490, 8: 1430, 9: 1180, 10: 1050,
    11: 800, 12: 700,
}

# Typical daily grid draw by season (PSE&G bill history), for "days of bank" estimates.
WINTER_DRAW = 19.0   # kWh/day — Jan–Feb average
SPRING_DRAW = 22.7   # kWh/day — Mar–May average
SUMMER_DRAW = 52.0   # kWh/day — Jun–Aug peak

# Production vs daily target: Poor < 0.45 ≤ Fair < 0.70 ≤ Good < 0.90 ≤ Excellent.
RATING_THRESHOLDS = (0.45, 0.70, 0.90)
_RATINGS = (                       # (label, colour, subject dot), worst → best
    ("POOR", "#D85A30", "🔴"),
    ("FAIR", "#EF9F27", "🟠"),
    ("GOOD", "#5BAD6F", "🟡"),
    ("EXCELLENT", "#1D9E75", "🟢"),
)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def fmt_date(d: str) -> str:
    """'2026-11-26' → 'November 26, 2026'."""
    dt = date.fromisoformat(d)
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def pct(val, total) -> float:
    """Percent of total, uncapped — over-target values report their true figure (e.g. 118.0)."""
    if not total:
        return 0.0
    return round(val / total * 100, 1)


def pto_duration(target_date: str) -> str:
    """Human-readable time since PTO: days → weeks → months → years."""
    delta = (date.fromisoformat(target_date) - date.fromisoformat(PTO_DATE)).days
    if delta < 0:
        return "pre-PTO"
    if delta == 0:
        return "PTO day"
    if delta < 14:
        return f"{delta} day{'s' if delta != 1 else ''} since PTO"
    if delta < 60:
        weeks, days = divmod(delta, 7)
        return f"{weeks}w{f' {days}d' if days else ''} since PTO"
    if delta < 365:
        months = delta // 30
        weeks = (delta % 30) // 7
        return f"{months}mo{f' {weeks}w' if weeks else ''} since PTO"
    years = delta // 365
    months = (delta % 365) // 30
    return f"{years}yr{f' {months}mo' if months else ''} since PTO"


def monthly_target(year: int, month: int) -> int:
    """Production target for a calendar month, prorated for the PTO month."""
    target = MONTHLY_TARGETS[month]
    pto = date.fromisoformat(PTO_DATE)
    if (year, month) == (pto.year, pto.month):
        days_in_month = calendar.monthrange(year, month)[1]
        operating_days = days_in_month - (pto.day - 1)
        return round(target * operating_days / days_in_month)
    return target


def daily_target(year: int, month: int) -> float:
    """Expected kWh per day: the month's target over a nominal 30-day month."""
    return monthly_target(year, month) / 30


def rating(ratio: float) -> tuple[str, str, str]:
    """(label, colour, subject dot) for production / daily target."""
    tier = sum(ratio >= t for t in RATING_THRESHOLDS)
    return _RATINGS[tier]


def rating_tier(ratio: float) -> int:
    """0 = Poor … 3 = Excellent, for callers with their own wording per tier."""
    return sum(ratio >= t for t in RATING_THRESHOLDS)

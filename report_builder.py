import json
import math
from pathlib import Path
from datetime import date, timedelta

import database

CONFIG_PATH = Path(__file__).parent / "config.json"
REPORTS_DIR = Path(__file__).parent / "reports"

MONTHLY_TARGETS = {
    1: 800, 2: 870, 3: 1100, 4: 1260, 5: 1430,
    6: 1420, 7: 1490, 8: 1430, 9: 1180, 10: 1050,
    11: 800, 12: 700,
}
SYSTEM_CAPACITY_KW = 11.2
PEAK_SUN_HOURS = 8
PTO_DATE = "2026-04-09"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def _fmt_date(d: str) -> str:
    dt = date.fromisoformat(d)
    return dt.strftime("%B") + " " + str(dt.day) + ", " + str(dt.year)


def _pct(val, total) -> float:
    if not total:
        return 0.0
    return min(round(val / total * 100, 1), 100.0)


def _pto_duration(target_date: str) -> str:
    """Human-readable time since PTO: days → weeks → months → years."""
    pto = date.fromisoformat(PTO_DATE)
    target = date.fromisoformat(target_date)
    delta = (target - pto).days

    if delta < 0:
        return "pre-PTO"
    if delta == 0:
        return "PTO day"
    if delta < 14:
        return f"{delta} day{'s' if delta != 1 else ''} since PTO"
    if delta < 60:
        weeks = delta // 7
        days = delta % 7
        tail = f" {days}d" if days else ""
        return f"{weeks}w{tail} since PTO"
    if delta < 365:
        months = delta // 30
        weeks = (delta % 30) // 7
        tail = f" {weeks}w" if weeks else ""
        return f"{months}mo{tail} since PTO"
    years = delta // 365
    months = (delta % 365) // 30
    tail = f" {months}mo" if months else ""
    return f"{years}yr{tail} since PTO"


def _perf_meter(target_date: str, produced: float, monthly_target: int) -> dict:
    """
    Compute performance meter data by comparing today's production to:
    - all other days in the DB (historical average / best)
    - the expected daily output for this month
    """
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT produced FROM daily_readings WHERE date != ? ORDER BY produced",
        (target_date,),
    ).fetchall()
    conn.close()

    hist = [r["produced"] for r in rows]
    hist_avg = sum(hist) / len(hist) if hist else None
    hist_best = max(hist) if hist else None
    hist_count = len(hist)

    # Expected daily output for this month
    days_in_month = 30  # close enough for all months
    daily_target = monthly_target / days_in_month

    # Rating vs daily target
    ratio = produced / daily_target if daily_target else 0
    if ratio >= 0.90:
        rating, rating_color = "EXCELLENT", "#1D9E75"
    elif ratio >= 0.70:
        rating, rating_color = "GOOD", "#5BAD6F"
    elif ratio >= 0.45:
        rating, rating_color = "FAIR", "#EF9F27"
    else:
        rating, rating_color = "POOR", "#D85A30"

    # Gauge scale: 0 → daily_target × 1.35 (gives breathing room above 100%)
    scale_max = daily_target * 1.35
    needle_pct = min(produced / scale_max * 100, 100) if scale_max else 0
    avg_pct = min(hist_avg / scale_max * 100, 100) if hist_avg and scale_max else None
    # Zone boundaries as % of scale_max
    # Poor: 0–45%, Fair: 45–70%, Good: 70–90%, Excellent: 90–100%
    z_poor  = round(0.45 / 1.35 * 100, 1)   # 33.3%
    z_fair  = round(0.70 / 1.35 * 100, 1)   # 51.9%
    z_good  = round(0.90 / 1.35 * 100, 1)   # 66.7%
    # excellent fills the rest

    sub_parts = [f"Today {produced:.1f} kWh"]
    if hist_avg is not None:
        sub_parts.append(f"Avg {hist_avg:.1f} kWh ({hist_count}d)")
    if hist_best is not None:
        sub_parts.append(f"Best {hist_best:.1f} kWh")
    sub_parts.append(f"Monthly daily target {daily_target:.0f} kWh")

    return {
        "rating": rating,
        "rating_color": rating_color,
        "needle_pct": round(needle_pct, 1),
        "avg_pct": round(avg_pct, 1) if avg_pct is not None else None,
        "z_poor": z_poor,
        "z_fair": z_fair,
        "z_good": z_good,
        "sub": " · ".join(sub_parts),
        "hist_count": hist_count,
    }


def _break_even(cfg: dict, as_of_date: str) -> dict:
    """
    Project break-even using a year-by-year compound model:
      - Electricity savings escalate 3%/yr (PSE&G historical avg)
      - SREC held flat at $76.50/yr per 1,000 kWh
      - Annual consumption baseline: 10,100 kWh (from pre-solar bills)
      - Annual production baseline: annual_target_kwh from config

    Already-earned value comes from the actual DB records up to as_of_date.
    Remaining value is projected forward year by year until cumulative >= net_cost.
    """
    RATE_ESCALATION  = 0.03          # 3% per year
    ANNUAL_KWH       = 10_100        # pre-solar consumption baseline
    PTO              = date.fromisoformat(PTO_DATE)
    as_of            = date.fromisoformat(as_of_date)

    conn = database.get_conn()
    row = conn.execute(
        "SELECT SUM(total_value) as earned, COUNT(*) as days FROM daily_readings WHERE date <= ?",
        (as_of_date,),
    ).fetchone()
    conn.close()

    net_cost     = cfg.get("net_cost", 16610)
    srec_rate    = cfg.get("srec_rate", 76.5)
    pseg_rate    = cfg.get("pseg_rate", 0.276)
    annual_prod  = cfg.get("annual_target_kwh", 13400)

    total_earned  = row["earned"] or 0.0
    remaining     = max(net_cost - total_earned, 0)
    pct_paid      = _pct(total_earned, net_cost)

    if remaining <= 0:
        return {
            "label": "Paid off!",
            "sub": f"${total_earned:,.0f} earned — done!",
            "pct_paid": pct_paid,
            "total_earned": total_earned,
            "remaining": 0,
            "break_even_date": None,
            "year_by_year": [],
        }

    # --- Year-by-year projection ---
    # Year 1 starts at PTO_DATE. For each year compute:
    #   electricity_savings = min(annual_prod, ANNUAL_KWH) × rate × (1.03)^yr
    #   srec_income         = (annual_prod / 1000) × srec_rate  (flat)
    pto_year        = PTO.year
    base_elec_saved = min(annual_prod, ANNUAL_KWH) * pseg_rate  # year-0 rate

    cumulative       = total_earned
    year_by_year     = []
    break_even_date  = None

    for yr in range(0, 30):  # project up to 30 years out
        calendar_year  = pto_year + yr
        escalation     = (1 + RATE_ESCALATION) ** yr
        elec_savings   = round(base_elec_saved * escalation, 2)
        srec_income    = round((annual_prod / 1000) * srec_rate, 2)  # informational only
        annual_value   = round(elec_savings, 2)  # SRECs excluded until approval

        # How much of this year is still ahead of us?
        year_start = date(calendar_year, PTO.month, PTO.day)
        year_end   = date(calendar_year + 1, PTO.month, PTO.day)

        # Skip years already fully captured in DB
        if year_end <= as_of:
            year_by_year.append({
                "year": calendar_year,
                "elec_savings": elec_savings,
                "srec_income": srec_income,
                "annual_value": annual_value,
                "cumulative": round(cumulative, 2),
                "paid_off": cumulative >= net_cost,
            })
            continue

        # Partial or future year — project day by day
        daily_value = annual_value / 365
        current     = max(as_of, year_start)

        while current < year_end and cumulative < net_cost:
            cumulative     += daily_value
            current        += timedelta(days=1)
            if cumulative >= net_cost and break_even_date is None:
                break_even_date = current

        year_by_year.append({
            "year": calendar_year,
            "elec_savings": elec_savings,
            "srec_income": srec_income,
            "annual_value": annual_value,
            "cumulative": round(min(cumulative, net_cost), 2),
            "paid_off": cumulative >= net_cost,
        })

        if cumulative >= net_cost:
            break

    # Format label
    if break_even_date:
        delta_days = (break_even_date - as_of).days
        years  = delta_days // 365
        months = (delta_days % 365) // 30
        if years >= 1:
            label = f"~{years}yr" + (f" {months}mo" if months else "")
        else:
            label = f"~{months}mo" if months >= 1 else f"~{delta_days}d"
        sub = f"${total_earned:,.0f} earned · ${remaining:,.2f} left · {break_even_date.strftime('%b %Y')}"
    else:
        label = "N/A"
        sub   = "Not enough data yet"

    return {
        "label": label,
        "sub": sub,
        "pct_paid": pct_paid,
        "total_earned": total_earned,
        "remaining": remaining,
        "break_even_date": break_even_date,
        "year_by_year": year_by_year,
    }


def _delta_html(current: float, previous: float | None, unit: str = "", fmt: str = ".1f") -> str:
    """
    Returns a small inline HTML change badge comparing current to previous.
    Green arrow up for improvement, red for decline, grey dash for no data.
    """
    if previous is None or previous == 0:
        return '<span style="font-size:10px;color:#bbb;margin-left:4px">—</span>'
    diff = current - previous
    pct  = (diff / abs(previous)) * 100
    if abs(diff) < 0.01:
        return '<span style="font-size:10px;color:#bbb;margin-left:4px">—</span>'
    arrow = "▲" if diff > 0 else "▼"
    color = "#1D9E75" if diff > 0 else "#D85A30"
    val   = format(abs(diff), fmt)
    return (
        f'<span style="font-size:10px;color:{color};margin-left:6px;white-space:nowrap">'
        f'{arrow} {val}{unit} ({abs(pct):.0f}%)</span>'
    )


def _insights(row: dict, cum: dict, cfg: dict, monthly_target: int) -> list[tuple[str, str]]:
    items = []
    produced = row["produced"]
    net = row["net"]
    srec_progress = cum["srec_progress_kwh"]
    monthly_kwh = cum["monthly_kwh"]

    perf_pct = _pct(produced, SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS)
    if perf_pct >= 80:
        items.append(("#1D9E75", f"Excellent output — {produced:.1f} kWh is {perf_pct:.0f}% of theoretical max ({SYSTEM_CAPACITY_KW} kW × {PEAK_SUN_HOURS} hrs)"))
    elif perf_pct >= 50:
        items.append(("#EF9F27", f"Good output — {produced:.1f} kWh is {perf_pct:.0f}% of theoretical max; cloud cover likely reduced yield"))
    else:
        items.append(("#D85A30", f"Low output — {produced:.1f} kWh is {perf_pct:.0f}% of theoretical max; possible clouds or shading"))

    if net > 0:
        items.append(("#1D9E75", f"{net:.2f} kWh net exported — ${net * cfg['pseg_rate']:.2f} in bill credits earned today"))
    else:
        items.append(("#378ADD", f"Net draw of {abs(net):.2f} kWh from grid today — consumed more than exported"))

    mtd_pct = _pct(monthly_kwh, monthly_target)
    month_name = date.fromisoformat(row["date"]).strftime("%B")
    items.append(("#378ADD", f"Month-to-date: {monthly_kwh:.1f} kWh ({mtd_pct:.1f}% of {monthly_target:,} kWh {month_name} target)"))

    return items


def _yby_rows(year_by_year: list, net_cost: float) -> str:
    rows = []
    for yr in year_by_year:
        is_paid    = yr["paid_off"]
        bg         = "#E1F5EE" if is_paid else ("white" if yr["year"] % 2 == 0 else "#fafafa")
        cum_color  = "#1D9E75" if is_paid else "#1a1a1a"
        marker     = " ✓ paid off" if is_paid else ""
        rows.append(
            f'<tr style="background:{bg}">'
            f'<td style="padding:6px 8px;color:#444">{yr["year"]}{marker}</td>'
            f'<td style="padding:6px 8px;text-align:right;color:#1D9E75">${yr["elec_savings"]:,.0f}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:600">${yr["annual_value"]:,.0f}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:600;color:{cum_color}">${yr["cumulative"]:,.0f} / ${net_cost:,.0f}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_report(target_date: str) -> Path:
    cfg = load_config()
    row = database.get_reading(target_date)
    if row is None:
        raise ValueError(f"No reading found for {target_date}")
    cum = database.get_cumulative()

    REPORTS_DIR.mkdir(exist_ok=True)

    d = row["date"]
    produced  = row["produced"]
    consumed  = row["consumed"]
    imported  = row["imported"]
    exported  = row["exported"]
    net       = row["net"]
    srec_earned         = row["srec_earned"]
    electricity_savings = row["electricity_savings"]
    self_consumed       = row["self_consumed"]
    total_value         = row["total_value"]

    monthly_kwh  = cum["monthly_kwh"]
    srec_progress = cum["srec_progress_kwh"]

    month = date.fromisoformat(d).month
    monthly_target = MONTHLY_TARGETS[month]
    month_name = date.fromisoformat(d).strftime("%B")

    # Previous day for change indicators
    prev_date = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    prev = database.get_reading(prev_date)

    pto_label   = _pto_duration(d)
    date_display = _fmt_date(d)
    perf        = _perf_meter(d, produced, monthly_target)
    be          = _break_even(cfg, d)

    srec_pct      = _pct(srec_progress, 1000)
    mtd_pct       = _pct(monthly_kwh, monthly_target)
    perf_pct      = _pct(produced, SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS)
    srec_days_left = math.ceil((1000 - srec_progress) / produced) if produced > 0 else "?"

    # Net metering bank since PTO
    BANK_TARGET = 500  # kWh — roughly one peak summer month of net grid draw
    bank_conn = database.get_conn()
    bank_row = bank_conn.execute(
        "SELECT SUM(net) as banked_kwh, AVG(consumed) as avg_consumed "
        "FROM daily_readings WHERE date >= ?", (PTO_DATE,)
    ).fetchone()
    bank_conn.close()
    banked_kwh   = bank_row["banked_kwh"] or 0.0
    avg_consumed = bank_row["avg_consumed"] or 1.0
    bank_value   = round(banked_kwh * cfg["pseg_rate"], 2)
    days_covered = round(banked_kwh / avg_consumed, 1) if banked_kwh > 0 else 0
    bank_pct     = _pct(banked_kwh, BANK_TARGET)

    insights = _insights(row, cum, cfg, monthly_target)
    insight_rows = "\n".join(
        f'    <div class="insight-row"><div class="dot" style="background:{color}"></div><div>{text}</div></div>'
        for color, text in insights
    )

    # Avg tick mark HTML (only if we have history)
    avg_tick_html = ""
    if perf["avg_pct"] is not None:
        avg_tick_html = f'<div style="position:absolute;left:{perf["avg_pct"]}%;top:-3px;bottom:-3px;width:2px;background:rgba(0,0,0,0.25);transform:translateX(-50%);border-radius:1px" title="Historical avg"></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Solar Summary — {date_display}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #1a1a1a; padding: 20px; }}
  .container {{ max-width: 700px; margin: 0 auto; background: white; border-radius: 16px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
  .date-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
  .date {{ font-size: 20px; font-weight: 600; }}
  .pto-badge {{ display: inline-block; background: #E1F5EE; color: #085041; border-radius: 20px; font-size: 12px; font-weight: 600; padding: 3px 12px; margin-bottom: 14px; }}
  .section {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #999; margin: 20px 0 10px; }}
  .grid4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }}
  .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }}
  .card {{ background: #f8f8f8; border-radius: 10px; padding: 12px 14px; }}
  .card .val {{ font-size: 22px; font-weight: 600; margin: 4px 0 2px; }}
  .card .lbl {{ font-size: 11px; color: #666; }}
  .card .sub {{ font-size: 11px; color: #999; margin-top: 2px; }}
  .flow-row {{ display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
  .flow-box {{ background: #f8f8f8; border-radius: 10px; padding: 10px 16px; text-align: center; min-width: 80px; }}
  .flow-box .fval {{ font-size: 18px; font-weight: 600; }}
  .flow-box .flbl {{ font-size: 11px; color: #666; margin-top: 2px; }}
  .flow-arrow {{ font-size: 20px; color: #bbb; }}
  .highlight {{ border: 2px solid #ddd; }}
  .bar-wrap {{ background: #f8f8f8; border-radius: 10px; padding: 10px 14px; margin-bottom: 8px; }}
  .bar-label {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 6px; color: #444; }}
  .bar-track {{ background: #e8e8e8; border-radius: 4px; height: 8px; }}
  .bar-fill {{ height: 8px; border-radius: 4px; }}
  .srec-card {{ background: #f8f8f8; border-radius: 10px; padding: 14px; margin-bottom: 12px; }}
  .srec-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .srec-title {{ font-size: 11px; color: #666; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }}
  .srec-val {{ font-size: 13px; font-weight: 600; color: #1D9E75; }}
  .srec-track {{ background: #e8e8e8; border-radius: 6px; height: 12px; overflow: hidden; }}
  .srec-fill {{ height: 12px; border-radius: 6px; background: #1D9E75; }}
  .srec-labels {{ display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-top: 5px; }}
  .divider {{ border: none; border-top: 1px solid #eee; margin: 20px 0; }}
  .insights {{ background: #f8f8f8; border-radius: 12px; padding: 14px; }}
  .insight-row {{ display: flex; gap: 10px; align-items: flex-start; padding: 7px 0; border-bottom: 1px solid #eee; font-size: 13px; color: #555; line-height: 1.5; }}
  .insight-row:last-child {{ border-bottom: none; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; flex-shrink: 0; }}
  .perf-meter {{ background: #f8f8f8; border-radius: 12px; padding: 14px 16px; margin-bottom: 18px; }}
  .perf-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .perf-section-lbl {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #999; }}
  .perf-rating {{ font-size: 14px; font-weight: 700; letter-spacing: 0.04em; }}
  .perf-gauge {{ position: relative; height: 16px; border-radius: 8px; overflow: visible; margin: 4px 0 8px; }}
  .perf-zone {{ position: absolute; top: 0; height: 100%; }}
  .perf-needle {{ position: absolute; top: -4px; bottom: -4px; width: 4px; border-radius: 2px; background: #1a1a1a; transform: translateX(-50%); box-shadow: 0 1px 3px rgba(0,0,0,0.3); }}
  .perf-zone-labels {{ display: flex; font-size: 10px; color: #bbb; margin-top: 4px; }}
  .perf-sub {{ font-size: 11px; color: #888; margin-top: 6px; }}
  @media (max-width: 500px) {{
    .grid4 {{ grid-template-columns: repeat(2, 1fr); }}
    .grid3 {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>
<div class="container">

  <div class="date-row">
    <div class="date">{date_display}</div>
  </div>
  <div class="pto-badge">{pto_label}</div>

  <!-- Performance meter -->
  <div class="perf-meter">
    <div class="perf-header">
      <span class="perf-section-lbl">Today's performance</span>
      <span class="perf-rating" style="color:{perf['rating_color']}">{perf['rating']}</span>
    </div>
    <div class="perf-gauge">
      <!-- colour zones -->
      <div class="perf-zone" style="left:0;width:{perf['z_poor']}%;background:#FFCDD2;border-radius:8px 0 0 8px"></div>
      <div class="perf-zone" style="left:{perf['z_poor']}%;width:{perf['z_fair'] - perf['z_poor']}%;background:#FFE0B2"></div>
      <div class="perf-zone" style="left:{perf['z_fair']}%;width:{perf['z_good'] - perf['z_fair']}%;background:#C8E6C9"></div>
      <div class="perf-zone" style="left:{perf['z_good']}%;width:{100 - perf['z_good']}%;background:#A5D6A7;border-radius:0 8px 8px 0"></div>
      <!-- historical avg tick -->
      {avg_tick_html}
      <!-- today's needle -->
      <div class="perf-needle" style="left:{perf['needle_pct']}%"></div>
    </div>
    <div class="perf-zone-labels">
      <span style="width:{perf['z_poor']}%">Poor</span>
      <span style="width:{perf['z_fair'] - perf['z_poor']}%">Fair</span>
      <span style="width:{perf['z_good'] - perf['z_fair']}%">Good</span>
      <span>Excellent</span>
    </div>
    <div class="perf-sub">{perf['sub']}</div>
  </div>

  <div class="section">Energy flow</div>
  <div class="flow-row">
    <div class="flow-box">
      <div class="fval" style="color:#1D9E75">{produced:.1f}</div>
      <div class="flbl">kWh produced{_delta_html(produced, prev["produced"] if prev else None, " kWh")}</div>
    </div>
    <div class="flow-arrow">vs</div>
    <div class="flow-box highlight">
      <div class="fval" style="color:#D85A30">{consumed:.1f}</div>
      <div class="flbl">kWh consumed{_delta_html(consumed, prev["consumed"] if prev else None, " kWh")}</div>
    </div>
    <div class="flow-arrow">=</div>
    <div class="flow-box" style="border:2px solid {'#1D9E75' if net >= 0 else '#D85A30'}">
      <div class="fval" style="color:{'#1D9E75' if net >= 0 else '#D85A30'}">{'+' if net >= 0 else ''}{net:.1f}</div>
      <div class="flbl">kWh {'net export' if net >= 0 else 'net import'}{_delta_html(net, prev["net"] if prev else None, " kWh")}</div>
    </div>
  </div>

  <div class="section">Key metrics</div>
  <div class="grid4">
    <div class="card">
      <div class="lbl">Net metering credit</div>
      <div class="val" style="color:{'#1D9E75' if net >= 0 else '#D85A30'}">{'+' if net >= 0 else ''}{net:.1f}</div>
      <div class="sub">kWh net today</div>
    </div>
    <div class="card">
      <div class="lbl">Month-to-date production</div>
      <div class="val">{monthly_kwh:.1f}</div>
      <div class="sub">kWh of {monthly_target:,} {month_name} target</div>
    </div>
    <div class="card">
      <div class="lbl">Break-even</div>
      <div class="val" style="color:{'#1D9E75' if be['remaining'] <= 0 else '#378ADD'}">{be['label']}</div>
      <div class="sub">{be['sub']}</div>
    </div>
  </div>

  <div class="section">Financial value today</div>
  <div class="grid3">
    <div class="card">
      <div class="lbl">Electricity savings</div>
      <div class="val" style="color:#1D9E75">${electricity_savings:.2f}{_delta_html(electricity_savings, prev["electricity_savings"] if prev else None, "", ".2f")}</div>
      <div class="sub">{min(produced, consumed):.2f} kWh × ${cfg['pseg_rate']:.3f}</div>
    </div>
    <div class="card">
      <div class="lbl">Total value</div>
      <div class="val" style="color:#1D9E75">${total_value:.2f}{_delta_html(total_value, prev["total_value"] if prev else None, "", ".2f")}</div>
      <div class="sub">electricity savings only</div>
    </div>
  </div>

  <div class="section">System performance</div>
  <div class="bar-wrap">
    <div class="bar-label"><span>Production vs theoretical max ({SYSTEM_CAPACITY_KW} kW × {PEAK_SUN_HOURS} hrs)</span><span><strong>{perf_pct:.0f}%</strong> — {produced:.1f} / {SYSTEM_CAPACITY_KW * PEAK_SUN_HOURS:.0f} kWh</span></div>
    <div class="bar-track"><div class="bar-fill" style="width:{perf_pct}%;background:#1D9E75"></div></div>
  </div>
  <div class="bar-wrap">
    <div class="bar-label"><span>Month-to-date vs {month_name} target</span><span><strong>{mtd_pct:.1f}%</strong> — {monthly_kwh:.1f} / {monthly_target:,} kWh</span></div>
    <div class="bar-track"><div class="bar-fill" style="width:{mtd_pct}%;background:#378ADD"></div></div>
  </div>
  <div class="bar-wrap">
    <div class="bar-label"><span>Break-even progress — ${cfg['net_cost']:,} installation cost</span><span><strong>{be['pct_paid']:.2f}%</strong> — ${be['total_earned']:,.2f} / ${cfg['net_cost']:,}</span></div>
    <div class="bar-track"><div class="bar-fill" style="width:{be['pct_paid']}%;background:#9B59B6"></div></div>
  </div>
  <div class="bar-wrap">
    <div class="bar-label"><span>Net metering bank since PTO</span><span><strong>{banked_kwh:.1f} kWh</strong> &nbsp;·&nbsp; ${bank_value:.2f} &nbsp;·&nbsp; ~{days_covered:.0f} days coverage</span></div>
    <div class="bar-track"><div class="bar-fill" style="width:{bank_pct}%;background:#378ADD"></div></div>
  </div>

  <div class="divider"></div>

  <div class="section">Payoff projection — electricity savings only, 3% annual rate escalation</div>
  <table style="width:100%;border-collapse:collapse;font-size:12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
    <thead>
      <tr style="color:#999;text-align:left;border-bottom:2px solid #eee">
        <th style="padding:6px 8px;font-weight:600">Year</th>
        <th style="padding:6px 8px;font-weight:600;text-align:right">Elec. savings</th>
        <th style="padding:6px 8px;font-weight:600;text-align:right">Annual value</th>
        <th style="padding:6px 8px;font-weight:600;text-align:right">Cumulative</th>
      </tr>
    </thead>
    <tbody>
{_yby_rows(be['year_by_year'], cfg['net_cost'])}
    </tbody>
  </table>

  <div class="divider"></div>

  <div class="section">Context &amp; insights</div>
  <div class="insights">
{insight_rows}
  </div>

</div>
</body>
</html>"""

    out_path = REPORTS_DIR / f"{d}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    path = build_report(target)
    print(f"Report written to: {path}")

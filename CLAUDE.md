# Solar Report — Project Briefing

A daily solar monitoring system for an Enphase installation in New Jersey.
Fetches production/consumption data from the Enphase Enlighten API v4,
stores it in SQLite, generates HTML reports, and sends email digests via Resend.

---

## Architecture

```
solar_report.py      — daily orchestrator (fetch → save → report → email); emails a failure alert and exits 1 on error
weekly_report.py     — weekly summary report + email (runs every Monday 6am)
monthly_report.py    — monthly summary report + email (runs 1st of month 6:30am)
gats_reminder.py     — monthly PJM-EIS GATS meter-reading reminder (runs 2nd Saturday, user-scheduled)
email_builder.py     — email-safe HTML for daily report (table layout, inline styles)
report_builder.py    — full HTML daily report (CSS layout, performance meter)
send_email.py        — Resend API delivery (send_raw() used by every report; send_failure_alert() for failed jobs)
enphase_api.py       — Enphase Enlighten API v4 (OAuth2, auto token refresh; MissingDataError for unpublished days)
database.py          — SQLite via sqlite3 (idempotent writes; MTD/lifetime are SUM queries; recompute_savings())
common.py            — shared constants/helpers: load_config, PTO_DATE, system size, MONTHLY_TARGETS, monthly_target(), rating()
tests/               — unittest suite: `python -m unittest discover -s tests -t .` (temp DB/config, no network)
rates.py             — PSE&G RS season/tier pricing (savings + break-even blended rate)
weather.py           — Open-Meteo weather/forecast fetch (peak solar hours 9am–2pm)
```

## Key Design Decisions

- **Import/export**: Uses `net = produced - consumed` only. No rgm_stats (it measured inverter output, not grid export).
- **Electricity savings**: what the day's consumption would have cost from PSE&G at Residential Service (RS) prices — `rates.py`, prices in `pseg_rs_rates`. RS is not time-of-use: price varies only by season (summer Jun–Sep) and the summer 600 kWh/month tier (calendar month as a proxy). Because every bill shows 0 kWh billed (net metering), all consumption is avoided cost while the bank is positive; only a draw the bank can't cover is excluded (`database.compute_electricity_savings`). Break-even uses `rates.blended_annual_rate` (~$0.271).
- **SRECs**: NJ-approved as of 2026-09-13, rate $85/MWh. Included in `total_value` (`database.py`) and in break-even projections (`report_builder.py`). Historical `daily_readings` rows were backfilled from PTO at the approved rate. Live "SREC income" card in all reports.
- **Lifetime production**: `database.get_lifetime_production(PTO_DATE)` — `SUM(produced)` since PTO. Displayed as its own card in all four report surfaces (daily HTML, daily email, weekly, monthly) alongside the SREC card.
- **Practical max**: The daily "Production vs practical max" bar and the forecast kWh estimates use `PRACTICAL_MAX_KWH` (72 kWh, `common.py`) — a clear-day ceiling just above the 70.3 kWh record. Not nameplate × sun-hours (11.48 kW × 8 = 92), which the 8.12 kW AC inverters clip well below.
- **Weather**: Uses hourly WMO codes during **peak solar hours only (9am–2pm)** to avoid overnight conditions skewing the description.
- **Email subject**: Colour-dot prefix (🟢🟡🟠🔴) based on production vs daily target ratio.
- **Headline**: One-sentence summary with rating opener, all-time percentile (top X%), weather context, % change vs yesterday, tomorrow forecast. Special cases: "🏆 New record" for rank 1, "lowest day yet" for last place.
- **Net metering bank**: Calibrated to PSE&G's own cumulative net-metering figure (`BANK_ANCHOR_DATE`/`BANK_ANCHOR_KWH` in `database.py`) plus `SUM(net)` for Enphase days after the anchor. Re-anchor from the bill's "Net Metering Program" table whenever a new bill arrives — Enphase's daily-total telemetry drifts from PSE&G's meter over time.
- **Break-even**: Year-by-year compound model (`report_builder._break_even`). Electricity savings escalate at `rate_escalation` (config, 5%/yr as of 2026-09-20 — set by comparing PSE&G bill history 2022–2025, which showed an 11.6%/yr average effective-rate CAGR driven by a PJM capacity-auction spike now capped by a FERC-approved price collar; 5% splits the difference between that spike and analysts' steadier 3–5%/yr grid-modernization estimate through 2028). Annual consumption cap is `annual_consumption_kwh` (config, 11,400 kWh — the actual 2023–2025 average from PSE&G bills, not just an installer estimate). SREC income held flat at `srec_rate`/MWh and included in annual value. Re-tune both config values periodically against fresh PSE&G bill history (same cadence as the quarterly rate updates below).
- **GATS reading**: `gats_reminder.py`'s monthly reminder reports Enphase's raw cumulative production as of the **last day of the previous month** (`enphase_api.fetch_cumulative_kwh_on`, summed from the daily `/energy_lifetime` array in one call, floored to whole kWh) — no offset or adjustment. GATS entries are per generation month and the meter date must fall in that month's window (1st of the month through the 15th of the next), so a live mid-month snapshot would pull the next month's production into the wrong period. The month-end method reproduced the hand-entered Jul/Aug 2026 readings exactly (9,140 / 10,617). Certificates-to-date are `(reading_kwh - 2990) // 1000` (`GATS_BASELINE_KWH`: the 2,990 kWh initial meter reading registered in GATS at PTO; only production above it earns certificates); the delta, certificates this period and running totals are all derived from the same Enphase month-end history (prior month-end, or the day before PTO for the first month), so nothing is stored between runs and re-running a month resends an identical email. The email also carries a 6-month history table (month-end reading and generated kWh per month, clamped to the PTO month, whose baseline is the day before PTO to match GATS — April = 1,397). Fails loudly via email on a fetch error, missing data for the month, a negative reading, a decrease from the prior reading, or a >3,000 kWh jump.

## Configuration (`config.json` — gitignored)

| Key | Notes |
|---|---|
| `pseg_rs_rates` | RS all-in $/kWh: `winter`, `summer_first_tier`, `summer_over_tier`, `tier_kwh`, `summer_months`, `summer_consumption_share`. From the June 1, 2026 rate sheet (Jul 15 transmission change not yet reflected); update when PSE&G publishes new rates |
| `srec_rate` | SREC value in $/MWh (also used for GATS certificate $ estimates) |
| `gats_entry_url` | PJM-EIS GATS generation entry page — linked in the monthly GATS reminder email |
| `net_cost` | Net system cost after incentives |
| `annual_target_kwh` | 13,400 kWh from installer estimate |
| `annual_consumption_kwh` | Pre-solar consumption baseline for break-even's savings cap — 11,400 kWh, from actual 2023–2025 PSE&G bill history (see "Break-even" above), not the installer estimate |
| `rate_escalation` | Assumed annual electricity-rate growth for break-even projections — 0.05 (5%/yr); re-tune against fresh bill history periodically |
| `latitude` / `longitude` | Used for Open-Meteo weather API |
| `resend_api_key` | Resend email delivery |

## Database Schema

```sql
daily_readings (date PK, produced, consumed, imported, exported, net,
                self_consumed, srec_earned, electricity_savings, total_value)
```
Month-to-date and lifetime production are `SUM(produced)` queries (`database.get_month_to_date`,
`get_lifetime_production`). The legacy `cumulative` counter table is unused. Saving a past day
recomputes every later day's savings; after a rate or SREC change run `database.recompute_savings(from_date)`.

## SDLC Workflow

- `main` = production (what the scheduled tasks run). Never commit directly.
- Feature branches: `feature/<name>`, fix branches: `fix/<name>`
- Every PR must include updated README and screenshots (`docs/screenshot-*.png`)
- Run `python -m unittest discover -s tests -t .` before every PR; add tests for new logic
- Merge via GitHub PR (squash merge). Clean up branch after merge.
- GitHub remote: `https://github.com/adeel825/solar-report.git`
- GitHub MCP server is configured at user scope — use it for branch/PR/merge operations.

## Running Reports Manually

```powershell
# Daily (yesterday by default, or pass a date)
python solar_report.py
python solar_report.py 2026-04-18

# Weekly (last full week by default, or pass a Monday date)
python weekly_report.py
python weekly_report.py 2026-04-14

# Monthly (previous month by default, or pass year + month)
python monthly_report.py
python monthly_report.py 2026 4

# GATS reminder (previous month-end reading by default, or pass YYYY-MM)
python gats_reminder.py
python gats_reminder.py 2026-09
```

## Scheduled Tasks (Windows Task Scheduler)

| Task | Schedule |
|---|---|
| `SolarDailyReport` | Daily 5:00 AM |
| `SolarWeeklyReport` | Monday 6:00 AM |
| `SolarMonthlyReport` | 1st of month 6:30 AM |
| `SolarGatsReminder` | 2nd Saturday of month, 9:00 AM (user-scheduled, not yet registered) |

## PTO Date

April 2, 2026. Set once in `common.py` as `PTO_DATE = "2026-04-02"`.
All period calculations are clamped to this date. `gats_reminder.py` imports `PTO_DATE`
only to start its history table's first month at the day before PTO; the reading itself is
unadjusted (see "GATS reading" above).

## Taking Screenshots

```python
import subprocess, time
from PIL import Image
import numpy as np

chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
url = 'file:///C:/dev/solar-report/reports/<file>.html'
subprocess.run([chrome, '--headless', '--disable-gpu',
                '--screenshot=docs/screenshot-raw.png',
                '--window-size=700,1400', url], timeout=15)
time.sleep(1)
# Auto-crop to content using Pillow (bg = #f5f5f5 = 245,245,245)
```

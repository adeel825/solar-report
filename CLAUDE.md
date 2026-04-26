# Solar Report — Project Briefing

A daily solar monitoring system for an Enphase installation in New Jersey.
Fetches production/consumption data from the Enphase Enlighten API v4,
stores it in SQLite, generates HTML reports, and sends email digests via Resend.

---

## Architecture

```
solar_report.py      — daily orchestrator (fetch → save → report → email)
weekly_report.py     — weekly summary report + email (runs every Monday 6am)
monthly_report.py    — monthly summary report + email (runs 1st of month 6:30am)
email_builder.py     — email-safe HTML for daily report (table layout, inline styles)
report_builder.py    — full HTML daily report (CSS layout, performance meter)
send_email.py        — Resend API delivery for daily email
enphase_api.py       — Enphase Enlighten API v4 (OAuth2, auto token refresh)
database.py          — SQLite via sqlite3 (idempotent writes, cumulative counters)
weather.py           — Open-Meteo weather/forecast fetch (peak solar hours 9am–2pm)
```

## Key Design Decisions

- **Import/export**: Uses `net = produced - consumed` only. No rgm_stats (it measured inverter output, not grid export).
- **Electricity savings**: `min(produced, consumed) × pseg_rate`. SRECs excluded until NJ approval.
- **SRECs**: Displayed as greyed "pending" preview in all reports. One-line re-enable in `database.py` when approved.
- **Weather**: Uses hourly WMO codes during **peak solar hours only (9am–2pm)** to avoid overnight conditions skewing the description.
- **Email subject**: Colour-dot prefix (🟢🟡🟠🔴) based on production vs daily target ratio.
- **Headline**: One-sentence summary with rating opener, all-time percentile (top X%), weather context, % change vs yesterday, tomorrow forecast. Special cases: "🏆 New record" for rank 1, "lowest day yet" for last place.
- **Net metering bank**: `SUM(net)` since PTO_DATE — cumulative kWh exported to grid.
- **Break-even**: Year-by-year compound model, 3% annual rate escalation, SREC excluded.

## Configuration (`config.json` — gitignored)

| Key | Notes |
|---|---|
| `pseg_rate` | Combined delivery + supply rate (update quarterly) |
| `pseg_supply_rate` | Supply component — changes quarterly |
| `net_cost` | Net system cost after incentives |
| `annual_target_kwh` | 13,400 kWh from installer estimate |
| `latitude` / `longitude` | Used for Open-Meteo weather API |
| `resend_api_key` | Resend email delivery |

## Database Schema

```sql
daily_readings (date PK, produced, consumed, imported, exported, net,
                self_consumed, srec_earned, electricity_savings, total_value)
cumulative     (key PK, value — keys: lifetime_kwh, monthly_kwh,
                srec_progress_kwh, monthly_reset_month)
```

## SDLC Workflow

- `main` = production (what the scheduled tasks run). Never commit directly.
- Feature branches: `feature/<name>`, fix branches: `fix/<name>`
- Every PR must include updated README and screenshots (`docs/screenshot-*.png`)
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
```

## Scheduled Tasks (Windows Task Scheduler)

| Task | Schedule |
|---|---|
| `SolarDailyReport` | Daily 5:00 AM |
| `SolarWeeklyReport` | Monday 6:00 AM |
| `SolarMonthlyReport` | 1st of month 6:30 AM |

## PTO Date

April 9, 2026. Set in `report_builder.py` and `email_builder.py` as `PTO_DATE = "2026-04-09"`.
All period calculations are clamped to this date.

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

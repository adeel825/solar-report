"""
PSE&G Residential Service (RS) electricity pricing.

RS is not time-of-use: the all-in $/kWh price (distribution + per-kWh riders +
Basic Generation Service + transmission) depends only on the season and on how
much has been used so far in the month (summer has a higher tier above 600 kWh).
Prices live in config.json under "pseg_rs_rates", taken from PSE&G's
"Understanding Your PSE&G Bill" rate sheet.

Calendar months stand in for billing months (PSE&G reads the meter around the
5th–9th), so the 600 kWh tier boundary is approximate.
"""
from datetime import date

DEFAULT_SUMMER_MONTHS = (6, 7, 8, 9)   # PSE&G: summer = June through September


def _rates(cfg: dict) -> dict:
    r = cfg["pseg_rs_rates"]
    return {
        "winter": r["winter"],
        "summer_first": r["summer_first_tier"],
        "summer_over": r["summer_over_tier"],
        "tier_kwh": r.get("tier_kwh", 600),
        "summer_months": tuple(r.get("summer_months", DEFAULT_SUMMER_MONTHS)),
        "summer_share": r.get("summer_consumption_share", 0.55),
    }


def is_summer(d: str, cfg: dict) -> bool:
    return date.fromisoformat(d).month in _rates(cfg)["summer_months"]


def day_cost(kwh: float, d: str, mtd_kwh_before: float, cfg: dict) -> float:
    """
    What `kwh` of grid electricity on date `d` costs under RS, given
    `mtd_kwh_before` kWh already used earlier in the same calendar month.
    """
    r = _rates(cfg)
    kwh = max(kwh, 0.0)
    if not is_summer(d, cfg):
        return kwh * r["winter"]
    first_left = max(r["tier_kwh"] - max(mtd_kwh_before, 0.0), 0.0)
    first = min(kwh, first_left)
    return first * r["summer_first"] + (kwh - first) * r["summer_over"]


def marginal_rate(d: str, mtd_kwh: float, cfg: dict) -> float:
    """Price of the next kWh on date `d` with `mtd_kwh` already used this month."""
    r = _rates(cfg)
    if not is_summer(d, cfg):
        return r["winter"]
    return r["summer_first"] if mtd_kwh < r["tier_kwh"] else r["summer_over"]


def rate_label(d: str, mtd_kwh_before: float, kwh: float, cfg: dict) -> str:
    """Short description of which RS price(s) applied to a day's usage."""
    r = _rates(cfg)
    if not is_summer(d, cfg):
        return f"winter ${r['winter']:.4f}"
    if mtd_kwh_before + kwh <= r["tier_kwh"]:
        return f"summer ${r['summer_first']:.4f}"
    if mtd_kwh_before >= r["tier_kwh"]:
        return f"summer &gt;{r['tier_kwh']} kWh ${r['summer_over']:.4f}"
    return "summer, crosses 600 kWh tier"


def blended_annual_rate(cfg: dict, annual_kwh: float) -> float:
    """
    Usage-weighted average RS price for a year of `annual_kwh` consumption, with
    `summer_consumption_share` of it in the four summer months (0.55 from
    2026 Enphase data). Used by the break-even projection.
    """
    r = _rates(cfg)
    n_summer = len(r["summer_months"])
    summer_kwh = annual_kwh * r["summer_share"]
    winter_kwh = annual_kwh - summer_kwh
    per_month = summer_kwh / n_summer if n_summer else 0.0
    first = min(per_month, r["tier_kwh"])
    summer_cost = n_summer * (first * r["summer_first"] + (per_month - first) * r["summer_over"])
    return (summer_cost + winter_kwh * r["winter"]) / annual_kwh if annual_kwh else r["winter"]

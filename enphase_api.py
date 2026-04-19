import json
import base64
import requests
from datetime import date, timedelta
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
BASE_URL = "https://api.enphaseenergy.com/api/v4/systems"
TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


def refresh_access_token(cfg):
    credentials = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode()
    ).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": cfg["refresh_token"],
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    cfg["access_token"] = tokens["access_token"]
    if "refresh_token" in tokens:
        cfg["refresh_token"] = tokens["refresh_token"]
    save_config(cfg)
    print("  [token refreshed]")
    return cfg


def api_get(url, params, cfg, retry=True):
    import time
    params["key"] = cfg["api_key"]
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}

    for attempt in range(4):
        resp = requests.get(url, params=params, headers=headers)
        if resp.status_code == 401 and retry:
            cfg = refresh_access_token(cfg)
            headers = {"Authorization": f"Bearer {cfg['access_token']}"}
            resp = requests.get(url, params=params, headers=headers)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            print(f"  [rate limited, waiting {wait}s...]")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json(), cfg

    resp.raise_for_status()  # re-raise after exhausting retries
    return resp.json(), cfg


def fetch_day(target_date: str | None = None) -> dict:
    """Fetch solar data for target_date (YYYY-MM-DD). Defaults to yesterday."""
    cfg = load_config()
    system_id = cfg["system_id"]

    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    base = f"{BASE_URL}/{system_id}"

    # Fetch full lifetime arrays (no date filter) — the date-filtered endpoint
    # returns empty arrays for recently processed days, so we always pull the
    # full history and extract by index instead.

    # --- Produced (energy_lifetime) ---
    data, cfg = api_get(f"{base}/energy_lifetime", {}, cfg)
    produced_wh = _extract_lifetime_value(data, target_date, "production")

    # --- Consumed (consumption_lifetime) ---
    data, cfg = api_get(f"{base}/consumption_lifetime", {}, cfg)
    consumed_wh = _extract_lifetime_value(data, target_date, "consumption")

    # --- Derived values ---
    produced = round(produced_wh / 1000, 3)
    consumed = round(consumed_wh / 1000, 3)
    net      = round(produced - consumed, 3)   # positive = net export, negative = net import

    return {
        "date":     target_date,
        "produced": produced,
        "consumed": consumed,
        "net":      net,
        # imported/exported stored as net-derived daily totals for DB compatibility
        "exported": round(max(net, 0), 3),
        "imported": round(max(-net, 0), 3),
    }


def _extract_lifetime_value(data: dict, target_date: str, key: str) -> float:
    """
    energy_lifetime / consumption_lifetime returns:
      {"start_date": "YYYY-MM-DD", "production": [wh, ...], ...}
    Finds the index corresponding to target_date.
    """
    start = data.get("start_date")
    values = data.get(key, [])
    if not start or not values:
        return 0.0
    start_d = date.fromisoformat(start)
    target_d = date.fromisoformat(target_date)
    idx = (target_d - start_d).days
    if 0 <= idx < len(values):
        return float(values[idx])
    return 0.0



if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Fetching data for: {target or 'yesterday'}...")
    result = fetch_day(target)
    print("\nResult:")
    for k, v in result.items():
        unit = "kWh" if k != "date" else ""
        print(f"  {k:<12} {v} {unit}")

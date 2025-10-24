# count_active.py
# Räknar aktiva affärer = öppna positioner + pending/working orders
# Stdout: bara ett heltal (eller JSON med --json). Exit code 0 om <= cap, annars 2.

import os, sys, json, time, requests
from capital_session import get_session

BASE_URL = os.getenv("BASE_URL", "https://demo-api-capital.backend-capital.com")
MAX_POSITIONS_CAP = int(os.getenv("MAX_POSITIONS_CAP", "10"))
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "DEMO")  # valfritt, skickas som header


def build_headers():
    sess = get_session()
    return {
        "X-CAP-API-KEY": os.getenv("API_KEY"),
        "CST": sess["CST"],
        "X-SECURITY-TOKEN": sess["X-SECURITY-TOKEN"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ACCOUNT-TYPE": ACCOUNT_TYPE,
    }


def _get(url: str, *, retry_on_401=True):
    headers = build_headers()
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 401 and retry_on_401:
        # token kan ha gått ut — prova en gång till med ny session
        time.sleep(0.3)
        headers = build_headers()
        r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 429:
        time.sleep(0.6)
        headers = build_headers()
        r = requests.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"GET {url} → {r.status_code}: {r.text[:200]}")
    try:
        return r.json() or {}
    except Exception:
        return {}


def count_positions() -> int:
    data = _get(f"{BASE_URL}/api/v1/positions")
    items = data.get("positions") or data.get("items") or data.get("results") or []
    return len(items)


def count_working_orders() -> int:
    data = _get(f"{BASE_URL}/api/v1/workingorders")
    items = data.get("workingOrders") or data.get("items") or data.get("results") or []
    return len(items)


def main():
    try:
        pos = count_positions()
        ords = count_working_orders()
        total = pos + ords
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps({"positions": pos, "orders": ords, "active_total": total}))
    else:
        print(total)

    sys.exit(0 if total <= MAX_POSITIONS_CAP else 2)


if __name__ == "__main__":
    main()

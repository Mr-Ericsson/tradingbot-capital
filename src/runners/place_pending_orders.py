# place_pending_orders.py
# Lägg flera pending BUY STOP-orders från orders.csv via Capital DEMO
# - Använder /workingorders (pending)
# - Bekräftar via /confirms/{dealReference}
# - Loggar till open_trades.csv
# - Storlek ≈ 100 USD per trade (TARGET_RISK_USD) – (margin-baserad i denna version)

import os
import sys
import csv
import time
import requests
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

# ==== MJUKT TAK FÖR AKTIVA TRADES (open + pending) ====
MAX_TRADES = 10  # <- ändra här när du vill

# Standardiserad session
from capital_session import get_session

# ==== ENV / KONFIG ====
BASE_URL = os.getenv("BASE_URL", "https://demo-api-capital.backend-capital.com")
TARGET_RISK_USD = float(os.getenv("TARGET_RISK_USD", "100"))  # 100 USD standard

# SL/TP-styrning (procent)
SL_PCT = float(os.getenv("SL_PCT", "3"))  # 3%
TP_PCT = float(os.getenv("TP_PCT", "3"))  # 3%
FORCE_PERCENT_SLTP = os.getenv("FORCE_PERCENT_SLTP", "true").lower() in (
    "1",
    "true",
    "yes",
    "y",
)
PRICE_DECIMALS = int(os.getenv("PRICE_DECIMALS", "2"))
TARGET_MARGIN_USD = float(
    os.getenv("TARGET_MARGIN_USD", "100")
)  # om du vill styra via margin

# ---- Auth headers (återanvänds i alla anrop) ----
_sess = get_session()
headers = {
    "X-CAP-API-KEY": os.getenv("API_KEY"),
    "CST": _sess["CST"],
    "X-SECURITY-TOKEN": _sess["X-SECURITY-TOKEN"],
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# --- helpers to fetch market info safely ---
def fetch_market(epic: str) -> dict:
    r = requests.get(f"{BASE_URL}/api/v1/markets/{epic}", headers=headers, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch market info for {epic}: {r.text[:200]}")
    return r.json()


def leverage_from_market(market_json: dict) -> float:
    try:
        instr = market_json.get("instrument", market_json)
        lev = instr.get("leverage")
        if lev:
            return float(lev)
        marginFactorLong = instr.get("marginFactorLong") or instr.get("marginFactor")
        marginUnit = (
            instr.get("marginUnit") or instr.get("marginFactorUnit") or ""
        ).upper()
        if marginFactorLong and marginUnit in ("", "PERCENT", "PERCENTAGE"):
            return 100.0 / float(marginFactorLong)
    except Exception:
        pass
    return 1.0  # defensiv fallback


# Cache för marketinfo per EPIC
_MARKET_CACHE = {}


def _fetch_market_info(epic: str):
    if epic in _MARKET_CACHE:
        return _MARKET_CACHE[epic]
    url1 = f"{BASE_URL}/api/v1/markets/{epic}"
    try:
        r1 = requests.get(url1, headers=headers, timeout=20)
        if r1.status_code == 200:
            data = r1.json()
            _MARKET_CACHE[epic] = data
            return data
    except Exception:
        pass
    url2 = f"{BASE_URL}/api/v1/markets?search={epic}"
    try:
        r2 = requests.get(url2, headers=headers, timeout=20)
        if r2.status_code == 200:
            data = r2.json()
            items = (
                data
                if isinstance(data, list)
                else data.get("markets") or data.get("results") or []
            )
            match = None
            for it in items:
                if (it.get("epic") or "").upper() == epic.upper():
                    match = it
                    break
            _MARKET_CACHE[epic] = match or data
            return _MARKET_CACHE[epic]
    except Exception:
        pass
    _MARKET_CACHE[epic] = None
    return None


def _deep_num_local(d: dict, keys: tuple[str, ...]) -> float | None:
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, dict):
            x = _deep_num_local(v, keys)
            if x is not None:
                return x
    return None


def _deep_str_local(d: dict, keys: tuple[str, ...]) -> str | None:
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            x = _deep_str_local(v, keys)
            if x:
                return x
    return None


def _extract_margin_fraction(market_info, direction: str = "BUY") -> float | None:
    if not isinstance(market_info, dict):
        return None
    dir_up = (direction or "BUY").upper()
    if dir_up == "SELL":
        mf = (
            _deep_num_local(market_info, ("marginFactorShort",))
            or _deep_num_local(market_info, ("marginFactor",))
            or _deep_num_local(market_info, ("marginFactorLong",))
        )
    else:
        mf = (
            _deep_num_local(market_info, ("marginFactorLong",))
            or _deep_num_local(market_info, ("marginFactor",))
            or _deep_num_local(market_info, ("marginFactorShort",))
        )
    if not mf:
        return None
    unit = (
        _deep_str_local(market_info, ("marginFactorUnit", "marginUnit")) or ""
    ).upper()
    if unit in ("PERCENT", "PERCENTAGE", ""):
        return float(mf) / 100.0
    return None


def get_size_rules(epic: str) -> tuple[float | None, float | None]:
    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/markets/{epic}", headers=headers, timeout=15
        )
        data = r.json() if r.status_code == 200 else {}
    except Exception:
        data = {}
    min_size = (
        _deep_num_local(data, ("minDealSize",))
        or _deep_num_local(data, ("minimumDealSize",))
        or _deep_num_local(data, ("minSize",))
        or None
    )
    step = (
        _deep_num_local(data, ("dealSizeIncrement",))
        or _deep_num_local(data, ("sizeIncrement",))
        or _deep_num_local(data, ("minDealSizeIncrement",))
        or _deep_num_local(data, ("unitIncrement",))
        or _deep_num_local(data, ("step",))
        or _deep_num_local(data, ("lotStep",))
        or None
    )
    if (step is None) and (min_size is not None) and (0 < min_size < 1):
        s = f"{min_size}"
        if "." in s:
            decimals = len(s.split(".")[1].rstrip("0"))
            if decimals > 0:
                step = float("0." + ("0" * (decimals - 1)) + "1")
    if step is None:
        step = 0.01
    return (min_size, step)


def quantize_size_to_step(
    size: float, min_size: float | None, step: float | None
) -> float:
    if not step or step <= 0:
        step = 0.01
    d_step = Decimal(str(step))
    q = (Decimal(str(size)) / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    size_adj = float(q * d_step)
    if min_size and size_adj < min_size:
        size_adj = float(min_size)
    return size_adj


def read_orders_csv(path="orders.csv"):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def calc_size(
    epic: str,
    entry_price: float,
    sl_price: float = None,
    leverage_hint: float = None,
    **kwargs,
) -> float:
    """
    Marginal-styrd storlek:
      Notional = TARGET_MARGIN_USD * leverage
      SIZE     = Notional / ENTRY
    """
    try:
        entry = float(entry_price)
        if entry <= 0:
            return 0.0
    except Exception:
        return 0.0
    lev = None
    if leverage_hint is not None:
        try:
            lev = float(leverage_hint)
        except Exception:
            lev = None
    if not lev or lev <= 0:
        try:
            lev = leverage_from_market(fetch_market(epic))
        except Exception:
            lev = 1.0
    notional = TARGET_MARGIN_USD * float(lev)
    raw_size = notional / entry
    try:
        min_size, step = get_size_rules(epic)
        q_size = quantize_size_to_step(raw_size, min_size, step)
        if q_size <= 0 and (min_size or 0) > 0:
            q_size = float(min_size)
        return round(q_size, 2)
    except Exception:
        return round(raw_size, 2)


def confirm_deal(deal_reference: str):
    url = f"{BASE_URL}/api/v1/confirms/{deal_reference}"
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def compute_levels_from_pct(
    entry: float, direction: str = "BUY", sl_pct: float = SL_PCT, tp_pct: float = TP_PCT
):
    direction = (direction or "BUY").upper()
    if direction == "SELL":
        sl = round(entry * (1 + sl_pct / 100.0), PRICE_DECIMALS)
        tp = round(entry * (1 - tp_pct / 100.0), PRICE_DECIMALS)
    else:
        sl = round(entry * (1 - sl_pct / 100.0), PRICE_DECIMALS)
        tp = round(entry * (1 + tp_pct / 100.0), PRICE_DECIMALS)
    return sl, tp


def place_pending_buy_stop(epic: str, entry: float, sl: float, tp: float, size: float):
    payload = {
        "epic": epic,
        "direction": "BUY",
        "type": "STOP",  # pending BUY STOP
        "level": float(entry),  # ENTRY
        "size": float(size),
        "stopLevel": float(sl),  # STOP LOSS
        "profitLevel": float(tp),  # TAKE PROFIT
        "guaranteedStop": False,
    }
    url = f"{BASE_URL}/api/v1/workingorders"
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    if r.status_code not in (200, 201):
        print(f"[FEL] {epic} → {r.status_code} {r.text}")
        return None, None, "REJECTED"
    deal_ref = None
    try:
        deal_ref = r.json().get("dealReference")
    except Exception:
        pass
    deal_id = None
    status = "UNKNOWN"
    if deal_ref:
        time.sleep(0.4)
        conf = confirm_deal(deal_ref)
        if conf:
            status = conf.get("dealStatus") or conf.get("status") or status
            affected = conf.get("affectedDeals") or []
            if isinstance(affected, list) and affected:
                deal_id = affected[0].get("dealId") or affected[0].get("dealIdOrigin")
    print(
        f"[OK] {epic} → Pending BUY STOP @ {entry} | SL {sl} | TP {tp} | Size {size} | status {status} | ref {deal_ref or '-'}"
    )
    return deal_ref, deal_id, status


def append_open_trades_log(
    epic, entry, sl, tp, size, deal_ref, deal_id, status, log_path="open_trades.csv"
):
    file_exists = os.path.exists(log_path)
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(
                [
                    "timestamp",
                    "epic",
                    "entry",
                    "sl",
                    "tp",
                    "size",
                    "dealReference",
                    "dealId",
                    "status",
                ]
            )
        w.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                epic,
                entry,
                sl,
                tp,
                size,
                deal_ref or "",
                deal_id or "",
                status,
            ]
        )


# ---- Räknare (open + pending) ----
def count_positions() -> int:
    url = f"{BASE_URL}/api/v1/positions"
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return 0
    data = r.json() or {}
    items = data.get("positions") or data.get("items") or data.get("results") or []
    return len(items)


def count_working_orders() -> int:
    url = f"{BASE_URL}/api/v1/workingorders"
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return 0
    data = r.json() or {}
    items = data.get("workingOrders") or data.get("items") or data.get("results") or []
    return len(items)


def count_active_total() -> int:
    return count_positions() + count_working_orders()


def read_orders_and_execute(path="orders.csv"):
    # ---- MJUKT TAK: stoppa om vi redan är vid eller över MAX_TRADES ----
    active_now = count_active_total()
    if active_now >= MAX_TRADES:
        print(
            f"[STOP] Redan {active_now} aktiva (open+pending) ≥ MAX_TRADES({MAX_TRADES}). Inga ordrar läggs."
        )
        return

    orders = read_orders_csv(path)
    if not orders:
        print("[INFO] Inga orderrader i orders.csv.")
        return

    # Begränsa antal nya ordrar till lediga slots
    slots = MAX_TRADES - active_now
    if slots < len(orders):
        print(
            f"[CAP] Har {active_now} aktiva. Tillåter endast {slots} nya av {len(orders)} orderrader."
        )
        orders = orders[:slots]
    else:
        print(f"[INFO] Skickar {len(orders)} pending BUY STOP-orders (DEMO)...")

    for row in orders:
        try:
            epic = row["EPIC"].strip()
            entry = float(row["ENTRY"])
            sl = float(row.get("SL", 0) or 0)
            tp = float(row.get("TP", 0) or 0)

            if FORCE_PERCENT_SLTP or sl == 0 or tp == 0:
                sl, tp = compute_levels_from_pct(entry, "BUY")

            lev_hint = None
            try:
                lv_raw = row.get("LEVERAGE")
                lev_hint = float(lv_raw) if (lv_raw not in (None, "")) else None
            except Exception:
                lev_hint = None

            size = calc_size(epic, entry, sl, leverage_hint=lev_hint)
            size = round(size, 2)

            deal_ref, deal_id, status = place_pending_buy_stop(
                epic, entry, sl, tp, size
            )
            append_open_trades_log(epic, entry, sl, tp, size, deal_ref, deal_id, status)

            time.sleep(0.7)
        except Exception as e:
            print(f"[FEL] Rad problem ({row}): {e}")

    print("\n✅ KLART – Pending orders lagda & loggade i open_trades.csv\n")


if __name__ == "__main__":
    read_orders_and_execute("orders.csv")

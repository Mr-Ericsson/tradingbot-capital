# capital_scan_tradeable.py
import os, csv, sys, time
from datetime import datetime, UTC
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.brokers.capitalcom.session import get_session

# ---- Capital session ----
session_data = get_session()
headers = {
    "X-CAP-API-KEY": os.getenv("API_KEY"),
    "CST": session_data["CST"],
    "X-SECURITY-TOKEN": session_data["X-SECURITY-TOKEN"],
}
BASE = os.getenv("BASE_URL")

# ---- Config ----
DEFAULT_MAX_SPREAD_FRAC = 0.003  # 0.3% spread (ändrat från 0.2%)
DEFAULT_ALLOWED_TYPES = {
    "SHARES",
    "INDICES",
    "COMMODITIES",
    "ETF",
    "ETFS",
    "CURRENCIES",
    "CRYPTOCURRENCIES",
}

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
POOL_SIZE = 32
SCAN_OUTPUT_PATH = "data/scan/all_instruments_capital.csv"  # Ändrat för att matcha EDGE-10 pipeline

REJECT_MODES = {"SUSPENDED", "AUCTION", "VIEW_ONLY"}


# ---- Helpers ----
def _deep_num(d, keys):
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, dict):
            x = _deep_num(v, keys)
            if x is not None:
                return x
    return None


def _deep_str(d, keys):
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            x = _deep_str(v, keys)
            if x:
                return x
    return None


def fetch_leverage(epic):
    try:
        r = requests.get(f"{BASE}/api/v1/markets/{epic}", headers=headers, timeout=10)
        data = r.json() if r.status_code == 200 else {}
    except:
        return ("", "", "", "")
    lev = _deep_num(data, ("leverage",))
    mfl = _deep_num(data, ("marginFactorLong",)) or _deep_num(data, ("marginFactor",))
    mfs = _deep_num(data, ("marginFactorShort",))
    mfu = (_deep_str(data, ("marginFactorUnit",)) or "").upper()
    if (not lev or lev <= 0) and mfl and mfu in ("", "PERCENT", "PERCENTAGE"):
        try:
            lev = 100.0 / float(mfl)
        except:
            lev = None
    return (
        round(lev, 2) if lev else "",
        round(mfl, 2) if mfl else "",
        round(mfs, 2) if mfs else "",
        mfu or "",
    )


def fetch_all_markets():
    url = f"{BASE}/api/v1/markets"
    r = requests.get(url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    r.raise_for_status()
    js = r.json() or {}
    return js if isinstance(js, list) else js.get("markets", [])


def status_summary(markets):
    d = {}
    for m in markets:
        s = (m.get("marketStatus") or "").upper()
        d[s] = d.get(s, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: kv[0]))


def is_us_stock_epic(epic, instrument_type):
    """Identifiera US-aktier baserat på EPIC format (samma logik som fetch_all_instruments.py)"""
    if instrument_type.upper() != "SHARES":
        return False
    epic = str(epic or "").upper()
    
    # Blocka kända ETF:er (samma lista som i EDGE-10 pipeline)
    blocked_etf_tickers = {
        "QQQ", "SPY", "IVV", "VTI", "TQQQ", "SQQQ", "QLD", "QID", 
        "XLF", "XLE", "XLI", "XLK", "XLY", "XLP", "XLU", "XLV", "XLB",
        "VOO", "GLD", "SMH", "SOXX", "VGT", "IWM", "EFA", "EEM"
    }
    if epic in blocked_etf_tickers:
        return False
    
    # US aktier har vanligen korta alfanumeriska koder utan punkter
    return len(epic) <= 5 and epic.isalpha() and "." not in epic


def row_from_market(m, max_spread_frac, allowed_types):
    status = (m.get("marketStatus") or "").upper()
    modes = {str(x).upper() for x in (m.get("marketModes") or [])}

    # kräv öppet för handel och filtrera bort close-only/suspenderad
    if status != "TRADEABLE":
        return None

    REJECT_MODES = {
        "CLOSE_ONLY",
        "CLOSING_ONLY",
        "EDITS_ONLY",
        "SUSPENDED",
        "DISABLED",
        "HALTED",
    }
    if any(x in modes for x in REJECT_MODES):
        return None

    if status != "TRADEABLE":
        return None
    modes = m.get("marketModes") or []
    if any((str(x).upper() in REJECT_MODES) for x in modes):
        return None
    itype = (m.get("instrumentType") or "").upper()
    if allowed_types is not None and itype not in allowed_types:
        return None
    
    # US-aktie filter: bara aktier som ser ut som US-aktier
    epic = m.get("epic", "")
    if itype == "SHARES" and not is_us_stock_epic(epic, itype):
        return None

    bid, offer = m.get("bid"), m.get("offer")
    if not bid or not offer:
        return None
    bid = float(bid)
    offer = float(offer)
    mid = (offer + bid) / 2.0
    spread = abs(offer - bid)
    if mid <= 0:
        return None
    spread_pct_val = (spread / mid) * 100
    if (spread / mid) > max_spread_frac:
        return None
    
    # Beräkna spread_quality (samma logik som fetch_all_instruments.py)
    if spread_pct_val <= 0:
        spread_quality = "no_spread"
    elif spread_pct_val <= 0.1:
        spread_quality = "excellent"
    elif spread_pct_val <= 0.3:
        spread_quality = "good"
    elif spread_pct_val <= 1.0:
        spread_quality = "fair"
    else:
        spread_quality = "poor"
    
    # Asset class mapping
    asset_class_map = {
        "SHARES": "stock",
        "CURRENCIES": "forex", 
        "INDICES": "index",
        "COMMODITIES": "commodity",
        "CRYPTOCURRENCIES": "crypto"
    }
    asset_class = asset_class_map.get(itype, itype.lower())
    
    # Market status
    market_status = (m.get("marketStatus") or "").upper()
    is_tradeable = market_status == "TRADEABLE"
    
    # US stock determination - eftersom vi redan filtrerat till US-aktier
    is_us_stock_val = True  # Alla SHARES som kommer hit är US-aktier
    
    # Hämta timestamp
    from datetime import datetime
    timestamp = datetime.utcnow().isoformat() + "+00:00"
    
    return {
        # EDGE-10 förväntade kolumner (samma som fetch_all_instruments.py)
        "timestamp": timestamp,
        "epic": epic,
        "name": (m.get("name") or m.get("instrumentName") or ""),
        "market_id": m.get("marketId", ""),
        "type": itype,
        "category": "US stocks" if itype == "SHARES" else "",  # Kategori för US-aktier
        "sector": "",    # Placeholder
        "country": "US" if itype == "SHARES" else "",   # US för aktier  
        "base_currency": m.get("currency", "USD"),
        "market_status": market_status,
        "bid": bid,
        "ask": offer,  # Capital.com kallar det "offer" men CSV använder "ask"
        "spread_pct": round(spread_pct_val, 6),
        "min_deal_size": m.get("dealingRules", {}).get("minStepDistance", {}).get("value", 1.0) if isinstance(m.get("dealingRules"), dict) else 1.0,
        "max_deal_size": 0,  # Placeholder
        "open_time": "",     # Placeholder
        "close_time": "",    # Placeholder  
        "percentage_change": m.get("percentageChange", 0),
        "asset_class": asset_class,
        "is_tradeable": is_tradeable,
        "is_us_stock": is_us_stock_val,  # KRITISK kolumn för EDGE-10
        "spread_quality": spread_quality,
    }


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---- MAIN ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--no-spread",
        action="store_true",
        help="Skippa spreadfilter: returnera alla TRADEABLE",
    )

    ap.add_argument("--spread", type=float, default=DEFAULT_MAX_SPREAD_FRAC)  # Använd konfigurerad default
    ap.add_argument(
        "--types",
        type=str,
        default="SHARES",  # Endast SHARES för US-aktier
    )

    ap.add_argument("--outfile", type=str, default=SCAN_OUTPUT_PATH)
    from datetime import datetime

    args = ap.parse_args()

    if args.no_spread:
        args.spread = 999.0
        print("[filter] NO-SPREAD-läge aktivt – ignorerar spreadfilter")

    # Normalisera typer (FX → CURRENCIES, FOREX → CURRENCIES)
    # Typ-normalisering + ALL-stöd
    raw_types = {t.strip().upper() for t in (args.types or "").split(",") if t.strip()}
    alias = {"FX": "CURRENCIES", "FOREX": "CURRENCIES", "ETF": "ETFS"}
    norm_types = {alias.get(t, t) for t in raw_types}
    use_all_types = (not norm_types) or ("ALL" in norm_types)
    allowed_types = None if use_all_types else norm_types
    
    t0 = time.time()  # Flytta t0 hit så den alltid sätts
    if datetime.utcnow().weekday() >= 5:  # lör/sön
        norm_types.add("CRYPTOCURRENCIES")

    print("[fetch] Hämtar alla marknader...")
    markets = fetch_all_markets()
    print(f"[fetch] totalt {len(markets)} instrument")

    ss = status_summary(markets)
    print("[status]")
    for k, v in ss.items():
        print(f"  {k}: {v}")

    rows = []
    for m in markets:
        # Efter
        r = row_from_market(m, args.spread, allowed_types)

        if r:
            rows.append(r)

    filter_description = "US-aktier" if "SHARES" in str(allowed_types) else "instrument"
    print(
        f"[filter] {len(rows)} {filter_description} <= {args.spread*100:.3f}% spread"
    )

    # Skippa leverage-berikandet för kompatibilitet med EDGE-10 format
    enriched = rows

    write_csv(
        args.outfile,
        [
            # EDGE-10 förväntade kolumner (samma som fetch_all_instruments.py)
            "timestamp",
            "epic", 
            "name",
            "market_id",
            "type",
            "category",
            "sector", 
            "country",
            "base_currency",
            "market_status",
            "bid",
            "ask",
            "spread_pct",
            "min_deal_size",
            "max_deal_size",
            "open_time",
            "close_time", 
            "percentage_change",
            "asset_class",
            "is_tradeable", 
            "is_us_stock",  # KRITISK för EDGE-10
            "spread_quality",
        ],
        enriched,
    )

    print(f"[out] {args.outfile}")
    dt = time.time() - t0
    print(f"[SCAN] klart: {len(rows)} träffar <= {args.spread*100:.3f}% på {dt:.1f}s")
    print(
        "\nepic".ljust(12),
        "bid".rjust(12),
        "offer".rjust(12),
        "spread".rjust(10),
        "spr%".rjust(8),
        "name",
    )
    for r in sorted(enriched, key=lambda x: x.get("spread_pct", float("inf")))[:20]:
        epic = r.get("epic", "")
        bid = float(r.get("bid", 0) or 0)
        offer = float(r.get("offer", 0) or 0)
        spread = float(r.get("spread", 0) or 0)
        spr_pct = float(r.get("spread_pct", 0) or 0)
        name = (r.get("name") or r.get("instrumentName") or "")[:40]
        print(
            f"{epic:<12} {bid:>12.6f} {offer:>12.6f} {spread:>10.6f} {spr_pct:>7.3f}% {name}"
        )


if __name__ == "__main__":
    main()

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
DEFAULT_MAX_SPREAD_FRAC = 0.002
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
SCAN_OUTPUT_PATH = "data/scan/scan_tradeable_current.csv"

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

    bid, offer = m.get("bid"), m.get("offer")
    if not bid or not offer:
        return None
    bid = float(bid)
    offer = float(offer)
    mid = (offer + bid) / 2.0
    spread = abs(offer - bid)
    if mid <= 0:
        return None
    if (spread / mid) > max_spread_frac:
        return None
    return {
        "epic": m.get("epic"),
        "bid": bid,
        "offer": offer,
        "spread": round(spread, 6),
        "spread_pct": round((spread / mid) * 100, 4),
        "name": (m.get("name") or m.get("instrumentName") or ""),
        "type": itype,
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

    ap.add_argument("--spread", type=float, default=0.005)
    ap.add_argument(
        "--types",
        type=str,
        default="CURRENCIES,INDICES,COMMODITIES,SHARES,CRYPTOCURRENCIES",
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
    if datetime.utcnow().weekday() >= 5:  # lör/sön
        norm_types.add("CRYPTOCURRENCIES")
        t0 = time.time()

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

    print(
        f"[filter] {len(rows)} träffar <= {args.spread*100:.3f}% spread - berikar leverage..."
    )

    enriched = []
    with ThreadPoolExecutor(max_workers=POOL_SIZE) as exe:
        futures = {exe.submit(fetch_leverage, r["epic"]): r for r in rows}
        for fut in as_completed(futures):
            r = futures[fut]
            lev, mfl, mfs, mfu = fut.result()
            (
                r["leverage"],
                r["marginFactorLong"],
                r["marginFactorShort"],
                r["marginUnit"],
            ) = (lev, mfl, mfs, mfu)
            enriched.append(r)

    write_csv(
        args.outfile,
        [
            "epic",
            "bid",
            "offer",
            "spread",
            "spread_pct",
            "name",
            "type",
            "leverage",
            "marginFactorLong",
            "marginFactorShort",
            "marginUnit",
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

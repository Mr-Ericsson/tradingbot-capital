#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
analyze_edge_score.py
    csv_path = CSV_PATH
- Output: orders.csv  (EPIC, ENTRY, SL, TP, LEVERAGE) – används av place_pending_orders.py

Logik:
- För varje EPIC: hämta marketinfo -> offer (köppris)
- ENTRY = offer * (1 + ENTRY_BUFFER_PCT)           # BUY STOP lite ovanför nuvarande pris
- SL     = ENTRY * (1 - SL_PCT)                    # default 3 %
- TP     = ENTRY * (1 + TP_PCT)                    # default 3 %
- LEVERAGE hämtas från top10-filen (om finns), annars från marketinfo (leverage eller 100/marginFactorLong)
"""

import os, csv, sys, json, time
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "top10", "top10_momentum_current.csv")
import os
from typing import Optional, Dict, Any
import os
from datetime import datetime, timezone as tz
import os

import argparse
import os
import requests
import os
from pathlib import Path
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "top10", "top10_momentum_current.csv")


# === Standardiserad session ===
from src.brokers.capitalcom.session import get_session
import os

ROOT = Path(__file__).resolve().parent

# === Konfig (behållna env-nycklar & defaults) ===
BASE_URL = os.getenv("BASE_URL") or os.getenv(
    "CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com"
)

csv_path = CSV_PATH
OUTPUT_ORDERS = os.getenv("ORDERS_FILE") or str(ROOT / "orders.csv")

# Entry/SL/TP-parametrar
ENTRY_BUFFER_PCT = float(os.getenv("ENTRY_BUFFER_PCT", "0.002"))  # 0.2% ovanför offer
SL_PCT = float(os.getenv("SL_PCT", "0.03"))  # 3% under entry
TP_PCT = float(os.getenv("TP_PCT", "0.03"))  # 3% över entry
# === Edge/Decision-parametrar ===
BUY_SCORE_MIN = 70.0  # BUY om score >= detta
DECISION_RESOLUTION = os.getenv(
    "DECISION_RESOLUTION", "MINUTE"
)  # candles för MA-beräkning
DECISION_CANDLES = int(os.getenv("DECISION_CANDLES", "300"))  # minst 200 för MA200

# Nätetikett
SLEEP_BETWEEN = float(
    os.getenv("MARKET_RATE_SLEEP", "0.10")
)  # liten paus mellan market-anrop

# Global requests-session (utan headers — vi skickar headers per anrop)
session = requests.Session()


def _num(x) -> Optional[float]:
    try:
        return float(str(x).strip().replace(",", "."))
    except Exception:
        return None


def deep_get_num(d: Dict[str, Any], keys: tuple) -> Optional[float]:
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, dict):
            x = deep_get_num(v, keys)
            if x is not None:
                return x
    return None


def deep_get_str(d: Dict[str, Any], keys: tuple) -> Optional[str]:
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k in keys and isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            x = deep_get_str(v, keys)
            if x:
                return x
    return None


def fetch_market(epic: str, headers: Dict[str, str]) -> dict:
    r = session.get(f"{BASE_URL}/api/v1/markets/{epic}", headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"/markets/{epic} status {r.status_code}: {r.text[:200]}")
    return r.json()


def current_offer_from_market(mkt: dict) -> Optional[float]:
    # Försök hitta ett "offer"/"offerPrice"/"buy" från snapshot
    val = (
        deep_get_num(mkt, ("offer",))
        or deep_get_num(mkt, ("offerPrice",))
        or deep_get_num(mkt, ("buy",))
        or deep_get_num(mkt, ("sell",))  # sista utvägen
    )
    return val


def leverage_from_market(mkt: dict) -> Optional[float]:
    lev = deep_get_num(mkt, ("leverage",))
    if lev and lev > 0:
        return float(lev)
    # härled från marginFactorLong i procent om leveragen saknas
    mf_long = deep_get_num(mkt, ("marginFactorLong",)) or deep_get_num(
        mkt, ("marginFactor",)
    )
    unit = (deep_get_str(mkt, ("marginFactorUnit", "marginUnit")) or "").upper()
    if mf_long and (unit in ("", "PERCENT", "PERCENTAGE")):
        try:
            return 100.0 / float(mf_long)
        except Exception:
            return None
    return None


def fetch_prices(
    epic: str,
    headers: Dict[str, str],
    resolution: str = DECISION_RESOLUTION,
    max_points: int = DECISION_CANDLES,
) -> dict:
    r = session.get(
        f"{BASE_URL}/api/v1/prices/{epic}",
        params={"resolution": resolution, "max": max_points},
        headers=headers,
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"/prices/{epic} status {r.status_code}: {r.text[:200]}")
    return r.json()


def extract_closes(prices_json: dict):
    closes = []
    for c in prices_json.get("prices", []) or prices_json.get("candles", []) or []:
        close = (
            deep_get_num(c, ("close", "closePrice", "lastTraded"))
            or deep_get_num(c, ("bid",))
            or deep_get_num(c, ("ask",))
        )
        if close:
            closes.append(float(close))
    return closes


def sma(vals, n):
    if not vals or len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def compute_ma20_ma200(epic: str, headers: Dict[str, str]):
    pj = fetch_prices(epic, headers)
    closes = extract_closes(pj)
    if not closes or len(closes) < 200:
        return None, None
    return sma(closes, 20), sma(closes, 200)


# === IO ===
def load_top10(path: str):
    if not os.path.exists(path):
        print(f"Hittar inte {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)
        rows = []
        for row in rdr:
            # case-insensitive nycklar
            uk = {(k or "").strip().lower(): k for k in row.keys()}

            def getf(key, default=None):
                k = uk.get(key.lower())
                return row.get(k) if k in uk.values() else default

            epic = (getf("epic", "") or "").strip()
            if not epic:
                continue

            def fnum(x):
                try:
                    return float(str(x).replace(",", "."))
                except Exception:
                    return None

            rows.append(
                {
                    "EPIC": epic,
                    "LEVERAGE": fnum(getf("leverage")),
                    "SCORE": fnum(getf("score")),
                    "M5_PCT": fnum(getf("m5_pct")),
                    "VWAP_DIST_PCT": fnum(getf("vwap_dist_pct")),
                    "RANGE_POS_PCT": fnum(getf("range_pos_pct")),
                    "RSI14": fnum(getf("rsi14")),
                    "NAME": (getf("name") or "").strip(),
                }
            )
        return rows


def write_orders(path: str, rows):
    fields = ["EPIC", "ENTRY", "SL", "TP", "LEVERAGE"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# === Beslutslogik ===
def decide_buy(
    score: Optional[float],
    m5_pct: Optional[float],
    vwap_dist_pct: Optional[float],
    last_price: Optional[float],
    ma200: Optional[float],
) -> str:
    """
    Returnerar 'BUY', 'WATCH' eller 'SKIP' enligt överenskommen regel:
    BUY  om score >= 70 och (pris > MA200) och (M5% > 0) och (VWAP >= 0)
    WATCH om score i [55, 69] eller borderline
    SKIP annars
    """
    try:
        sc = float(score) if score is not None else None
        m5 = float(m5_pct) if m5_pct is not None else None
        vwap = float(vwap_dist_pct) if vwap_dist_pct is not None else None
        px = float(last_price) if last_price is not None else None
        ma = float(ma200) if ma200 is not None else None
    except Exception:
        return "SKIP"

    if (
        sc is not None
        and sc >= BUY_SCORE_MIN
        and px is not None
        and ma is not None
        and m5 is not None
        and vwap is not None
    ):
        if (px > ma) and (m5 > 0.0) and (vwap >= 0.0):
            return "BUY"

    if sc is not None and 55.0 <= sc < BUY_SCORE_MIN:
        return "WATCH"
    return "SKIP"


def build_orders(top_rows, headers: Dict[str, str]):
    out = []
    print("\nEPIC          DEC   SCORE   M5%    RNG%    VWAP%    ATR?   NAME")
    print("-" * 70)
    for i, r in enumerate(top_rows, start=1):
        epic = r["EPIC"]
        lev_hint = r.get("LEVERAGE")
        score = r.get("SCORE")
        m5_pct = r.get("M5_PCT")
        vwap_pct = r.get("VWAP_DIST_PCT")
        rng_pct = r.get("RANGE_POS_PCT")
        name = r.get("NAME", "")

        try:
            mkt = fetch_market(epic, headers)
        except Exception as e:
            print(f"{epic:<12} SKIP  --    (marketinfo fel: {str(e)[:28]}...)")
            continue

        offer = current_offer_from_market(mkt)
        if not offer:
            print(f"{epic:<12} SKIP  --    (saknar offer)")
            continue

        # Hämta MA20/MA200 för trend-beslut
        try:
            ma20, ma200 = compute_ma20_ma200(epic, headers)
        except Exception:
            ma20, ma200 = None, None

        # Beslut
        dec = decide_buy(score, m5_pct, vwap_pct, offer, ma200)

        # Snygg utskrift för översikt
        def fmt(x, w=7):
            try:
                return f"{float(x):>{w}.3f}"
            except:
                return f"{'-':>{w}}"

        print(
            f"{epic:<12} {dec:<5} {fmt(score)} {fmt(m5_pct)} {fmt(rng_pct)} {fmt(vwap_pct)} {fmt(None)}  {name[:24]}"
        )

        if dec != "BUY":
            time.sleep(SLEEP_BETWEEN)
            continue

        # ENTRY/SL/TP
        entry = offer * (1.0 + ENTRY_BUFFER_PCT)
        sl = entry * (1.0 - SL_PCT)
        tp = entry * (1.0 + TP_PCT)

        # LEVERAGE – från CSV om finns; annars från market
        lev = (
            lev_hint
            if (isinstance(lev_hint, (int, float)) and lev_hint > 0)
            else leverage_from_market(mkt)
        )

        out.append(
            {
                "EPIC": epic,
                "ENTRY": f"{entry:.4f}",
                "SL": f"{sl:.4f}",
                "TP": f"{tp:.4f}",
                "LEVERAGE": (
                    f"{lev:.2f}" if (isinstance(lev, (int, float)) and lev > 0) else ""
                ),
            }
        )

        time.sleep(SLEEP_BETWEEN)

    return out


def main():
    global ENTRY_BUFFER_PCT, SL_PCT, TP_PCT

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        default=CSV_PATH,
    )
    ap.add_argument(
        "--output", default=OUTPUT_ORDERS, help="orders CSV (default: orders.csv)"
    )
    ap.add_argument(
        "--entry-buffer",
        type=float,
        default=ENTRY_BUFFER_PCT,
        help="ENTRY-buffer i procent (0.002 = 0.2%%)",
    )
    ap.add_argument(
        "--sl-pct",
        type=float,
        default=SL_PCT,
        help="SL i procent av ENTRY (0.03 = 3%%)",
    )
    ap.add_argument(
        "--tp-pct",
        type=float,
        default=TP_PCT,
        help="TP i procent av ENTRY (0.03 = 3%%)",
    )
    args = ap.parse_args()

    # uppdatera ev overrides från CLI
    ENTRY_BUFFER_PCT = float(args.entry_buffer)
    SL_PCT = float(args.sl_pct)
    TP_PCT = float(args.tp_pct)

    # Standardiserad login via capital_session
    print("[session] hämtar Capital.com-session ...")
    sess = get_session()
    headers = {
        "X-CAP-API-KEY": os.getenv("API_KEY"),
        "CST": sess["CST"],
        "X-SECURITY-TOKEN": sess["X-SECURITY-TOKEN"],
        "Accept": "application/json",
    }
    print("[session] klar ✅")

    top_rows = load_top10(args.input)
    print(f"[load] top-kandidater: {len(top_rows)}")

    orders = build_orders(top_rows, headers)
    buy_count = len(orders)
    print(f"[edge] BUY-kandidater: {buy_count} av {len(top_rows)}")

    if not orders:
        print("[out] inga orderrader producerades.")
        sys.exit(0)

    write_orders(args.output, orders)
    print(f"[out] {args.output} ({len(orders)} rader)")
    print("✅ klart")


if __name__ == "__main__":
    main()

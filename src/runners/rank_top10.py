# rank_top10_momentum.py  (drop-in v1.0)
# Proffs-variant: hårda förfilter + edge-score + sektortak + tydlig output.
# Kräver .env med BASE_URL, API_KEY, LOGIN, PASSWORD (Capital.com DEMO/real).

import os, math, time, argparse, sys
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# ---------- Tunables (default) ----------
DEFAULT_RESOLUTION = "MINUTE"  # MINUTE, MINUTE_5, MINUTE_15, HOUR, etc.
DEFAULT_CANDLES = 300  # hur många candles vi hämtar per instrument
RS_MIN_DEFAULT = 0.0  # hårt filter: relativ styrka >= 0
SPIKE_MIN_DEFAULT = 1.2  # hårt filter: ATR-spike >= 1.2
MOM_MIN_DEFAULT = 0.0  # hårt filter: momentum >= 0
SPREAD_WEIGHT_DEFAULT = 50.0  # penalty: spread_pct * weight
SECTOR_CAP_DEFAULT = 3  # max antal från samma sektor/market_type
MOM_SHORT = 30  # minuter för momentum-kärna
MOM_LONG = 60  # om tillräckligt data finns

# ---------- Helpers ----------


@dataclass
class CapitalSession:
    api_key: str
    cst: str
    xst: str
    base_url: str


def load_env():
    load_dotenv()
    base_url = os.getenv("BASE_URL", "https://demo-api-capital.backend-capital.com")
    api_key = os.getenv("API_KEY")
    login = os.getenv("LOGIN") or os.getenv("LOGIN_ID")  # <- stöder båda
    password = os.getenv("PASSWORD")
    if not api_key or not login or not password:
        print(
            "[ERR] .env saknar API_KEY/LOGIN (eller LOGIN_ID)/PASSWORD", file=sys.stderr
        )
        sys.exit(1)
    return base_url, api_key, login, password


def login_capital() -> CapitalSession:
    base_url, api_key, login, password = load_env()
    url = f"{base_url}/api/v1/session"
    headers = {
        "X-CAP-API-KEY": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "VERSION": "3",
    }
    payload = {"identifier": login, "password": password}
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    if r.status_code >= 300:
        print(f"[ERR] login failed {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    cst = r.headers.get("CST")
    xst = r.headers.get("X-SECURITY-TOKEN")
    if not cst or not xst:
        print("[ERR] missing CST/X-SECURITY-TOKEN in login response", file=sys.stderr)
        sys.exit(1)
    return CapitalSession(api_key=api_key, cst=cst, xst=xst, base_url=base_url)


def session_headers(s: CapitalSession) -> Dict[str, str]:
    return {
        "X-CAP-API-KEY": s.api_key,
        "CST": s.cst,
        "X-SECURITY-TOKEN": s.xst,
        "Accept": "application/json",
    }


def fetch_prices(
    s: CapitalSession, epic: str, resolution: str, max_points: int
) -> pd.DataFrame:
    """
    GET /api/v1/prices/{epic}?resolution=MINUTE&max=300
    Returns DataFrame med kolumner: time, open, high, low, close, volume
    """
    url = f"{s.base_url}/api/v1/prices/{epic}"
    params = {"resolution": resolution, "max": max_points}
    r = requests.get(url, headers=session_headers(s), params=params, timeout=25)
    if r.status_code >= 300:
        raise RuntimeError(f"prices fail {epic} {r.status_code}: {r.text}")
    data = r.json()
    prices = data.get("prices") or []
    if not prices:
        raise RuntimeError(f"no prices {epic}")
    rows = []
    for p in prices:
        # Capital.com payload brukar ha closePrice/bid/ask; ta medel av bid/ask för OHLC.
        def mid(x):
            if not isinstance(x, dict):
                return np.nan
            b = x.get("bid")
            a = x.get("ask")
            if b is None or a is None:
                return np.nan
            return (float(b) + float(a)) / 2.0

        row = {
            "time": p.get("snapshotTimeUTC") or p.get("snapshotTime"),
            "open": mid(p.get("openPrice", {})),
            "high": mid(p.get("highPrice", {})),
            "low": mid(p.get("lowPrice", {})),
            "close": mid(p.get("closePrice", {})),
            "volume": float(p.get("lastTradedVolume") or 0.0),
        }
        rows.append(row)
    df = pd.DataFrame(rows).dropna(subset=["close"]).reset_index(drop=True)
    return df


def pct_change(a: float, b: float) -> float:
    # ((a - b) / b) * 100
    if b == 0 or b is None or np.isnan(b):
        return 0.0
    return (a - b) / b * 100.0


def compute_atr_spike(df: pd.DataFrame, window: int = 20) -> float:
    if len(df) < window + 1:
        return 1.0
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1)),
        ),
    )
    atr = tr.rolling(window=window).mean()
    cur_tr = tr.iloc[-1]
    base = atr.iloc[-1]
    if base is None or np.isnan(base) or base == 0:
        return 1.0
    return float(cur_tr / base)


def compute_momentum(df: pd.DataFrame, minutes: int) -> float:
    # antar 1 candle = 1 minut i MINUTE-resolution
    if len(df) < minutes + 1:
        return 0.0
    last = df["close"].iloc[-1]
    prev = df["close"].iloc[-(minutes + 1)]
    return pct_change(last, prev)


def compute_vwap_dist_pct(df: pd.DataFrame, window: int = 200) -> float:
    """
    vwap_dist_pct = (last_close / vwap - 1) * 100
    Använder senaste 'window' rader. Om volymer saknas -> returnera 0.0
    """
    if df.empty:
        return 0.0
    use = df.tail(window).copy()
    if "volume" not in use.columns:
        return 0.0
    vol = use["volume"].fillna(0.0).values
    px = use["close"].ffill().values
    vol_sum = float(vol.sum())
    if vol_sum <= 0.0:
        return 0.0
    vwap = float((px * vol).sum() / vol_sum)
    last_close = float(px[-1])
    if vwap <= 0:
        return 0.0
    return (last_close / vwap - 1.0) * 100.0


def compute_edge(
    mom: float, spike: float, rs: float, spread_pct: float, spread_weight: float
) -> float:
    # volymproxy via spike → log(spike) för att inte överdriva extremfall
    spike_comp = max(spike, 1e-6)
    return (
        0.5 * (mom * math.log(spike_comp))
        + 0.3 * rs
        + 0.3 * spike
        - (spread_pct * spread_weight)
    )


def normalize_series(x: pd.Series) -> pd.Series:
    mn, mx = x.min(), x.max()
    if mx - mn < 1e-9:
        return pd.Series([50.0] * len(x), index=x.index)
    return (x - mn) / (mx - mn) * 100.0


# ---------- Main pipeline ----------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-path", default="scan_tradeable_current.csv")
    ap.add_argument("--out-path", default="top10_momentum_current.csv")
    ap.add_argument(
        "--resolution", default=os.getenv("DECISION_RESOLUTION", DEFAULT_RESOLUTION)
    )
    ap.add_argument(
        "--candles",
        type=int,
        default=int(os.getenv("DECISION_CANDLES", DEFAULT_CANDLES)),
    )

    ap.add_argument(
        "--rs-min", type=float, default=float(os.getenv("RS_MIN", RS_MIN_DEFAULT))
    )
    ap.add_argument(
        "--spike-min",
        type=float,
        default=float(os.getenv("SPIKE_MIN", SPIKE_MIN_DEFAULT)),
    )
    ap.add_argument(
        "--mom-min", type=float, default=float(os.getenv("MOM_MIN", MOM_MIN_DEFAULT))
    )
    ap.add_argument(
        "--spread-weight",
        type=float,
        default=float(os.getenv("SPREAD_WEIGHT", SPREAD_WEIGHT_DEFAULT)),
    )
    ap.add_argument(
        "--sector-cap",
        type=int,
        default=int(os.getenv("SECTOR_CAP", SECTOR_CAP_DEFAULT)),
    )

    args = ap.parse_args()

    # Läs kandidatlista från scannern
    if not os.path.exists(args.scan_path):
        print(f"[ERR] saknas: {args.scan_path}", file=sys.stderr)
        sys.exit(1)

    scan = pd.read_csv(args.scan_path)
    if "epic" not in scan.columns and "EPIC" in scan.columns:
        scan = scan.rename(columns={"EPIC": "epic"})
    # spread kan vara i % (0.15) eller fraktion (0.0015). Detektera:
    if "spread_pct" in scan.columns:
        sp = scan["spread_pct"].astype(float).fillna(0.0)
        # om medel < 1 → tolka som fraktion → konvertera till procent
        if sp.mean() < 0.5:
            scan["spread_pct"] = sp * 100.0
    else:
        scan["spread_pct"] = 0.0

    # Sector/market_type
    sector_col = None
    for cand in ["sector", "marketType", "market_type", "type"]:
        if cand in scan.columns:
            sector_col = cand
            break
    if sector_col is None:
        sector_col = "marketType"
        scan[sector_col] = "UNKNOWN"

    # Login Capital
    sess = login_capital()

    rows: List[Dict[str, Any]] = []
    failures: List[Tuple[str, str]] = []

    for idx, r in scan.iterrows():
        epic = str(r.get("epic") or "")
        if not epic:
            continue
        try:
            df = fetch_prices(
                sess, epic=epic, resolution=args.resolution, max_points=args.candles
            )
            # Basic sanity – senaste candle inte äldre än ~15 min vid MINUTE
            # (vi litar på att df har UTC-strängar; vi använder bara positioner här)
            m30 = compute_momentum(df, MOM_SHORT)
            m60 = compute_momentum(df, MOM_LONG)
            if m60 != 0.0:
                momentum = 0.6 * m30 + 0.4 * m60
            else:
                momentum = m30

            # RS-bas
            m15 = compute_momentum(df, 15)

            # NYTT: M5% (senaste 5 minuter, förutsätter MINUTE-resolution)
            m5_pct = compute_momentum(df, 5)

            spike = compute_atr_spike(df, window=20)
            spread_pct = float(r.get("spread_pct") or 0.0)

            # NYTT: VWAP-distans i procent (senaste ~200 rader)
            vwap_dist_pct = compute_vwap_dist_pct(df, window=200)

            rows.append(
                {
                    "epic": epic,
                    "name": r.get("name") or r.get("instrumentName") or "",
                    "sector": r.get(sector_col) or "UNKNOWN",
                    "spread_pct": spread_pct,
                    "mom30": m30,
                    "mom60": m60,
                    "mom": momentum,
                    "m15": m15,
                    "m5_pct": m5_pct,  # <— NY
                    "vwap_dist_pct": vwap_dist_pct,  # <— NY
                    "spike": spike,
                }
            )

        except Exception as e:
            failures.append((epic, str(e)))
            continue

    data = pd.DataFrame(rows)
    if data.empty:
        print("[ERR] inga kandidater kunde beräknas", file=sys.stderr)
        if failures:
            print("[INFO] failures:", failures[:5], file=sys.stderr)
        sys.exit(1)

    # RS mot benchmark (snitt m15 bland kandidater)
    bench = float(data["m15"].mean())
    data["rs"] = data["m15"] - bench

    # Hårda förfilter
    rs_min = args.rs_min
    spike_min = args.spike_min
    mom_min = args.mom_min

    pre = data.copy()
    mask = (pre["mom"] >= mom_min) & (pre["rs"] >= rs_min) & (pre["spike"] >= spike_min)

    dropped = pre.loc[~mask].copy()
    if not dropped.empty:
        dropped["drop_reason"] = np.where(
            dropped["mom"] < mom_min,
            "MOM",
            np.where(dropped["rs"] < rs_min, "RS", "SPIKE"),
        )

    survivors = pre.loc[mask].copy()

    if survivors.empty:
        # Om allt filtrerades bort – backoff: släpp igenom topp 20 på mom som nödläge
        survivors = (
            pre.sort_values("mom", ascending=False)
            .head(20)
            .drop(columns=["drop_reason"], errors="ignore")
        )
        print(
            "[WARN] alla filtrerades bort; backoff: tar topp 20 på momentum",
            file=sys.stderr,
        )

    # Edge-score
    spread_weight = args.spread_weight
    survivors["edge_raw"] = survivors.apply(
        lambda z: compute_edge(
            z["mom"], z["spike"], z["rs"], z["spread_pct"], spread_weight
        ),
        axis=1,
    )
    survivors["edge"] = normalize_series(survivors["edge_raw"])

    # Sektortak (post-sortering)
    survivors = survivors.sort_values("edge", ascending=False).reset_index(drop=True)
    cap = args.sector_cap
    pick_rows = []
    seen_per_sector: Dict[str, int] = {}
    for _, row in survivors.iterrows():
        sec = str(row["sector"])
        cnt = seen_per_sector.get(sec, 0)
        if cnt >= cap:
            continue
        pick_rows.append(row)
        seen_per_sector[sec] = cnt + 1
        if len(pick_rows) >= 10:
            break
    top10 = pd.DataFrame(pick_rows)

    # NYTT: ge analyze_step_3 ett "score" som speglar edge
    if not top10.empty:
        top10["score"] = top10["edge"]

    # Output CSV
    now_utc = pd.Timestamp.utcnow().replace(tzinfo=None)
    for df_out, path in [(top10, args.out_path)]:
        cols = [
            "epic",
            "name",
            "sector",
            "spread_pct",
            "mom30",
            "mom60",
            "mom",
            "m15",
            "m5_pct",  # <— NY
            "rs",
            "spike",
            "edge",
            "score",  # <— NY
            "vwap_dist_pct",  # <— NY
        ]

        for c in cols:
            if c not in df_out.columns:
                df_out[c] = np.nan
        df_out = df_out[cols].copy()
        df_out.insert(0, "ts_utc", now_utc.isoformat(timespec="seconds"))
        df_out.to_csv(path, index=False)

    # Konsolvisning
    def fmt(x, p=2):
        try:
            return f"{float(x):.{p}f}"
        except:
            return str(x)

    print("\n=== TOP-10 (LONG, continuation) ===")
    if not top10.empty:
        show = top10.copy()
        show["spread_pct"] = show["spread_pct"].map(lambda v: f"{v:.2f}%")
        show["mom"] = show["mom"].map(lambda v: f"{v:.2f}%")
        show["rs"] = show["rs"].map(lambda v: f"{v:+.2f} pp")
        show["spike"] = show["spike"].map(lambda v: f"{v:.2f}x")
        show["edge"] = show["edge"].map(lambda v: f"{v:.1f}")
        print(
            show[
                ["epic", "name", "sector", "spread_pct", "mom", "rs", "spike", "edge"]
            ].to_string(index=False)
        )
    else:
        print("(tom)")

    # Droppade (valfritt: skriv till fil för efteranalys)
    dropped_path = os.path.splitext(args.out_path)[0] + "_dropped.csv"
    if not dropped.empty:
        dropped_out = dropped.copy()
        dropped_out.insert(0, "ts_utc", now_utc.isoformat(timespec="seconds"))
        dropped_out.to_csv(dropped_path, index=False)

    # Failures (logga kort)
    if failures:
        print(
            f"\n[INFO] misslyckade pris-hämtningar: {len(failures)} (visar upp till 10)",
            file=sys.stderr,
        )
        for epic, err in failures[:10]:
            print(f"  - {epic}: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()

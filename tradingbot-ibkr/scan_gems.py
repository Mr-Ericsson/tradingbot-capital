# scan_gems.py — Top-10 long/short kandidater för 3% TP/SL med MA20/MA200 + momentum + nyhetsfilter
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import math

pd.options.mode.chained_assignment = None

# --- Parameterer ---
UNIVERSE_LIMIT = (
    600  # hur många tickers från NASDAQ vi testar (fallback används om API saknas)
)
PRICE_MIN, PRICE_MAX = 5, 200
VOL_MIN = 1_000_000  # snittvolym 20d
MCAP_MIN, MCAP_MAX = 1e9, 5e10  # $1B–$50B (exkludera drakar)
ADR_PCT_MIN = 3.0  # kräver att 3% är rimligt för dagsrörelse
NEWDAYS = 3
TOP_N = 10

NEG_WORDS = [
    "downgrade",
    "offering",
    "lawsuit",
    "sec",
    "recall",
    "probe",
    "fraud",
    "guidance cut",
    "misses",
    "shortfall",
]
POS_WORDS = [
    "upgrade",
    "beats",
    "raises guidance",
    "partnership",
    "contract",
    "wins",
    "record",
    "surge",
]


def adr_pct(close):
    hi_lo = close.rolling(20).apply(
        lambda s: (s.max() - s.min()) / s.iloc[-1] * 100, raw=False
    )
    # bättre: ATR, men denna duger för rimlighetsfilter
    return hi_lo


def slope(series, lookback=5):
    if len(series) < lookback:
        return 0.0
    y = series.tail(lookback).values
    x = np.arange(len(y))
    b, a = np.polyfit(x, y, 1)
    return b  # lutning


def momentum_roc(close, lookback=60):
    if len(close) <= lookback:
        return 0.0
    return (close.iloc[-1] / close.iloc[-lookback] - 1.0) * 100.0


def swing_low(series, lookback=20):
    return series.tail(lookback).min()


def swing_high(series, lookback=20):
    return series.tail(lookback).max()


def news_score(tkr):
    try:
        n = yf.Ticker(tkr).news or []
    except Exception:
        n = []
    cutoff = datetime.utcnow() - timedelta(days=NEWDAYS)
    s = 0
    for it in n:
        # yfinance news ts in seconds
        ts = datetime.utcfromtimestamp(it.get("providerPublishTime", 0))
        if ts < cutoff:
            continue
        title = (it.get("title") or "").lower()
        if any(w in title for w in NEG_WORDS):
            s -= 1
        if any(w in title for w in POS_WORDS):
            s += 1
    return s


def pick_universe():
    # Försök ta NASDAQ-lista; fallback till en handplockad mid/small-cap lista
    try:
        tickers = yf.tickers_nasdaq()[:UNIVERSE_LIMIT]
        return [t for t in tickers if t.isalpha() and len(t) <= 5]
    except Exception:
        return [
            "PLTR",
            "NET",
            "DDOG",
            "ZS",
            "MDB",
            "SEDG",
            "ENPH",
            "ON",
            "WOLF",
            "U",
            "RBLX",
            "ABNB",
            "SHOP",
            "ROKU",
            "AFRM",
            "UAA",
            "DKNG",
            "BILL",
            "OKTA",
            "COIN",
            "SMCI",
            "TWLO",
            "PATH",
            "SNOW",
            "CRWD",
            "TEAM",
            "ESTC",
            "ZS",
            "NEWR",
            "CFLT",
            "FSLR",
            "RUN",
            "NVAX",
            "CLSK",
            "MSTR",
            "CELH",
            "TTD",
            "RIVN",
            "HOOD",
            "ALB",
            "MRNA",
            "PYPL",
            "SQ",
            "PLUG",
            "SMAR",
            "TASK",
        ]


def fetch_one(t):
    try:
        info = yf.Ticker(t).fast_info
        price = info.last_price
        mcap = info.market_cap or 0
        vol = info.last_volume or 0
    except Exception:
        return None
    if not price or price != price:
        return None
    if not (PRICE_MIN <= price <= PRICE_MAX):
        return None
    if not (MCAP_MIN <= mcap <= MCAP_MAX):
        return None

    df = yf.download(t, period="1y", interval="1d", auto_adjust=True, progress=False)
    if df.empty or len(df) < 220:
        return None

    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    df["ATR20"] = (df["High"] - df["Low"]).rolling(20).mean()
    df["ADR%20"] = adr_pct(df["Close"])
    last = df.iloc[-1]

    # volymsnitt
    vol20 = df["Volume"].tail(20).mean()
    if vol20 < VOL_MIN:
        return None
    if last["ADR%20"] < ADR_PCT_MIN:
        return None  # 3% måste vara rimligt

    # trend & avstånd till MA20
    above200 = last["Close"] > last["MA200"]
    below200 = last["Close"] < last["MA200"]
    dist_ma20 = abs(last["Close"] - last["MA20"])
    near_ma20 = dist_ma20 <= 0.5 * last["ATR20"]

    # momentum
    mom60 = momentum_roc(df["Close"], 60)
    ma20_slope = slope(df["MA20"], 5)

    # long/short flag
    is_long = above200 and near_ma20 and (mom60 > 0) and (ma20_slope > 0)
    is_short = below200 and near_ma20 and (mom60 < 0) and (ma20_slope < 0)
    if not (is_long or is_short):
        return None

    # stöd/motstånd
    sup = swing_low(df["Low"], 20)
    res = swing_high(df["High"], 20)

    # nyhets-score
    nscore = news_score(t)

    # enkel score (viktad)
    # närmare MA20 bättre, högre ADR bättre, positivt nyhets-score bättre
    dist_score = 1.0 - min(1.0, dist_ma20 / max(1e-6, last["ATR20"]))
    adr_score = min(1.0, last["ADR%20"] / 6.0)  # 3–6% ger 0.5–1.0
    mom_score = np.tanh(abs(mom60) / 25)
    news_w = 0.1 * nscore

    base = 0.35 * dist_score + 0.35 * adr_score + 0.20 * mom_score + news_w
    side = "LONG" if is_long else "SHORT"

    return {
        "symbol": t,
        "side": side,
        "price": round(float(last["Close"]), 2),
        "ma20": round(float(last["MA20"]), 2),
        "ma200": round(float(last["MA200"]), 2),
        "adr_pct20": round(float(last["ADR%20"]), 2),
        "atr20": round(float(last["ATR20"]), 2),
        "dist_to_ma20": round(float(dist_ma20), 2),
        "mom60_pct": round(float(mom60), 2),
        "ma20_slope": round(float(ma20_slope), 4),
        "support20": round(float(sup), 2),
        "resist20": round(float(res), 2),
        "news_score": int(nscore),
        "score": round(float(base), 3),
    }


def main():
    tickers = pick_universe()
    rows = []
    for i, t in enumerate(tickers, 1):
        try:
            r = fetch_one(t)
            if r:
                rows.append(r)
        except Exception:
            continue

    if not rows:
        print("Inga kandidater hittades. Höj universum eller sänk kraven.")
        return

    df = (
        pd.DataFrame(rows)
        .sort_values(["score", "adr_pct20"], ascending=[False, False])
        .head(TOP_N)
        .reset_index(drop=True)
    )

    # beräkna planerad qty/SL/TP för $100 per trade
    df["entry_hint"] = df[
        "ma20"
    ]  # vi siktar på MA20-återtag; bekräftas 15 min efter öppning
    df["tp"] = (df["entry_hint"] * (1.03)).round(2)
    df["sl"] = (df["entry_hint"] * (0.97)).round(2)
    # shorts: spegla
    shorts = df["side"] == "SHORT"
    df.loc[shorts, "tp"] = (df.loc[shorts, "entry_hint"] * (0.97)).round(2)
    df.loc[shorts, "sl"] = (df.loc[shorts, "entry_hint"] * (1.03)).round(2)

    df["qty_$100"] = (
        (100.0 / df["entry_hint"]).apply(lambda x: max(1, math.floor(x))).astype(int)
    )

    cols = [
        "symbol",
        "side",
        "price",
        "ma20",
        "ma200",
        "adr_pct20",
        "mom60_pct",
        "dist_to_ma20",
        "entry_hint",
        "tp",
        "sl",
        "qty_$100",
        "news_score",
        "score",
    ]
    df[cols].to_csv("top10.csv", index=False)
    print(df[cols])
    print(
        "\n[OK] top10.csv skapad – köp 15 min efter öppning med bekräftelse enligt side/MA20."
    )
    print(
        "Regel vid 15:45: LONG om pris>MA20 på 5–15m och tar ut high; SHORT om pris<MA20 och tar ut low."
    )


if __name__ == "__main__":
    main()

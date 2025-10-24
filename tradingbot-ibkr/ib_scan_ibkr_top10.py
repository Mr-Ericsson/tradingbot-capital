# ib_scan_ibkr_top10.py
from ib_insync import *
import pandas as pd, numpy as np, math, time

IB_PORT = 7497  # Paper

# --- många scanners för stort universum ---
SCANS = [
    # USA aktier
    ("STK", "STK.US.MAJOR", "TOP_VOLUME"),
    ("STK", "STK.US.MAJOR", "HOT_BY_VOLUME"),
    ("STK", "STK.US.MAJOR", "TOP_GAINERS"),
    ("STK", "STK.US.MAJOR", "TOP_LOSERS"),
    ("STK", "STK.US.MAJOR", "HOT_BY_PRICE"),
    # Europa
    ("STK", "STK.EU.MAJOR", "TOP_VOLUME"),
    ("STK", "STK.EU.MAJOR", "HOT_BY_VOLUME"),
    # Asien
    ("STK", "STK.ASIA.MAJOR", "TOP_VOLUME"),
    ("STK", "STK.ASIA.MAJOR", "HOT_BY_VOLUME"),
    # FX & Futures (om du vill ha med dem i samma körning)
    ("FX", "FX.GLOB.MAJOR", "TOP_VOLUME"),
    ("FUT", "FUT.US.MAJOR", "TOP_VOLUME"),
]

TOP_N = 10
ADR_MIN = 3.0
PRICE_MIN, PRICE_MAX = 5, 200  # för aktier; FX/FUT filtreras annorlunda om du vill


def adr_pct(df):
    hi = df["high"].rolling(20).max()
    lo = df["low"].rolling(20).min()
    return (hi - lo) / df["close"] * 100


def slope(s, n=5):
    if len(s) < n:
        return 0.0
    y = s.tail(n).values
    x = np.arange(len(y))
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def histDF(ib, con):
    bars = ib.reqHistoricalData(
        con,
        endDateTime="",
        durationStr="1 Y",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
    )
    return util.df(bars)


def is_stock(con):
    return con.secType == "STK"


def is_fx(con):
    return con.secType == "CASH" or isinstance(con, Forex)


def is_fut(con):
    return con.secType == "FUT"


def evaluate(ib, con):
    try:
        df = histDF(ib, con)
        if df.empty or len(df) < 220:
            return None
        df["ma20"] = df["close"].rolling(20).mean()
        df["ma200"] = df["close"].rolling(200).mean()
        df["atr20"] = (df["high"] - df["low"]).rolling(20).mean()
        df["adr%"] = adr_pct(df)
        last = df.iloc[-1]
    except Exception:
        return None

    # Baskrav
    if is_stock(con):
        price = float(last["close"])
        if not (PRICE_MIN <= price <= PRICE_MAX):
            return None
    # (lägg ev. filter för FX/FUT om du vill)

    if float(last["adr%"]) < ADR_MIN:  # 3% måste vara möjligt
        return None

    dist = abs(last["close"] - last["ma20"])
    near_ma20 = dist <= 0.5 * float(df["atr20"].iloc[-1])

    # momentum
    if len(df) >= 60:
        mom60 = (df["close"].iloc[-1] / df["close"].iloc[-60] - 1) * 100
    else:
        mom60 = 0.0
    ma20_s = slope(df["ma20"], 5)

    above200 = last["close"] > last["ma200"]
    below200 = last["close"] < last["ma200"]

    go_long = above200 and near_ma20 and (mom60 > 0) and (ma20_s > 0)
    go_short = below200 and near_ma20 and (mom60 < 0) and (ma20_s < 0)
    if not (go_long or go_short):
        return None

    side = "LONG" if go_long else "SHORT"
    entry = float(last["ma20"])
    tp = entry * (1.03 if side == "LONG" else 0.97)
    sl = entry * (0.97 if side == "LONG" else 1.03)
    qty100 = max(1, int(math.floor(100 / entry))) if entry > 0 else 1

    score = (
        0.35 * (1 - min(1, dist / max(1e-6, float(df["atr20"].iloc[-1]))))
        + 0.35 * min(1, float(last["adr%"]) / 6.0)
        + 0.30 * np.tanh(abs(mom60) / 25.0)
    )

    sym = con.localSymbol or con.symbol
    exch = getattr(con, "primaryExchange", "") or con.exchange
    return {
        "symbol": sym,
        "secType": con.secType,
        "exch": exch,
        "side": side,
        "price": round(float(last["close"]), 4),
        "ma20": round(float(last["ma20"]), 4),
        "ma200": round(float(last["ma200"]), 4),
        "adr_pct": round(float(last["adr%"]), 2),
        "mom60": round(float(mom60), 2),
        "entry": round(entry, 4),
        "tp": round(tp, 4),
        "sl": round(sl, 4),
        "qty_$100": qty100,
        "score": round(float(score), 3),
    }


def main():
    ib = IB()
    ib.connect("127.0.0.1", IB_PORT, clientId=19)

    seen = set()
    cand = []

    for secType, locationCode, scanCode in SCANS:
        sub = ScannerSubscription(
            instrument=secType, locationCode=locationCode, scanCode=scanCode
        )
        sub.numberOfRows = 100  # maximera rader per scan
        items = ib.reqScannerData(sub, [])
        for it in items:
            con = it.contractDetails.contract
            key = (con.secType, con.symbol, con.exchange, getattr(con, "currency", ""))
            if key in seen:
                continue
            seen.add(key)
            res = evaluate(ib, con)
            if res:
                cand.append(res)
        time.sleep(0.25)

    ib.disconnect()

    if not cand:
        print("Inga kandidater – justera SCANS/filtren.")
        return

    df = (
        pd.DataFrame(cand)
        .sort_values("score", ascending=False)
        .head(TOP_N)
        .reset_index(drop=True)
    )
    cols = [
        "symbol",
        "secType",
        "exch",
        "side",
        "price",
        "ma20",
        "ma200",
        "adr_pct",
        "mom60",
        "entry",
        "tp",
        "sl",
        "qty_$100",
        "score",
    ]
    print(df[cols])
    df[cols].to_csv("top10.csv", index=False)
    print(
        "\n[OK] top10.csv skapad – ta entries ~15 min efter open med MA20-bekräftelse på 5–15m."
    )


if __name__ == "__main__":
    main()

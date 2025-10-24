# pip install ib-insync pandas numpy
from ib_insync import *
import pandas as pd, numpy as np, math, time, string

IB_PORT = 7497
PRICE_MIN, PRICE_MAX = 5, 200  # för aktier
ADR_MIN = 3.0  # kräver att 3% TP/SL är rimligt
TOP_N = 10
QUERY_CHARS = list(string.ascii_uppercase) + list("0123456789")


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


def is_good_stock(d: ContractDescription):
    c = d.contract
    if c.secType != "STK":
        return False
    if c.currency != "USD":
        return False
    # filtrera till riktiga US-venues
    ve = (
        d.derivativeSecTypes or []
    )  # not used; fallback to validExchanges on contractDetails below if needed
    valid_exchs = (d.contract.primaryExchange or "") + "," + (d.contract.exchange or "")
    good = any(x in valid_exchs for x in ["NASDAQ", "NYSE", "ARCA", "AMEX", "SMART"])
    return good


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

    price = float(last["close"])
    if not (PRICE_MIN <= price <= PRICE_MAX):
        return None
    if float(last["adr%"]) < ADR_MIN:
        return None  # måste klara 3%

    dist = abs(last["close"] - last["ma20"])
    near_ma20 = dist <= 0.5 * float(df["atr20"].iloc[-1])

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
    ib.connect("127.0.0.1", IB_PORT, clientId=21)

    # 1) Hämta stor universumlista från IBKR (reqMatchingSymbols på A–Z/0–9)
    cons: list[Contract] = []
    seen = set()
    for q in QUERY_CHARS:
        matches = ib.reqMatchingSymbols(q)
        for m in matches:
            if not is_good_stock(m):
                continue
            c = m.contract
            key = (c.symbol, c.primaryExchange, c.currency)
            if key in seen:
                continue
            seen.add(key)
            # normalisera till SMART/primaryExch för prisdata
            cons.append(
                Stock(
                    symbol=c.symbol,
                    exchange="SMART",
                    currency="USD",
                    primaryExchange=c.primaryExchange or "",
                )
            )
        time.sleep(0.05)  # throttle

    # 2) Ta bort dubbletter
    uniq = []
    ukeys = set()
    for c in cons:
        k = (c.symbol, c.primaryExchange or "", c.currency)
        if k in ukeys:
            continue
        ukeys.add(k)
        uniq.append(c)

    # 3) Analys
    rows = []
    for i, con in enumerate(uniq, 1):
        try:
            ib.qualifyContracts(con)  # får conId etc
            r = evaluate(ib, con)
            if r:
                rows.append(r)
        except Exception:
            pass
        if i % 50 == 0:
            time.sleep(0.2)

    ib.disconnect()

    if not rows:
        print("Inga kandidater hittades (möjl. p.g.a. marknadsdata-begr.).")
        return

    df = (
        pd.DataFrame(rows)
        .sort_values("score", ascending=False)
        .head(TOP_N)
        .reset_index(drop=True)
    )
    cols = [
        "symbol",
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
    print("\n[OK] top10.csv skapad – använd 15-min bekräftelse (MA20) innan köp/sälj.")


if __name__ == "__main__":
    main()

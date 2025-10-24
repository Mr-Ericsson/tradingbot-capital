# scan_top10.py — Top-10 momentum med MA20/MA200 via yfinance
# pip install yfinance pandas numpy
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# En enkel universum (lägg till vad du vill)
UNIVERSE = [
    "AAPL",
    "NVDA",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "SMCI",
    "CRM",
    "ADI",
    "COST",
    "LRCX",
    "PANW",
    "NOW",
    "ABNB",
    "SNOW",
    "INTC",
    "NKE",
    "PEP",
    "JNJ",
    "KO",
    "PFE",
    "GE",
    "PLTR",
    "XOM",
    "JPM",
    "BAC",
    "MU",
    "QCOM",
    "MRVL",
    "SHOP",
    "UBER",
    "ORCL",
    "IBM",
    "DIS",
    "F",
    "GM",
    "RIVN",
    "LCID",
]


def momentum_score(df, lookback=60):
    # enkel total-return * trend-kvalitet
    ret = df["Adj Close"].pct_change().tail(lookback + 1)
    total = (1 + ret).prod() - 1
    # stabilitet via R^2 på pris mot tid
    y = df["Adj Close"].tail(lookback).values
    x = np.arange(len(y))
    coef = np.polyfit(x, y, 1)
    yhat = np.poly1d(coef)(x)
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return float(total) * float(max(r2, 0))


def compute_features(ticker):
    d = yf.download(
        ticker, period="1y", interval="1d", auto_adjust=True, progress=False
    )
    if d.empty or len(d) < 220:
        return None
    d["MA20"] = d["Close"].rolling(20).mean()
    d["MA200"] = d["Close"].rolling(200).mean()
    mom = momentum_score(d, lookback=60)
    last = d.iloc[-1]
    return {
        "symbol": ticker,
        "close": float(last["Close"]),
        "ma20": float(last["MA20"]),
        "ma200": float(last["MA200"]),
        "above_ma20": bool(last["Close"] > last["MA20"]),
        "above_ma200": bool(last["Close"] > last["MA200"]),
        "mom60": mom,
    }


def main():
    rows = []
    for t in UNIVERSE:
        try:
            r = compute_features(t)
            if r:
                rows.append(r)
        except Exception:
            pass
    df = pd.DataFrame(rows)
    df = df.sort_values("mom60", ascending=False).reset_index(drop=True)
    top10 = df.head(10)
    print(
        top10[
            ["symbol", "close", "ma20", "ma200", "above_ma20", "above_ma200", "mom60"]
        ]
    )
    top10.to_csv("top10.csv", index=False)
    print("\n[OK] Sparade Top-10 till top10.csv")


if __name__ == "__main__":
    main()

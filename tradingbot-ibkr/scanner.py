# scanner.py (robust)
from __future__ import annotations
import yfinance as yf
import pandas as pd
import numpy as np


# Vill du ha exakt OHLC utan justering: sätt auto_adjust=False
# (vi kör False så Close inte blir justerad oväntat)
def fetch_history(ticker: str, days: int = 60) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=f"{days}d",
        interval="1d",
        progress=False,
        auto_adjust=False,
    )
    return df.dropna()


def _as_float(x) -> float:
    # Konvertera numpy-scalar/Series/enstaka till py-float
    if hasattr(x, "item"):
        try:
            return float(x.item())
        except Exception:
            pass
    try:
        return float(x)
    except Exception:
        return float("nan")


def momentum_score(df: pd.DataFrame) -> float:
    closes = df["Close"]
    if len(closes) < 25:
        return float("-inf")
    c = closes.to_numpy(dtype="float64", copy=False)
    # 1d, 5d, 20d avkastning
    r1 = _as_float(c[-1] / c[-2] - 1.0)
    r5 = _as_float(c[-1] / c[-6] - 1.0)
    r20 = _as_float(c[-1] / c[-21] - 1.0)
    score = 0.5 * r20 + 0.35 * r5 + 0.15 * r1
    if not np.isfinite(score):
        return float("-inf")
    return float(score)


def pick_top(
    universe: list[str], top_n: int = 10, min_price: float = 5.0, min_vol: int = 200_000
):
    rows: list[tuple[str, float, float]] = []
    for sym in universe:
        try:
            df = fetch_history(sym)
            if df.empty:
                continue
            last = _as_float(df["Close"].iloc[-1])
            vol = _as_float(df["Volume"].tail(10).mean())
            if not (np.isfinite(last) and np.isfinite(vol)):
                continue
            if last < min_price or vol < min_vol:
                continue
            score = momentum_score(df)
            if not np.isfinite(score):
                continue
            rows.append((sym, float(score), float(last)))
        except Exception:
            # Skippa konstiga tickers tyst
            continue

    ranked = sorted(rows, key=lambda x: x[1], reverse=True)
    return ranked[: max(0, top_n)]

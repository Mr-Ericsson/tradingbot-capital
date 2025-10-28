#!/usr/bin/env python3
"""
Debug processing av en enskild ticker
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
import pandas as pd
from datetime import datetime
from universe_run_optimized import process_ticker_fast, calculate_atr, setup_logging


def debug_single_ticker():
    """Debug processing av AAPL"""
    logger = setup_logging()

    # Samma parametrar som skriptet
    ticker = "AAPL"
    market_date = "2025-10-27"
    spread_pct = 0.001  # 0.1%
    name = "Apple Inc"

    # Hämta data
    start_date = (pd.Timestamp(market_date) - pd.Timedelta(days=365)).strftime(
        "%Y-%m-%d"
    )
    end_date = (pd.Timestamp(market_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"Hämtar {ticker} data från {start_date} till {end_date}")
    df_yahoo = yf.download(ticker, start=start_date, end=end_date, progress=False)

    logger.info(f"Data shape: {df_yahoo.shape}")
    logger.info(f"Columns: {df_yahoo.columns.tolist()}")
    logger.info(f"Index range: {df_yahoo.index.min()} to {df_yahoo.index.max()}")

    # Fix MultiIndex manually för debug
    if isinstance(df_yahoo.columns, pd.MultiIndex):
        logger.info("Fixing MultiIndex columns...")
        df_yahoo.columns = df_yahoo.columns.get_level_values(0)
        logger.info(f"Fixed columns: {df_yahoo.columns.tolist()}")

    # Check market date specifically
    market_dt = datetime.strptime(market_date, "%Y-%m-%d")
    today_rows = df_yahoo[df_yahoo.index.date == market_dt.date()]
    logger.info(f"Market date {market_date} rows: {len(today_rows)}")

    if not today_rows.empty:
        logger.info(f"Market date data exists: Close={today_rows.iloc[0]['Close']}")

        # Historical data check
        train_df = df_yahoo[df_yahoo.index.date < market_dt.date()]
        logger.info(f"Historical data: {len(train_df)} rows")

        if len(train_df) >= 30:
            logger.info("Sufficient historical data available")
        else:
            logger.warning(f"Insufficient historical data: {len(train_df)} < 30")

    # Testa processing
    logger.info("Testar process_ticker_fast...")
    try:
        result = process_ticker_fast(ticker, df_yahoo, market_date, spread_pct, name)

        if result:
            logger.info("✅ Processing lyckades!")
            logger.info(f"EdgeScore: {result.get('Score', 'N/A')}")
            logger.info(f"DayReturnPct: {result.get('DayReturnPct', 'N/A')}")
            logger.info(f"A_WINRATE: {result.get('A_WINRATE', 'N/A')}")
        else:
            logger.error("❌ Processing returnerade None")

    except Exception as e:
        logger.error(f"❌ Processing error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    debug_single_ticker()

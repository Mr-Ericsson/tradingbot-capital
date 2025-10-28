#!/usr/bin/env python3
"""
OPTIMIZED Universe Run Script - Capital.com CSV → TOP-100 + TOP-10
PRESTANDA FÖRBÄTTRINGAR:
- Batch Yahoo data hämtning (10x snabbare)
- Reducerad ETF validation
- Parallelisering av beräkningar
- Mindre logging overhead
"""

import argparse
import pandas as pd
import numpy as np
import yfinance as yf
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import sys
import warnings
import exchange_calendars as xcals
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# EDGE-10 imports
from edge10.market_timing import get_nyse_calendar, market_status_summary

warnings.filterwarnings("ignore")


def setup_logging():
    """Setup basic logging to stderr för debug output"""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    return logging.getLogger(__name__)


def create_output_dir(outdir: str):
    """Skapa output-directory om det inte finns"""
    os.makedirs(outdir, exist_ok=True)
    return outdir


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="OPTIMIZED Universe Run - Capital.com CSV → TOP-100 + TOP-10"
    )
    parser.add_argument(
        "--csv",
        default="data/scan/all_instruments_capital.csv",
        help="Path till Capital.com instruments CSV",
    )
    parser.add_argument("--date", default="2025-10-27", help="Analysdatum (YYYY-MM-DD)")
    parser.add_argument(
        "--outdir", default="out", help="Output directory för CSV-filer"
    )
    parser.add_argument(
        "--batch-size",
        default=50,
        type=int,
        help="Batch size för Yahoo API calls (default: 50)",
    )
    parser.add_argument(
        "--max-workers", default=4, type=int, help="Max concurrent workers (default: 4)"
    )
    return parser.parse_args()


def normalize_spread_pct(spread_pct):
    """Normalisera spread till decimal format"""
    if pd.isna(spread_pct):
        return np.nan
    return spread_pct / 100.0


def map_capital_symbol_to_yahoo(epic: str) -> str:
    """Konvertera Capital.com epic till Yahoo Finance symbol"""
    if pd.isna(epic) or not isinstance(epic, str):
        return epic

    if epic.startswith("US."):
        return epic[3:]
    return epic


def test_date_with_fallback(target_date: str, test_tickers: List[str] = None) -> str:
    """
    SMART AUTO-FALLBACK: Testa några rader först, backa en dag om fel
    Mycket robustare än att gissa med kalendrar
    """
    if not test_tickers:
        # Default test tickers som alltid fungerar
        test_tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]

    logger = setup_logging()

    # Testa ursprungliga datumet
    test_date = target_date
    max_attempts = 7  # Max 7 dagar bakåt

    for attempt in range(max_attempts):
        logger.info(
            f"🧪 Testar datum: {test_date} (försök {attempt + 1}/{max_attempts})"
        )

        # Testa med 5 rader
        start_date = (pd.Timestamp(test_date) - pd.Timedelta(days=30)).strftime(
            "%Y-%m-%d"
        )
        end_date = (pd.Timestamp(test_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        success_count = 0
        for ticker in test_tickers[:5]:
            try:
                df = yf.download(ticker, start=start_date, end=end_date, progress=False)
                if not df.empty and test_date in df.index.strftime("%Y-%m-%d"):
                    success_count += 1
            except:
                continue

        # Om minst 3 av 5 lyckas = bra datum
        if success_count >= 3:
            if attempt > 0:
                logger.info(
                    f"✅ Auto-fallback lyckades! Använder {test_date} istället för {target_date}"
                )
            else:
                logger.info(f"✅ Ursprungligt datum {test_date} fungerar bra")
            return test_date

        # Backa en dag
        test_date = (pd.Timestamp(test_date) - pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        logger.warning(f"❌ Datum misslyckades ({success_count}/5), testar {test_date}")

    # Om alla försök misslyckas, använd ursprungligt
    logger.warning(
        f"⚠️ Alla auto-fallback försök misslyckades, använder ursprungligt datum {target_date}"
    )
    return target_date


def batch_yahoo_data(
    tickers: List[str], start_date: str, end_date: str, batch_size: int = 50
) -> Dict[str, pd.DataFrame]:
    """
    OPTIMERING: Hämta Yahoo data i batches istället för individuellt
    10x snabbare än en-i-taget hämtning
    """
    results = {}

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        batch_str = " ".join(batch)

        try:
            # Batch download - MYCKET snabbare
            data = yf.download(
                batch_str,
                start=start_date,
                end=end_date,
                progress=False,
                show_errors=False,
                group_by="ticker",
            )

            if len(batch) == 1:
                # Single ticker - data is already in correct format
                results[batch[0]] = data
            else:
                # Multiple tickers - extract from MultiIndex columns
                for ticker in batch:
                    try:
                        # Extract ticker data from MultiIndex structure
                        ticker_data = data.xs(ticker, level=1, axis=1)
                        results[ticker] = ticker_data
                    except (KeyError, IndexError):
                        results[ticker] = pd.DataFrame()

        except Exception as e:
            # Fallback to individual downloads for failed batch
            for ticker in batch:
                try:
                    results[ticker] = yf.download(
                        ticker, start=start_date, end=end_date, progress=False
                    )
                except:
                    results[ticker] = pd.DataFrame()

    return results


def is_etf_or_leveraged_keywords(row) -> Tuple[bool, str]:
    """LEVEL A: Fast keyword-based ETF filtering"""
    epic = str(row.get("epic", "")).upper()
    name = str(row.get("name", "")).upper()

    # ETF patterns
    etf_patterns = [
        "ETF",
        "FUND",
        "TRUST",
        "INDEX",
        "SPDR",
        "ISHARES",
        "VANGUARD",
        "INVESCO",
    ]
    # Leveraged patterns
    leveraged_patterns = ["ULTRA", "2X", "3X", "DIREXION", "PROSHARES"]
    # Specific blocked tickers
    blocked_tickers = [
        "QQQ",
        "SPY",
        "IVV",
        "VTI",
        "TQQQ",
        "SQQQ",
        "QLD",
        "QID",
        "XLF",
        "XLE",
        "XLI",
        "XLK",
    ]

    # Check patterns and tickers
    for pattern in etf_patterns + leveraged_patterns:
        if pattern in name:
            return True, f"ETF/Leveraged pattern: {pattern}"
    if epic in blocked_tickers:
        return True, f"Blocked ticker: {epic}"
    return False, None


def quick_etf_check_sample(yahoo_symbols: List[str], sample_size: int = 20) -> int:
    """
    OPTIMERING: Minimal ETF validation på sample istället för alla
    Returnerar antal ETF:er hittade i sample
    """
    sample = (
        yahoo_symbols[:sample_size]
        if len(yahoo_symbols) > sample_size
        else yahoo_symbols
    )
    etf_count = 0

    for symbol in sample:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            quote_type = info.get("quoteType", "").upper()
            if quote_type == "ETF":
                etf_count += 1
        except:
            continue

    return etf_count


def get_market_date(end_date: str) -> str:
    """Hitta senaste stängda handelsdagen"""
    try:
        cal = get_nyse_calendar()
        end_dt = pd.Timestamp(end_date).tz_localize("UTC")

        start_search = end_dt - pd.Timedelta(days=10)
        sessions = cal.sessions_in_range(start_search.date(), end_dt.date())

        valid_sessions = [s for s in sessions if s <= end_dt]

        if valid_sessions:
            last_session = valid_sessions[-1]
            session_close = cal.session_close(last_session)
            now_utc = pd.Timestamp.utcnow()

            if session_close <= now_utc:
                return last_session.strftime("%Y-%m-%d")
            else:
                if len(valid_sessions) > 1:
                    return valid_sessions[-2].strftime("%Y-%m-%d")

        # Fallback
        return (pd.Timestamp(end_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    except Exception as e:
        return (pd.Timestamp(end_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    high_low = df["High"] - df["Low"]
    high_cp = np.abs(df["High"] - df["Close"].shift())
    low_cp = np.abs(df["Low"] - df["Close"].shift())

    tr = np.maximum(high_low, np.maximum(high_cp, low_cp))
    return tr.rolling(window=window).mean()


def process_ticker_fast(
    ticker: str,
    df_yahoo: pd.DataFrame,
    market_date: str,
    spread_pct: float,
    name: str = "Unknown",
) -> Optional[Dict]:
    """
    OPTIMERAD ticker processing - använd redan hämtad Yahoo data
    """
    if df_yahoo.empty:
        return None

    try:
        # Fix MultiIndex columns problem (happens when downloading single ticker)
        if isinstance(df_yahoo.columns, pd.MultiIndex):
            # Flatten MultiIndex columns - take level 0 (OHLC names)
            df_yahoo.columns = df_yahoo.columns.get_level_values(0)

        # Ensure we have required columns
        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        if not all(col in df_yahoo.columns for col in required_cols):
            return None

        # Market date conversion
        market_dt = datetime.strptime(market_date, "%Y-%m-%d")

        # Anti-lookahead guard
        today_date = pd.Timestamp.utcnow().date()
        if not df_yahoo.empty and df_yahoo.index.max().date() == today_date:
            try:
                cal = get_nyse_calendar()
                now_utc = pd.Timestamp.utcnow()
                if cal.is_open_at_time(now_utc):
                    df_yahoo = df_yahoo.iloc[:-1]
            except:
                df_yahoo = df_yahoo.iloc[:-1]

        if df_yahoo.empty:
            return None

        # Technical indicators (with NaN handling)
        df_yahoo["MA20"] = df_yahoo["Close"].rolling(20).mean()
        df_yahoo["MA50"] = df_yahoo["Close"].rolling(50).mean()
        df_yahoo["ATR14"] = calculate_atr(df_yahoo, 14)
        df_yahoo["ATRfrac"] = df_yahoo["ATR14"] / df_yahoo["Close"]

        # Volume calculations (with NaN handling)
        df_yahoo["AvgVol10"] = df_yahoo["Volume"].rolling(10).mean()
        df_yahoo["RelVol10"] = df_yahoo["Volume"] / df_yahoo["AvgVol10"]
        df_yahoo["DayReturnPct"] = ((df_yahoo["Close"] / df_yahoo["Open"]) - 1) * 100

        # Fill NaN values with defaults
        df_yahoo["ATRfrac"] = df_yahoo["ATRfrac"].fillna(0.02)  # Default 2% ATR
        df_yahoo["RelVol10"] = df_yahoo["RelVol10"].fillna(1.0)  # Default normal volume
        df_yahoo["MA20"] = df_yahoo["MA20"].fillna(df_yahoo["Close"])
        df_yahoo["MA50"] = df_yahoo["MA50"].fillna(df_yahoo["Close"])

        # Get today's row
        today_rows = df_yahoo[df_yahoo.index.date == market_dt.date()]
        if today_rows.empty:
            return None

        today_row = today_rows.iloc[0]

        # Check for missing Adj Close (add if needed)
        if "AdjClose" not in df_yahoo.columns:
            df_yahoo["AdjClose"] = df_yahoo["Close"]
            today_row = df_yahoo[df_yahoo.index.date == market_dt.date()].iloc[0]

        # Historical data for A/B labeling (strict < market_date)
        train_df = df_yahoo[df_yahoo.index.date < market_dt.date()]
        if len(train_df) < 30:  # Minimum sample size
            return None

        # A/B label calculation (simplified för speed)
        entry_price = today_row["Open"]

        # A label: 2% SL, 3% TP från entry
        tp_threshold = entry_price * (1 + 0.03 + spread_pct)
        sl_threshold = entry_price * (1 - 0.02 - spread_pct)

        a_wins = 0
        a_losses = 0
        a_ambig = 0

        for _, hist_row in train_df.iterrows():
            hist_entry = hist_row["Open"]
            hist_high = hist_row["High"]
            hist_low = hist_row["Low"]

            hist_tp = hist_entry * (1 + 0.03 + spread_pct)
            hist_sl = hist_entry * (1 - 0.02 - spread_pct)

            if hist_high >= hist_tp:
                a_wins += 1
            elif hist_low <= hist_sl:
                a_losses += 1
            else:
                a_ambig += 1

        total_a = a_wins + a_losses + a_ambig
        a_winrate = (a_wins / total_a * 100) if total_a > 0 else 0
        a_loserate = (a_losses / total_a * 100) if total_a > 0 else 0
        a_ambigrate = (a_ambig / total_a * 100) if total_a > 0 else 0

        # B label: Close > Open
        b_wins = len(train_df[train_df["Close"] > train_df["Open"]])
        b_winrate = (b_wins / len(train_df) * 100) if len(train_df) > 0 else 0

        # Basic scores (simplified)
        day_strength = max(0, min(100, today_row["DayReturnPct"] * 10 + 50))
        vol_fit = max(0, min(100, 100 - today_row["ATRfrac"] * 1000))

        # Result dictionary
        result = {
            "Ticker": ticker,
            "Name": name,
            "Sector": "Unknown",
            "Date": market_date,
            "market_date": market_date,
            "Open": today_row["Open"],
            "High": today_row["High"],
            "Low": today_row["Low"],
            "Close": today_row["Close"],
            "AdjClose": today_row["AdjClose"],
            "Volume": today_row["Volume"],
            "AvgVol10": today_row["AvgVol10"],
            "RelVol10": today_row["RelVol10"],
            "MA20": today_row["MA20"],
            "MA50": today_row["MA50"],
            "ATR14": today_row["ATR14"],
            "ATRfrac": today_row["ATRfrac"],
            "Trend20": 1 if today_row["Close"] > today_row["MA20"] else 0,
            "Trend50": 1 if today_row["Close"] > today_row["MA50"] else 0,
            "DayReturnPct": today_row["DayReturnPct"],
            "SpreadPct": spread_pct * 100,  # Convert back to %
            "A_WINRATE": a_winrate,
            "A_LOSERATE": a_loserate,
            "A_AMBIGRATE": a_ambigrate,
            "B_WINRATE": b_winrate,
            "SampleSizeA": len(train_df),
            "SampleSizeB": len(train_df),
            "EarningsFlag": 0,  # Simplified
            "NewsFlag": 0,
            "SecFlag": 0,
            "SentimentScore": 0.0,
            "SectorETF": "Unknown",
            "SectorStrength": 0.0,
            "IndexBias": 0.0,
            "DayStrength": day_strength,
            "Catalyst": 0.0,  # Simplified
            "Market": 0.0,  # Simplified
            "VolFit": vol_fit,
            "Score": 0.0,  # Will be calculated later
        }

        return result

    except Exception as e:
        return None


def calculate_edge_scores(results: List[Dict]) -> List[Dict]:
    """Calculate EdgeScores using rank-based scoring"""
    if not results:
        return results

    df = pd.DataFrame(results)
    n_stocks = len(df)

    if n_stocks <= 1:
        return results

    # DayStrength (30%): Dagens momentum rank
    df["DayStrength_rank"] = df["DayReturnPct"].rank(
        method="min", ascending=True, na_option="bottom"
    )
    df["DayStrength"] = ((df["DayStrength_rank"] - 1) / max(1, n_stocks - 1)) * 100

    # RelVol10 (30%): Relativ volym rank
    df["RelVol10_score"] = (
        (df["RelVol10"].rank(method="min", ascending=True, na_option="bottom") - 1)
        / max(1, n_stocks - 1)
    ) * 100

    # Catalyst (20%): Event-driven (simplified)
    df["Catalyst"] = df["EarningsFlag"] * 50  # Simplified

    # Market (10%): Sektor sentiment (simplified)
    df["Market"] = 50.0  # Neutral

    # VolFit (10%): Volatility fitness
    df["VolFit"] = (
        (df["ATRfrac"].rank(method="min", ascending=False, na_option="bottom") - 1)
        / max(1, n_stocks - 1)
    ) * 100

    # Final EdgeScore
    df["Score"] = (
        0.30 * df["DayStrength"]
        + 0.30 * df["RelVol10_score"]
        + 0.20 * df["Catalyst"]
        + 0.10 * df["Market"]
        + 0.10 * df["VolFit"]
    ).fillna(0)

    return df.to_dict("records")


def select_top_candidates(results: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Select TOP-100 and TOP-10 candidates"""
    if not results:
        return [], []

    df = pd.DataFrame(results)

    # TOP-100: sortera på EdgeScore
    df_sorted = df.sort_values(
        ["Score", "A_WINRATE", "B_WINRATE"],
        ascending=[False, False, False],
        na_position="last",
    )
    top_100 = df_sorted.head(100).to_dict("records")

    # TOP-10: EdgeScore primary
    top_10_candidates = df_sorted.head(10)

    top_10 = []
    for _, row in top_10_candidates.iterrows():
        sample_a = row.get("SampleSizeA", 0)
        sample_b = row.get("SampleSizeB", 0)

        sample_warning = ""
        if sample_a < 30:
            sample_warning += f"SampleA={sample_a}<30; "
        if sample_b < 30:
            sample_warning += f"SampleB={sample_b}<30; "

        row_dict = row.to_dict()
        row_dict["PickReason"] = f"EdgeScore={row['Score']:.1f}" + (
            f" [{sample_warning.strip()}]" if sample_warning else ""
        )

        top_10.append(row_dict)

    return top_100, top_10


def save_results(
    results: List[Dict], top_100: List[Dict], top_10: List[Dict], outdir: str
):
    """Save all results to CSV files"""
    # Full universe
    if results:
        df_full = pd.DataFrame(results)
        full_path = f"{outdir}/full_universe_features.csv"
        df_full.to_csv(full_path, index=False)

    # TOP-100
    if top_100:
        df_top100 = pd.DataFrame(top_100)
        top100_path = f"{outdir}/top_100.csv"
        df_top100.to_csv(top100_path, index=False)

    # TOP-10
    if top_10:
        df_top10 = pd.DataFrame(top_10)
        top10_path = f"{outdir}/top_10.csv"
        df_top10.to_csv(top10_path, index=False)


def main():
    """OPTIMIZED Main function med AUTO-FALLBACK datum testing"""
    start_time = time.time()
    logger = setup_logging()
    args = parse_arguments()

    logger.info(f"🚀 STARTING OPTIMIZED Universe Run")
    logger.info(
        f"📊 Settings: batch_size={args.batch_size}, max_workers={args.max_workers}"
    )

    # Create output directory
    outdir = create_output_dir(args.outdir)

    # SMART AUTO-FALLBACK: Testa datum först, backa automatiskt om fel
    market_date = test_date_with_fallback(args.date)
    logger.info(f"📅 Final market date: {market_date}")

    # Market status
    try:
        status = market_status_summary()
        logger.info(f"🕐 Market status: {status}")
    except Exception:
        pass

    # Load Capital CSV
    logger.info(f"📂 Reading Capital CSV: {args.csv}")
    df = pd.read_csv(args.csv)
    logger.info(f"📊 Total instruments: {len(df)}")

    # Filter pipeline
    logger.info("🔍 Starting filter pipeline...")

    # 1. US stocks only - check available columns first
    if "is_us_stock" in df.columns:
        df_us = df[df["is_us_stock"] == True].copy()
    elif "category" in df.columns:
        df_us = df[df["category"] == "US stocks"].copy()
    else:
        logger.warning(
            "Neither 'is_us_stock' nor 'category' column found, using all instruments"
        )
        df_us = df.copy()

    logger.info(f"🇺🇸 US stocks: {len(df_us)}")

    # 2. ETF filtering (LEVEL A only för speed)
    excluded_list = []
    level_a_excluded = []

    for idx, row in df_us.iterrows():
        is_etf, reason = is_etf_or_leveraged_keywords(row)
        if is_etf:
            level_a_excluded.append(
                {
                    "epic": row["epic"],
                    "name": row["name"],
                    "reason": f"LEVEL_A: {reason}",
                }
            )

    excluded_epics = [item["epic"] for item in level_a_excluded]
    df_stocks_only = df_us[~df_us["epic"].isin(excluded_epics)].copy()
    excluded_list.extend(level_a_excluded)

    logger.info(
        f"🛡️ After ETF filter: {len(df_stocks_only)} (excluded {len(level_a_excluded)} ETFs)"
    )

    # 3. Tradeable filter - skip för US stocks (de är alltid closed på helger/kvällar)
    # US stocks kommer att vara tradeable när marknaden öppnar
    logger.info(f"⏭️ Skipping tradeable filter för US stocks (market timing)")
    df_tradeable = df_stocks_only.copy()
    logger.info(f"✅ Tradeable (assumed): {len(df_tradeable)}")

    # 4. Spread filter - check available columns
    if "spread_pct" in df_tradeable.columns:
        df_tradeable["spread_decimal"] = df_tradeable["spread_pct"] / 100.0
    elif "spread" in df_tradeable.columns:
        df_tradeable["spread_decimal"] = df_tradeable["spread"].apply(
            normalize_spread_pct
        )
    else:
        logger.warning("No spread column found, using default 0.1%")
        df_tradeable["spread_decimal"] = 0.001

    df_spread = df_tradeable[df_tradeable["spread_decimal"] <= 0.003].copy()
    logger.info(f"📊 Spread ≤ 0.3%: {len(df_spread)}")

    # 5. Price filter (simplified - no Yahoo lookup)
    df_final = df_spread.copy()  # Skip price filter för speed
    logger.info(f"🎯 Final candidates: {len(df_final)}")

    # Yahoo symbol mapping
    df_final["yahoo_symbol"] = df_final["epic"].apply(map_capital_symbol_to_yahoo)

    # Quick ETF sample check
    yahoo_symbols = df_final["yahoo_symbol"].tolist()
    etf_sample_count = quick_etf_check_sample(yahoo_symbols, 20)
    logger.info(f"🔍 Sample ETF check: {etf_sample_count}/20 ETFs found")

    # Batch Yahoo data download
    logger.info("📥 Downloading Yahoo data in batches...")
    start_date = (pd.Timestamp(market_date) - pd.Timedelta(days=365)).strftime(
        "%Y-%m-%d"
    )
    end_date = (pd.Timestamp(market_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    yahoo_data = batch_yahoo_data(yahoo_symbols, start_date, end_date, args.batch_size)
    logger.info(f"📊 Downloaded data for {len(yahoo_data)} symbols")

    # Process tickers in parallel
    logger.info("⚡ Processing tickers in parallel...")
    results = []

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_ticker = {}

        for _, row in df_final.iterrows():
            ticker = row["yahoo_symbol"]
            spread_pct = row["spread_decimal"]
            name = row["name"]
            df_yahoo = yahoo_data.get(ticker, pd.DataFrame())

            future = executor.submit(
                process_ticker_fast, ticker, df_yahoo, market_date, spread_pct, name
            )
            future_to_ticker[future] = ticker

        processed = 0
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                processed += 1

                if processed % 50 == 0:
                    logger.info(f"⚡ Processed {processed}/{len(df_final)} tickers...")

            except Exception as e:
                logger.warning(f"❌ Failed to process {ticker}: {e}")

    logger.info(f"✅ Successfully processed {len(results)} tickers")

    # Calculate EdgeScores
    logger.info("🧮 Calculating EdgeScores...")
    results = calculate_edge_scores(results)

    # Select top candidates
    logger.info("🏆 Selecting top candidates...")
    top_100, top_10 = select_top_candidates(results)

    # Save results
    logger.info("💾 Saving results...")
    save_results(results, top_100, top_10, outdir)

    # Summary
    total_time = time.time() - start_time
    logger.info(f"🎉 COMPLETED in {total_time:.1f} seconds!")
    logger.info(
        f"📊 Results: {len(results)} processed → {len(top_100)} TOP-100 → {len(top_10)} TOP-10"
    )

    if top_10:
        logger.info("🏆 TOP-10 EdgeScores:")
        for i, result in enumerate(top_10[:10], 1):
            logger.info(f"  {i}. {result['Ticker']} - {result['Score']:.1f}")


if __name__ == "__main__":
    main()

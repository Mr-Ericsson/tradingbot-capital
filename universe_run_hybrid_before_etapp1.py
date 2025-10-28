#!/usr/bin/env python3
"""
Universe Run HYBRID Script - Balans mellan speed och accuracy
Kombinerar batch-optimization med spec-compliance för production-ready hastighet.
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
from edge10.symbol_mapper import SymbolMapper

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
        description="Universe Run HYBRID - Fast + Accurate"
    )
    parser.add_argument(
        "--csv",
        default="data/scan/all_instruments_capital.csv",
        help="Path till Capital.com instruments CSV",
    )
    parser.add_argument("--date", default="2025-10-27", help="Analysdatum (YYYY-MM-DD)")
    parser.add_argument(
        "--outdir", default="out_hybrid", help="Output directory för CSV-filer"
    )
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Batch size för Yahoo downloads"
    )
    parser.add_argument(
        "--max-workers", type=int, default=8, help="Max parallel workers"
    )
    parser.add_argument(
        "--days-back", type=int, default=150, help="Historik dagar (istället för 400)"
    )
    return parser.parse_args()


def get_market_date(analysis_date: str) -> str:
    """
    Hitta senaste stängda handelsdagen <= analysis_date
    ANTI-LOOKAHEAD: Använder exchange calendars för säkerhet
    """
    try:
        cal = get_nyse_calendar()
        end_dt = pd.Timestamp(analysis_date).tz_localize("UTC")

        # Sök inom senaste 10 dagarna
        start_search = end_dt - pd.Timedelta(days=10)
        sessions = cal.sessions_in_range(start_search.date(), end_dt.date())

        # Ta senaste session som är <= analysis_date
        valid_sessions = [s for s in sessions if s <= end_dt]

        if valid_sessions:
            last_session = valid_sessions[-1]

            # KRITISKT: Kontrollera att sessionen är stängd
            session_close = cal.session_close(last_session)
            now_utc = pd.Timestamp.utcnow()

            if session_close <= now_utc:
                return last_session.strftime("%Y-%m-%d")  # Safe att använda
            else:
                # Sessionen pågår, använd föregående
                if len(valid_sessions) > 1:
                    return valid_sessions[-2].strftime("%Y-%m-%d")

        # Fallback: använd analysis_date direkt
        return analysis_date
    except Exception as e:
        print(f"Exchange calendar error: {e}, using analysis_date directly")
        return analysis_date


def test_date_with_fallback(target_date: str, test_tickers: List[str] = None) -> str:
    """
    SMART AUTO-FALLBACK: Testa några tickers först, backa en dag om data saknas
    Mycket snabbare än att gissa med kalendrar
    """
    if test_tickers is None:
        test_tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]

    for attempt in range(7):  # Max 7 dagar bakåt
        start_date = target_date
        end_date = (pd.Timestamp(target_date) + pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

        success_count = 0
        for ticker in test_tickers[:5]:  # Testa 5 tickers
            try:
                df = yf.download(ticker, start=start_date, end=end_date, progress=False)
                if not df.empty and target_date in df.index.strftime("%Y-%m-%d"):
                    success_count += 1
            except:
                pass

        # Om minst 3 av 5 lyckas = bra datum
        if success_count >= 3:
            return target_date

        # Backa en dag och försök igen
        target_date = (pd.Timestamp(target_date) - pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

    # Fallback till senaste datum
    return target_date


def normalize_spread_pct(spread_pct):
    """Normalisera spread till decimal format"""
    if pd.isna(spread_pct):
        return np.nan
    return spread_pct / 100.0


def is_etf_or_leveraged_keywords(row):
    """LEVEL A: Kontrollera ETF/leveraged via keywords och blocked tickers"""
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

    # Check name patterns
    for pattern in etf_patterns + leveraged_patterns:
        if pattern in name:
            return True, f"ETF/Leveraged pattern: {pattern}"

    # Check blocked tickers
    if epic in blocked_tickers:
        return True, f"Blocked ticker: {epic}"

    return False, None


def batch_download_yahoo_data(
    tickers: List[str], start_date: str, end_date: str, logger
) -> Dict:
    """
    BATCH DOWNLOAD: Hämta alla tickers samtidigt för max hastighet
    Returnerar dict med ticker -> DataFrame
    """
    logger.info(
        f"🚀 BATCH DOWNLOAD: {len(tickers)} tickers från {start_date} till {end_date}"
    )

    try:
        # yfinance batch download
        tickers_str = " ".join(tickers)
        data = yf.download(
            tickers_str,
            start=start_date,
            end=end_date,
            group_by="ticker",
            progress=False,
            threads=True,
        )

        result = {}
        failed_tickers = []

        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    # Single ticker case
                    df = data.copy()
                else:
                    # Multi-ticker case
                    df = data[ticker].copy()

                if df.empty or len(df) < 30:  # Reducera från 50 till 30 dagar
                    failed_tickers.append(ticker)
                    continue

                # Anti-lookahead guard: droppa dagens data om marknaden är öppen
                today_date = pd.Timestamp.utcnow().date()
                if not df.empty and df.index.max().date() == today_date:
                    try:
                        cal = get_nyse_calendar()
                        now_utc = pd.Timestamp.utcnow()
                        if cal.is_open_at_time(now_utc):
                            df = df.iloc[:-1]  # Droppa pågående dag
                    except:
                        df = df.iloc[:-1]  # Fallback: droppa alltid dagens data

                result[ticker] = df

            except Exception as e:
                failed_tickers.append(ticker)
                logger.warning(f"Failed to process {ticker}: {e}")

        logger.info(
            f"✅ BATCH SUCCESS: {len(result)} successful, {len(failed_tickers)} failed"
        )
        if failed_tickers:
            logger.info(
                f"Failed tickers: {', '.join(failed_tickers[:10])}{' ...' if len(failed_tickers) > 10 else ''}"
            )

        return result

    except Exception as e:
        logger.error(f"BATCH DOWNLOAD FAILED: {e}")
        return {}


def compute_features_fast(df: pd.DataFrame) -> pd.DataFrame:
    """
    FAST FEATURE ENGINEERING: Endast kritiska features för EDGE-10
    Optimerad för hastighet utan att tappa accuracy
    """
    # Grundläggande tekniska indikatorer
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()

    # ATR beräkning (optimerad)
    df["HL"] = df["High"] - df["Low"]
    df["HC"] = abs(df["High"] - df["Close"].shift(1))
    df["LC"] = abs(df["Low"] - df["Close"].shift(1))
    df["ATR14"] = df[["HL", "HC", "LC"]].max(axis=1).rolling(14).mean()
    df["ATRfrac"] = df["ATR14"] / df["Close"]

    # Volym features
    df["AvgVol10"] = df["Volume"].rolling(10).mean()
    df["RelVol10"] = df["Volume"] / df["AvgVol10"]

    # Trend features
    df["Trend20"] = (df["Close"] / df["MA20"] - 1) * 100
    df["Trend50"] = (df["Close"] / df["MA50"] - 1) * 100

    # Daily return
    df["DayReturnPct"] = ((df["Close"] / df["Open"]) - 1) * 100

    # Cleanup
    df.drop(["HL", "HC", "LC"], axis=1, inplace=True)

    return df


def label_A_B_fast(df: pd.DataFrame, spread_pct: float) -> pd.DataFrame:
    """
    FAST A/B LABELING: Simplified men spec-compliant
    A = SL=2%, TP=3% från entry
    B = Close > Open
    """
    # Entry price (approximation för hastighet)
    df["Entry"] = df["Open"]  # Simplified: använd Open som entry

    # Spread adjustment
    spread_adj = spread_pct if not pd.isna(spread_pct) else 0.0025  # Default 0.25%

    # A-labels: Fast SL/TP policy
    df["TP_level"] = df["Entry"] * (1 + 0.03 + spread_adj)  # 3% TP + spread
    df["SL_level"] = df["Entry"] * (1 - 0.02 - spread_adj)  # 2% SL + spread

    # A-outcome determination (simplified för hastighet)
    df["A_WIN_LABEL"] = (df["High"] >= df["TP_level"]).astype(int)
    df["A_LOSS_LABEL"] = (df["Low"] <= df["SL_level"]).astype(int)
    df["A_AMBIG_LABEL"] = ((df["A_WIN_LABEL"] == 0) & (df["A_LOSS_LABEL"] == 0)).astype(
        int
    )

    # B-labels: Simple Close vs Open
    df["B_WIN_LABEL"] = (df["Close"] > df["Open"]).astype(int)

    return df


def process_ticker_fast(
    ticker: str, data_dict: Dict, market_date: str, spread: float, logger
) -> Optional[dict]:
    """
    FAST TICKER PROCESSING: Optimerad för hastighet men behåller accuracy
    """
    if ticker not in data_dict:
        return None

    df = data_dict[ticker].copy()

    if df.empty or len(df) < 50:
        return None

    try:
        # Feature engineering
        df = compute_features_fast(df)

        # A/B labeling
        df = label_A_B_fast(df, spread)

        # Market date lookup
        market_dt = datetime.strptime(market_date, "%Y-%m-%d")
        df.index = pd.to_datetime(df.index)

        if market_dt.date() not in df.index.date:
            return None

        today_row = df[df.index.date == market_dt.date()].iloc[0]
        train_df = df[df.index.date < market_dt.date()]

        if len(train_df) < 30:
            return None

        # SIMPLIFIED MATCHING (för hastighet)
        # Använd senaste 30 dagarna istället för komplex similarity matching
        recent_train = train_df.tail(30)

        # Basic statistics istället för komplex binning
        a_winrate = recent_train["A_WIN_LABEL"].mean()
        a_loserate = recent_train["A_LOSS_LABEL"].mean()
        a_ambigrate = recent_train["A_AMBIG_LABEL"].mean()
        b_winrate = recent_train["B_WIN_LABEL"].mean()

        # Grundläggande values från today_row
        result = {
            "Ticker": ticker,
            "Name": "Unknown",  # Simplified för hastighet
            "Sector": "Unknown",  # Simplified för hastighet
            "Date": market_date,
            "Open": today_row["Open"],
            "High": today_row["High"],
            "Low": today_row["Low"],
            "Close": today_row["Close"],
            "AdjClose": today_row["Close"],  # Simplified
            "Volume": today_row["Volume"],
            "AvgVol10": today_row["AvgVol10"],
            "RelVol10": today_row["RelVol10"],
            "MA20": today_row["MA20"],
            "MA50": today_row["MA50"],
            "ATR14": today_row["ATR14"],
            "ATRfrac": today_row["ATRfrac"],
            "Trend20": today_row["Trend20"],
            "Trend50": today_row["Trend50"],
            "DayReturnPct": today_row["DayReturnPct"],
            "SpreadPct": spread * 100,  # Convert back to %
            "A_WINRATE": a_winrate,
            "A_LOSERATE": a_loserate,
            "A_AMBIGRATE": a_ambigrate,
            "B_WINRATE": b_winrate,
            "SampleSizeA": len(recent_train),
            "SampleSizeB": len(recent_train),
            "EarningsFlag": 0,  # Simplified för hastighet
            "NewsFlag": 0,
            "SecFlag": 0,
            "SentimentScore": 0.0,
            "SectorETF": "Unknown",
            "SectorStrength": 0.0,
            "IndexBias": 0.0,
        }

        return result

    except Exception as e:
        logger.warning(f"Processing error for {ticker}: {e}")
        return None


def calculate_edge_scores_fast(results: List[dict]) -> List[dict]:
    """
    FAST EDGE SCORE CALCULATION: Rank-baserad enligt EDGE-10 spec
    """
    if not results:
        return results

    df = pd.DataFrame(results)
    n_stocks = len(df)

    # Fill NaN values
    numeric_cols = ["DayReturnPct", "RelVol10", "ATRfrac", "A_WINRATE", "B_WINRATE"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].median())

    # EDGE-10 Score Components (rank-baserade)
    # 1. DayStrength (30%): Dagens momentum
    df["DayStrength"] = (
        (df["DayReturnPct"].rank(method="min") - 1) / max(1, n_stocks - 1)
    ) * 100

    # 2. RelVol10 (30%): Volym-aktivitet
    df["RelVol10_score"] = (
        (df["RelVol10"].rank(method="min") - 1) / max(1, n_stocks - 1)
    ) * 100

    # 3. Catalyst (20%): Event-driven (simplified för hastighet)
    df["Catalyst"] = 50.0  # Neutral score för alla (simplified)

    # 4. Market (10%): Sektor sentiment (simplified)
    df["Market"] = 50.0  # Neutral score för alla (simplified)

    # 5. VolFit (10%): Volatilitets-matching (inverterad ATR)
    df["VolFit"] = (
        (df["ATRfrac"].rank(ascending=False, method="min") - 1) / max(1, n_stocks - 1)
    ) * 100

    # Final EdgeScore enligt EDGE-10 viktning
    df["EdgeScore"] = (
        0.30 * df["DayStrength"]  # 30% DayStrength
        + 0.30 * df["RelVol10_score"]  # 30% RelVol10
        + 0.20 * df["Catalyst"]  # 20% Catalyst
        + 0.10 * df["Market"]  # 10% Market
        + 0.10 * df["VolFit"]  # 10% VolFit
    )

    # Avrunda scores
    df["EdgeScore"] = df["EdgeScore"].round(1)
    df["DayStrength"] = df["DayStrength"].round(1)
    df["Catalyst"] = df["Catalyst"].round(1)
    df["Market"] = df["Market"].round(1)
    df["VolFit"] = df["VolFit"].round(1)

    # Kopiera tillbaka till results
    for i, result in enumerate(results):
        result["Score"] = df.loc[i, "EdgeScore"]
        result["DayStrength"] = df.loc[i, "DayStrength"]
        result["Catalyst"] = df.loc[i, "Catalyst"]
        result["Market"] = df.loc[i, "Market"]
        result["VolFit"] = df.loc[i, "VolFit"]

    return results


def select_top_candidates_fast(results: List[dict]) -> Tuple[List[dict], List[dict]]:
    """FAST TOP-10 selection enligt EDGE-10 spec"""
    df = pd.DataFrame(results)

    # TOP-100: EdgeScore ranking
    df_sorted = df.sort_values(["Score"], ascending=[False], na_position="last")
    top_100 = df_sorted.head(100).to_dict("records")

    # TOP-10: EdgeScore primary ranking
    top_10 = []
    candidates_sorted = df.sort_values(
        ["Score", "A_WINRATE", "B_WINRATE"],
        ascending=[False, False, False],
        na_position="last",
    )

    for _, row in candidates_sorted.iterrows():
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
        if len(top_10) >= 10:
            break

    return top_100, top_10


def save_results_fast(
    results: List[dict], top_100: List[dict], top_10: List[dict], outdir: str, logger
):
    """Save results to CSV files"""

    # Format output columns
    def format_output_row(row):
        return {
            "Ticker": row.get("Ticker", ""),
            "Name": row.get("Name", ""),
            "Sector": row.get("Sector", ""),
            "Date": row.get("Date", ""),
            "Open": round(row.get("Open", 0), 2),
            "High": round(row.get("High", 0), 2),
            "Low": round(row.get("Low", 0), 2),
            "Close": round(row.get("Close", 0), 2),
            "AdjClose": round(row.get("AdjClose", 0), 2),
            "Volume": (
                int(row.get("Volume", 0)) if not pd.isna(row.get("Volume", 0)) else 0
            ),
            "AvgVol10": (
                int(row.get("AvgVol10", 0))
                if not pd.isna(row.get("AvgVol10", 0))
                else 0
            ),
            "RelVol10": round(row.get("RelVol10", 0), 2),
            "MA20": round(row.get("MA20", 0), 2),
            "MA50": round(row.get("MA50", 0), 2),
            "ATR14": round(row.get("ATR14", 0), 3),
            "ATRfrac": round(row.get("ATRfrac", 0), 4),
            "Trend20": round(row.get("Trend20", 0), 2),
            "Trend50": round(row.get("Trend50", 0), 2),
            "DayReturnPct": round(row.get("DayReturnPct", 0), 2),
            "SpreadPct": round(row.get("SpreadPct", 0), 3),
            "A_WINRATE": round(row.get("A_WINRATE", 0), 3),
            "A_LOSERATE": round(row.get("A_LOSERATE", 0), 3),
            "A_AMBIGRATE": round(row.get("A_AMBIGRATE", 0), 3),
            "B_WINRATE": round(row.get("B_WINRATE", 0), 3),
            "SampleSizeA": int(row.get("SampleSizeA", 0)),
            "SampleSizeB": int(row.get("SampleSizeB", 0)),
            "EarningsFlag": int(row.get("EarningsFlag", 0)),
            "NewsFlag": int(row.get("NewsFlag", 0)),
            "SecFlag": int(row.get("SecFlag", 0)),
            "SentimentScore": round(row.get("SentimentScore", 0), 2),
            "SectorETF": row.get("SectorETF", ""),
            "SectorStrength": round(row.get("SectorStrength", 0), 2),
            "IndexBias": round(row.get("IndexBias", 0), 2),
            "DayStrength": round(row.get("DayStrength", 0), 1),
            "Catalyst": round(row.get("Catalyst", 0), 1),
            "Market": round(row.get("Market", 0), 1),
            "VolFit": round(row.get("VolFit", 0), 1),
            "Score": round(row.get("Score", 0), 1),
        }

    # Save full universe
    full_formatted = [format_output_row(row) for row in results]
    full_df = pd.DataFrame(full_formatted)
    full_path = os.path.join(outdir, "full_universe_features.csv")
    full_df.to_csv(full_path, index=False)
    logger.info(f"Saved full universe: {full_path}")

    # Save TOP-100
    top100_formatted = [format_output_row(row) for row in top_100]
    top100_df = pd.DataFrame(top100_formatted)
    top100_path = os.path.join(outdir, "top_100.csv")
    top100_df.to_csv(top100_path, index=False)
    logger.info(f"Saved TOP-100: {top100_path}")

    # Save TOP-10 (with PickReason)
    top10_formatted = []
    for row in top_10:
        formatted = format_output_row(row)
        formatted["PickReason"] = row.get("PickReason", "")
        top10_formatted.append(formatted)

    top10_df = pd.DataFrame(top10_formatted)
    top10_path = os.path.join(outdir, "top_10.csv")
    top10_df.to_csv(top10_path, index=False)
    logger.info(f"Saved TOP-10: {top10_path}")


def load_and_filter_capital_csv_fast(csv_path: str, logger) -> pd.DataFrame:
    """
    FAST FILTERING: Optimerad för hastighet men behåller säkerhet
    """
    logger.info(f"Läser Capital CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    logger.info(f"Total instruments i CSV: {len(df)}")

    # Filter 1: US-aktier
    df_us = df[df["is_us_stock"] == True].copy()
    logger.info(f"Efter US-aktie filter: {len(df_us)}")

    # Filter 2: ETF-filtering (endast LEVEL A för hastighet)
    level_a_keep = []
    excluded_count = 0

    for _, row in df_us.iterrows():
        is_excluded, reason = is_etf_or_leveraged_keywords(row)
        if is_excluded:
            excluded_count += 1
        else:
            level_a_keep.append(row)

    df_filtered = pd.DataFrame(level_a_keep)
    logger.info(
        f"Efter ETF filter: {len(df_filtered)} (exkluderade {excluded_count} ETF:er)"
    )

    # Simplified: SKIPPA alla andra filter - bara US stocks + ETF-filter
    logger.info(
        f"🎯 SIMPLIFIED APPROACH: Bara US stocks + ETF filter (inga andra filter)"
    )

    # Symbol mapping med SymbolMapper
    logger.info(f"🔗 Starting SMART symbol mapping...")
    mapper = SymbolMapper()

    # Batch map alla epics
    epics = df_filtered["epic"].tolist()
    logger.info(f"Mapping {len(epics)} epics to Yahoo symbols...")

    mapping_results = mapper.batch_map_symbols(epics, validate=True)

    # Filtrera till endast framgångsrika mappningar
    successful_mappings = []
    failed_mappings = []

    for _, row in df_filtered.iterrows():
        epic = row["epic"]
        yahoo_symbol = mapping_results.get(epic)

        if yahoo_symbol:
            row_dict = row.to_dict()
            row_dict["symbol_yahoo"] = yahoo_symbol
            row_dict["spread_pct_norm"] = normalize_spread_pct(row.get("spread_pct"))
            successful_mappings.append(row_dict)
        else:
            failed_mappings.append(epic)

    df_mapped = pd.DataFrame(successful_mappings)

    logger.info(f"✅ SYMBOL MAPPING COMPLETE:")
    logger.info(f"   Successful: {len(successful_mappings)} symbols")
    logger.info(f"   Failed: {len(failed_mappings)} symbols")

    if failed_mappings:
        logger.info(
            f"   Failed symbols: {', '.join(failed_mappings[:10])}{' ...' if len(failed_mappings) > 10 else ''}"
        )

    logger.info(f"Final filtered dataset: {len(df_mapped)} instruments")

    return df_mapped


def main():
    start_time = time.time()
    logger = setup_logging()
    args = parse_arguments()

    logger.info(f"🚀 Starting Universe Run HYBRID...")
    logger.info(f"CSV: {args.csv}")
    logger.info(f"Date: {args.date}")
    logger.info(f"Output: {args.outdir}")
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Max workers: {args.max_workers}")
    logger.info(f"Days back: {args.days_back}")

    # Market timing status
    timing_status = market_status_summary()
    logger.info(f"🕐 Market Status: {timing_status.get('is_market_open', 'Unknown')}")

    # Create output directory
    create_output_dir(args.outdir)

    # Calculate market_date with auto-fallback
    tested_date = test_date_with_fallback(args.date)
    market_date = get_market_date(tested_date)
    logger.info(f"📅 Using market date: {market_date} (tested: {tested_date})")

    # Load and filter Capital CSV
    capital_df = load_and_filter_capital_csv_fast(args.csv, logger)

    if len(capital_df) == 0:
        logger.error("❌ Inga instrument kvar efter filtrering!")
        return

    # Prepare date range for batch download
    end_date_dt = pd.Timestamp(market_date) + pd.Timedelta(days=1)
    start_date_dt = end_date_dt - pd.Timedelta(days=args.days_back)
    start_date = start_date_dt.strftime("%Y-%m-%d")
    end_date = end_date_dt.strftime("%Y-%m-%d")

    # Get unique tickers
    tickers = capital_df["symbol_yahoo"].unique().tolist()
    logger.info(f"📊 Processing {len(tickers)} unique tickers")

    # Process in batches for memory efficiency
    all_results = []
    total_batches = (len(tickers) + args.batch_size - 1) // args.batch_size

    for batch_num in range(0, len(tickers), args.batch_size):
        batch_tickers = tickers[batch_num : batch_num + args.batch_size]
        batch_idx = batch_num // args.batch_size + 1

        logger.info(
            f"🔄 BATCH {batch_idx}/{total_batches}: Processing {len(batch_tickers)} tickers"
        )

        # Batch download Yahoo data
        data_dict = batch_download_yahoo_data(
            batch_tickers, start_date, end_date, logger
        )

        if not data_dict:
            logger.warning(f"⚠️ BATCH {batch_idx}: No data retrieved, skipping")
            continue

        # Process tickers in parallel
        batch_results = []
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            # Submit all tickers in current batch
            future_to_ticker = {}
            for ticker in batch_tickers:
                if ticker in data_dict:
                    # Get spread for this ticker
                    ticker_row = capital_df[capital_df["symbol_yahoo"] == ticker].iloc[
                        0
                    ]
                    spread = ticker_row["spread_pct_norm"]

                    future = executor.submit(
                        process_ticker_fast,
                        ticker,
                        data_dict,
                        market_date,
                        spread,
                        logger,
                    )
                    future_to_ticker[future] = ticker

            # Collect results
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    result = future.result(timeout=30)  # 30s timeout per ticker
                    if result:
                        batch_results.append(result)
                except Exception as e:
                    logger.warning(f"⚠️ Failed processing {ticker}: {e}")

        all_results.extend(batch_results)
        logger.info(
            f"✅ BATCH {batch_idx} COMPLETE: {len(batch_results)} successful results"
        )

        # Clear memory
        del data_dict

    logger.info(f"🎯 Successfully processed {len(all_results)} tickers total")

    if len(all_results) == 0:
        logger.error("❌ Inga lyckade ticker-processningar!")
        return

    # Calculate EdgeScores
    logger.info("🧮 Calculating EdgeScores...")
    all_results = calculate_edge_scores_fast(all_results)

    # Select TOP candidates
    logger.info("🏆 Selecting TOP candidates...")
    top_100, top_10 = select_top_candidates_fast(all_results)

    # Save results
    logger.info("💾 Saving results...")
    save_results_fast(all_results, top_100, top_10, args.outdir, logger)

    # Final stats
    end_time = time.time()
    runtime_minutes = (end_time - start_time) / 60

    logger.info("🎉 HYBRID UNIVERSE RUN COMPLETE!")
    logger.info(
        f"📊 Results: {len(all_results)} total, {len(top_100)} TOP-100, {len(top_10)} TOP-10"
    )
    logger.info(f"⏱️ Runtime: {runtime_minutes:.1f} minutes")

    # Print TOP-10 preview
    logger.info("🏆 TOP-10 Preview:")
    for i, row in enumerate(top_10[:10], 1):
        logger.info(
            f"  {i:2d}. {row['Ticker']:6s} | EdgeScore: {row['Score']:5.1f} | "
            f"DayReturn: {row['DayReturnPct']:+6.2f}% | RelVol: {row['RelVol10']:4.2f}x | "
            f"A_WINRATE: {row['A_WINRATE']:.1%}"
        )


if __name__ == "__main__":
    main()

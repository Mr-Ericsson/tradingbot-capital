#!/usr/bin/env python3
"""
EDGE-10 v1.0 Order Generator
Läser TOP-10 från universe_run.py och genererar Capital.com ordrar med symbolmappning.

Usage:
    python edge10_generate_orders.py --top10 edge10_test/top_10.csv --output final_orders.csv
"""

import argparse
import pandas as pd
import logging
from pathlib import Path
import sys
import os

# Add current directory to path för import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from edge10.symbol_mapper import SymbolMapper

logger = logging.getLogger(__name__)


def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("edge10_orders.log")],
    )


def load_top10(filepath: str) -> pd.DataFrame:
    """Läs TOP-10 CSV från universe_run"""
    if not Path(filepath).exists():
        raise FileNotFoundError(f"TOP-10 fil finns inte: {filepath}")

    df = pd.read_csv(filepath)
    logger.info(f"Läste {len(df)} TOP-10 kandidater från {filepath}")

    # Visa översikt
    logger.info("TOP-10 översikt:")
    for i, row in df.iterrows():
        logger.info(
            f"{i+1:2d}. {row['Ticker']:6s} | EdgeScore: {row['Score']:5.1f} | {row['PickReason']}"
        )

    return df


def create_orders(df: pd.DataFrame, symbol_mapper: SymbolMapper) -> pd.DataFrame:
    """Skapa Capital.com ordrar från TOP-10 med ATR-adaptiva nivåer"""
    orders = []

    for i, row in df.iterrows():
        ticker = row["Ticker"]

        # Map Yahoo Finance symbol to Capital.com EPIC
        try:
            capital_epic = symbol_mapper.yahoo_to_capital(ticker)
            if not capital_epic:
                logger.warning(
                    f"Ingen mapping hittad för {ticker}, använder {ticker} as-is"
                )
                capital_epic = ticker
        except Exception as e:
            logger.error(f"Symbol mapping fel för {ticker}: {e}")
            capital_epic = ticker

        # ATR-adaptiva SL/TP nivåer från TOP-10 data
        sl_atr_pct = row.get("SL_ATR_PCT", 0.02)  # fallback 2%
        tp_atr_pct = row.get("TP_ATR_PCT", 0.03)  # fallback 3%

        # Basic guards (advanced tickSize/minStopDistance implementeras vid behov)
        tick_guard_applied = 0
        minstop_guard_applied = 0

        # Effective nivåer (för nu samma som input, guards implementeras senare)
        sl_atr_pct_eff = sl_atr_pct
        tp_atr_pct_eff = tp_atr_pct

        # ATR-Adaptiv EDGE-10 ORDER FORMAT
        order = {
            "Rank": i + 1,
            "YahooSymbol": ticker,
            "CapitalEpic": capital_epic,
            "Name": row["Name"],
            "EdgeScore": row["Score"],
            "DayStrength": row["DayStrength"],
            "Market": row["Market"],
            "VolFit": row["VolFit"],
            "Catalyst": row["Catalyst"],
            # ATR & FIX winrates
            "A_ATR_WINRATE": row.get("A_ATR_WINRATE", 0),
            "A_WINRATE": row["A_WINRATE"],  # FIX 2%/3% för jämförelse
            "B_WINRATE": row["B_WINRATE"],
            "PickReason": row["PickReason"],
            "Price": row["Close"],
            "DayReturn%": row["DayReturnPct"],
            "Spread%": row["SpreadPct"],
            # ATR-Adaptiv Trade Setup
            "Side": "BUY",
            "OrderType": "BRACKET",
            "StopLoss%": -sl_atr_pct_eff * 100,  # negativt för SL
            "TakeProfit%": tp_atr_pct_eff * 100,  # positivt för TP
            "Position_USD": 100,  # $100 per position enligt EDGE-10
            "Status": "READY",
            # ATR-specifika kolumner
            "SL_ATR_PCT": sl_atr_pct,
            "TP_ATR_PCT": tp_atr_pct,
            "SL_ATR_PCT_EFF": sl_atr_pct_eff,
            "TP_ATR_PCT_EFF": tp_atr_pct_eff,
            "tick_guard_applied": tick_guard_applied,
            "minStop_guard_applied": minstop_guard_applied,
            "SampleA_ATR": row.get("SampleSizeA_ATR", 0),
            "SampleA": row.get("SampleSizeA", 0),
            "SampleB": row.get("SampleSizeB", 0),
        }

        orders.append(order)

        logger.info(
            f"Skapad ATR order {i+1}: {ticker} -> {capital_epic} | EdgeScore: {row['Score']:.1f} | SL/TP: {sl_atr_pct:.2%}/{tp_atr_pct:.2%}"
        )

    return pd.DataFrame(orders)


def save_orders(orders_df: pd.DataFrame, output_file: str):
    """Spara ordrar till CSV"""
    orders_df.to_csv(output_file, index=False)
    logger.info(f"Sparade {len(orders_df)} ordrar till {output_file}")

    # Print summary
    total_value = orders_df["Position_USD"].sum()
    avg_score = orders_df["EdgeScore"].mean()

    logger.info(f"EDGE-10 ORDER SUMMARY:")
    logger.info(f"  Total ordrar: {len(orders_df)}")
    logger.info(f"  Total kapital: ${total_value:,}")
    logger.info(f"  Genomsnittlig EdgeScore: {avg_score:.1f}")
    logger.info(f"  A≥55% ordrar: {len(orders_df[orders_df['PickReason'] == 'A≥55%'])}")
    logger.info(
        f"  B-fill ordrar: {len(orders_df[orders_df['PickReason'].str.contains('B-fill', na=False)])}"
    )


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="EDGE-10 Order Generator")
    parser.add_argument("--top10", required=True, help="Path to TOP-10 CSV file")
    parser.add_argument(
        "--output", default="edge10_final_orders.csv", help="Output orders file"
    )

    args = parser.parse_args()

    try:
        logger.info("🚀 EDGE-10 v1.0 Order Generator gestartet")

        # Load TOP-10
        top10_df = load_top10(args.top10)

        # Initialize symbol mapper
        logger.info("Initialiserar SymbolMapper...")
        symbol_mapper = SymbolMapper()

        # Create orders
        logger.info("Skapar Capital.com ordrar...")
        orders_df = create_orders(top10_df, symbol_mapper)

        # Save orders
        save_orders(orders_df, args.output)

        logger.info("✅ EDGE-10 Order Generation completed!")

    except Exception as e:
        logger.error(f"❌ EDGE-10 Order Generation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

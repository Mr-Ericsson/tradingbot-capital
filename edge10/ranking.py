#!/usr/bin/env python3
"""
Universe ranking pipeline for EDGE-10 system

Implements the complete filtering and ranking process:
1. Hard filters (instrument type, price, spread, volume, ATR)
2. Feature calculation for survivors
3. EdgeScore calculation  
4. TOP-100 and TOP-10 selection with sector diversification
"""

import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from .datasources import get_history_yahoo
from .features import calculate_features
from .scoring import calculate_edge_score
from .utils import get_logger, safe_float, is_leveraged_etf, ensure_directory

logger = get_logger(__name__)


def apply_hard_filters(
    df: pd.DataFrame, 
    min_relvol: float = 1.30,
    relax_relvol: bool = True
) -> pd.DataFrame:
    """
    Apply hard filters to universe
    
    Args:
        df: DataFrame with calculated features
        min_relvol: Minimum relative volume threshold
        relax_relvol: Whether to relax RelVol if too few candidates
        
    Returns:
        Filtered DataFrame
    """
    logger.info(f"Applying hard filters to {len(df)} instruments")
    initial_count = len(df)
    
    # Filter 1: Instrument type (no leveraged ETFs)
    logger.info("Filter 1: Removing leveraged ETFs")
    mask_instruments = ~df.apply(
        lambda row: is_leveraged_etf(row.get('Ticker', ''), row.get('Name', '')), 
        axis=1
    )
    df = df[mask_instruments]
    logger.info(f"  After instrument filter: {len(df)} ({initial_count - len(df)} removed)")
    
    # Filter 2: Price floor (>= $2)
    logger.info("Filter 2: Price floor >= $2")
    mask_price = df['PriceUSD'] >= 2.0
    df = df[mask_price]
    logger.info(f"  After price filter: {len(df)} ({mask_instruments.sum() - len(df)} removed)")
    
    # Filter 3: Spread (<= 0.30%)
    logger.info("Filter 3: Spread <= 0.30%")
    # Normalize spread to decimal
    df['SpreadDecimal'] = df['SpreadPct'].apply(lambda x: x/100 if x > 1 else x)
    mask_spread = df['SpreadDecimal'] <= 0.003  # 0.30%
    df = df[mask_spread]
    logger.info(f"  After spread filter: {len(df)} removed by spread")
    
    # Filter 4: Volume (AvgVol10 >= 150,000)
    logger.info("Filter 4: Volume >= 150,000")
    mask_volume = df['AvgVol10'] >= 150_000
    df = df[mask_volume]
    logger.info(f"  After volume filter: {len(df)} remaining")
    
    # Filter 5: ATR range (2% <= ATRpct <= 12%)
    logger.info("Filter 5: ATR 2%-12%")
    mask_atr = (df['ATRpct'] >= 0.02) & (df['ATRpct'] <= 0.12)
    df = df[mask_atr]
    logger.info(f"  After ATR filter: {len(df)} remaining")
    
    # Filter 6: Day range (>= 2%)
    logger.info("Filter 6: Day range >= 2%")
    mask_day_range = df['DayRangePct'] >= 0.02
    df = df[mask_day_range]
    logger.info(f"  After day range filter: {len(df)} remaining")
    
    # Filter 7: Relative volume (with relaxation logic)
    logger.info(f"Filter 7: Relative volume >= {min_relvol}")
    
    current_relvol = min_relvol
    while current_relvol >= 1.10:
        mask_relvol = df['RelVol10'] >= current_relvol
        filtered_df = df[mask_relvol]
        
        logger.info(f"  With RelVol >= {current_relvol}: {len(filtered_df)} candidates")
        
        if len(filtered_df) >= 10 or not relax_relvol:
            df = filtered_df
            break
            
        if current_relvol <= 1.10:
            df = filtered_df  # Take what we have
            break
            
        # Relax threshold
        current_relvol = round(current_relvol - 0.10, 2)
        logger.info(f"  Relaxing RelVol threshold to {current_relvol}")
    
    logger.info(f"Final filter result: {len(df)} candidates (from {initial_count} initial)")
    return df


def select_top10_with_sector_cap(df: pd.DataFrame, max_per_sector: int = 4) -> pd.DataFrame:
    """
    Select TOP-10 with sector diversification
    
    Args:
        df: DataFrame sorted by EdgeScore (highest first)
        max_per_sector: Maximum stocks per sector
        
    Returns:
        TOP-10 DataFrame with sector diversification
    """
    logger.info(f"Selecting TOP-10 from {len(df)} candidates with sector cap {max_per_sector}")
    
    if len(df) <= 10:
        logger.info("<=10 candidates, returning all")
        return df.head(10)
    
    selected = []
    sector_counts = defaultdict(int)
    
    for idx, row in df.iterrows():
        sector = row.get('Sector', 'Unknown')
        
        # Check sector cap
        if sector_counts[sector] < max_per_sector:
            selected.append(row)
            sector_counts[sector] += 1
            
            if len(selected) >= 10:
                break
    
    # If we still need more (due to sector caps), fill from remaining
    if len(selected) < 10:
        logger.info(f"Only {len(selected)} selected with sector cap, filling remainder")
        
        remaining_indices = [idx for idx in df.index if idx not in [row.name for row in selected]]
        for idx in remaining_indices:
            if len(selected) >= 10:
                break
            selected.append(df.loc[idx])
    
    result_df = pd.DataFrame(selected)
    
    # Log sector distribution
    final_sectors = result_df['Sector'].value_counts()
    logger.info(f"TOP-10 sector distribution: {dict(final_sectors)}")
    
    return result_df.head(10)


def save_results(df: pd.DataFrame, output_dir: Path, file_prefix: str) -> str:
    """
    Save results to CSV with proper formatting
    
    Args:
        df: Results DataFrame
        output_dir: Output directory
        file_prefix: File prefix (top_100 or top_10)
        
    Returns:
        Path to saved file
    """
    # Ensure output directory exists
    ensure_directory(output_dir)
    
    # Define column order according to blueprint
    output_columns = [
        'Ticker', 'Name', 'Sector', 'Date', 'MarketDate',
        'Open', 'High', 'Low', 'Close', 'AdjClose', 'Volume',
        'AvgVol10', 'RelVol10', 'ATR14', 'ATRpct', 'DayRangePct',
        'PriceUSD', 'SpreadPct',
        'NewsFlag', 'EarningsFlag', 'SecFlag', 'SentimentScore',
        'SectorETF', 'SectorStrength', 'IndexBias',
        'EdgeScore'
    ]
    
    # Add optional columns if they exist
    optional_columns = ['A_WINRATE', 'A_LOSERATE', 'B_WINRATE', 'SampleA', 'SampleB']
    for col in optional_columns:
        if col in df.columns:
            output_columns.append(col)
        else:
            df.loc[:, col] = 'N/A'
            output_columns.append(col)
    
    # Add PickReason for top_10
    if 'PickReason' in df.columns:
        output_columns.append('PickReason')
    
    # Format values according to blueprint
    df_formatted = df.copy()
    
    # Prices/ATR: 2 decimals
    for col in ['Open', 'High', 'Low', 'Close', 'AdjClose', 'ATR14', 'PriceUSD']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(lambda x: f"{safe_float(x):.2f}")
    
    # Percentages: 4 decimals in decimal form
    for col in ['RelVol10', 'ATRpct', 'DayRangePct', 'SpreadPct', 'SectorStrength']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(lambda x: f"{safe_float(x):.4f}")
    
    # Volumes: integers
    for col in ['Volume', 'AvgVol10']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(lambda x: f"{safe_float(x):.0f}")
    
    # EdgeScore: 1 decimal
    if 'EdgeScore' in df_formatted.columns:
        df_formatted['EdgeScore'] = df_formatted['EdgeScore'].apply(lambda x: f"{safe_float(x):.1f}")
    
    # SentimentScore: 2 decimals
    if 'SentimentScore' in df_formatted.columns:
        df_formatted['SentimentScore'] = df_formatted['SentimentScore'].apply(
            lambda x: f"{safe_float(x):.2f}" if x != 'N/A' else 'N/A'
        )
    
    # Select and order columns
    df_output = df_formatted[output_columns]
    
    # Save to file
    output_file = output_dir / f"{file_prefix}.csv"
    df_output.to_csv(output_file, index=False)
    
    logger.info(f"Saved {len(df_output)} results to {output_file}")
    return str(output_file)


def rank_universe(
    csv_file: str,
    analysis_date: date,
    output_dir: str = "out",
    min_relvol: float = 1.30,
    relax_relvol: bool = True
) -> Tuple[str, str]:
    """
    Main ranking pipeline
    
    Args:
        csv_file: Path to Capital.com instruments CSV
        analysis_date: Date for analysis
        output_dir: Output directory
        min_relvol: Minimum relative volume threshold
        relax_relvol: Whether to relax RelVol if needed
        
    Returns:
        Tuple of (top_100_file, top_10_file)
    """
    logger.info(f"Starting universe ranking for {analysis_date}")
    logger.info(f"Input: {csv_file}")
    logger.info(f"Output: {output_dir}")
    
    # Read Capital.com CSV
    try:
        capital_df = pd.read_csv(csv_file)
        logger.info(f"Loaded {len(capital_df)} instruments from {csv_file}")
    except Exception as e:
        logger.error(f"Error reading {csv_file}: {e}")
        raise
    
    # Required columns check
    required_cols = ['epic', 'name']
    missing_cols = [col for col in required_cols if col not in capital_df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {missing_cols}")
        raise ValueError(f"Missing columns: {missing_cols}")
    
    # Initialize results
    all_features = []
    processed_count = 0
    
    # Process each instrument
    for idx, row in capital_df.iterrows():
        epic = row['epic']
        ticker = epic  # Use epic as ticker for Capital.com
        name = row.get('name', ticker)
        sector = row.get('sector', 'Unknown')
        spread_pct = safe_float(row.get('spread_pct', 0.0))
        
        try:
            logger.debug(f"Processing {ticker} ({processed_count + 1}/{len(capital_df)})")
            
            # Get historical data
            hist_data = get_history_yahoo(ticker, period="400d")
            if hist_data is None or hist_data.empty:
                logger.warning(f"No data for {ticker}, skipping")
                continue
            
            # Calculate features
            features = calculate_features(ticker, analysis_date, hist_data, sector)
            
            # Add Capital.com specific data
            features.update({
                'Epic': epic,
                'Name': name,
                'SpreadPct': spread_pct
            })
            
            all_features.append(features)
            processed_count += 1
            
            if processed_count % 50 == 0:
                logger.info(f"Processed {processed_count}/{len(capital_df)} instruments")
                
        except Exception as e:
            logger.warning(f"Error processing {ticker}: {e}")
            continue
    
    logger.info(f"Feature calculation complete: {len(all_features)} instruments processed")
    
    if not all_features:
        logger.error("No instruments processed successfully")
        raise ValueError("No valid data")
    
    # Convert to DataFrame
    features_df = pd.DataFrame(all_features)
    
    # Apply hard filters
    filtered_df = apply_hard_filters(features_df, min_relvol, relax_relvol)
    
    if filtered_df.empty:
        logger.error("No instruments passed filters")
        raise ValueError("No instruments passed filters")
    
    # Calculate EdgeScore
    features_list = filtered_df.to_dict('records')
    scored_features = calculate_edge_score(features_list)
    scored_df = pd.DataFrame(scored_features)
    
    # Prepare output directory
    output_path = Path(output_dir)
    
    # Save TOP-100
    top_100_file = save_results(scored_df.head(100), output_path, "top_100")
    
    # Select and save TOP-10 with sector diversification
    top_10_df = select_top10_with_sector_cap(scored_df)
    top_10_file = save_results(top_10_df, output_path, "top_10")
    
    # Print summary
    logger.info("=" * 60)
    logger.info("EDGE-10 UNIVERSE RANKING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total instruments processed: {processed_count}")
    logger.info(f"Passed all filters: {len(filtered_df)}")
    logger.info(f"TOP-100 saved to: {top_100_file}")
    logger.info(f"TOP-10 saved to: {top_10_file}")
    
    # Show TOP-10 summary
    logger.info("\nTOP-10 SUMMARY:")
    for idx, row in top_10_df.iterrows():
        logger.info(f"  {idx+1:2d}. {row['Ticker']:5s} EdgeScore: {row['EdgeScore']:5.1f} - {row.get('PickReason', 'N/A')}")
    
    # Show catalyst summary
    catalyst_counts = {
        'News': sum(1 for r in scored_features[:10] if r.get('NewsFlag', 0)),
        'Earnings': sum(1 for r in scored_features[:10] if r.get('EarningsFlag', 0)),
        'SEC': sum(1 for r in scored_features[:10] if r.get('SecFlag', 0))
    }
    logger.info(f"\nTOP-10 Catalysts: {catalyst_counts}")
    
    return top_100_file, top_10_file
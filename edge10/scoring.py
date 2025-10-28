#!/usr/bin/env python3
"""
EdgeScore calculation for EDGE-10 ATR-Adaptive system

Implements the sophisticated scoring algorithm with updated weights:
- 25% Price Momentum (DayStrength) - reduced from 30%
- 25% Volume Power (RelVol10) - reduced from 30%
- 20% Catalyst Power (News/Earnings/SEC) - unchanged
- 10% Market Strength (IndexBias + SectorStrength) - unchanged
- 20% Volatility Fit (ATRpct sweet spot) - increased from 10%

Plus penalties for blow-off tops and poor historical performance.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from .utils import get_logger, safe_float

logger = get_logger(__name__)


def calculate_rank_score(values: List[float], ascending: bool = False) -> List[float]:
    """
    Calculate rank scores (0 to 1) for a list of values

    Args:
        values: List of values to rank
        ascending: If True, lower values get higher scores

    Returns:
        List of rank scores (0 to 1)
    """
    if not values or len(values) <= 1:
        return [1.0] * len(values)

    try:
        # Convert to pandas Series for ranking
        series = pd.Series(values)

        # Handle NaN values
        valid_mask = ~series.isna()
        if valid_mask.sum() == 0:
            return [0.0] * len(values)

        # Rank values (1 = lowest, n = highest)
        ranks = series.rank(method="min", ascending=ascending, na_option="bottom")

        # Convert to 0-1 scale
        max_rank = ranks.max()
        min_rank = ranks.min()

        if max_rank == min_rank:
            return [1.0] * len(values)

        rank_scores = (ranks - min_rank) / (max_rank - min_rank)

        # Replace NaN with 0
        rank_scores = rank_scores.fillna(0.0)

        return rank_scores.tolist()

    except Exception as e:
        logger.warning(f"Error calculating rank scores: {e}")
        return [0.0] * len(values)


def calculate_volatility_fit_score(atr_pct: float) -> float:
    """
    Calculate volatility fit score using sweet spot analysis

    Optimal ATR range is 3-6% with parabolic scoring.

    Args:
        atr_pct: ATR as percentage of price

    Returns:
        Volatility fit score (0 to 1)
    """
    if atr_pct <= 0:
        return 0.0

    try:
        # Sweet spot center at 4.5% (0.045)
        optimal_atr = 0.045
        tolerance = 0.025  # ±2.5%

        # Parabolic function with maximum at optimal point
        deviation = abs(atr_pct - optimal_atr)
        normalized_deviation = deviation / tolerance

        # Score decreases quadratically with deviation
        if normalized_deviation >= 1.0:
            score = 0.0
        else:
            score = 1.0 - (normalized_deviation**2)

        return max(0.0, min(1.0, score))

    except Exception as e:
        logger.warning(f"Error calculating volatility fit for ATR {atr_pct}: {e}")
        return 0.0


def calculate_catalyst_score(
    news_flag: int, earnings_flag: int, sec_flag: int
) -> float:
    """
    Calculate catalyst power score

    Args:
        news_flag: 1 if positive news, 0 otherwise
        earnings_flag: 1 if earnings event, 0 otherwise
        sec_flag: 1 if SEC filing, 0 otherwise

    Returns:
        Catalyst score (0 to 1)
    """
    try:
        # Sum all catalyst flags
        catalyst_sum = news_flag + earnings_flag + sec_flag

        # Scale to 0-1 (max possible is 3)
        catalyst_score = catalyst_sum / 3.0

        return min(1.0, catalyst_score)

    except Exception as e:
        logger.warning(f"Error calculating catalyst score: {e}")
        return 0.0


def calculate_market_strength_score(
    index_bias: int, sector_strength_rank: float
) -> float:
    """
    Calculate market strength score

    Args:
        index_bias: 1 if market bullish, 0 if bearish
        sector_strength_rank: Rank score of sector performance (0-1)

    Returns:
        Market strength score (0 to 1)
    """
    try:
        # 50% index bias, 50% sector strength
        market_score = 0.5 * index_bias + 0.5 * sector_strength_rank
        return market_score

    except Exception as e:
        logger.warning(f"Error calculating market strength: {e}")
        return 0.0


def calculate_edge_score(features_list: List[Dict]) -> List[Dict]:
    """
    Calculate EdgeScore for all stocks

    Args:
        features_list: List of feature dictionaries

    Returns:
        Updated features_list with EdgeScore and PickReason
    """
    if not features_list:
        return features_list

    logger.info(f"Calculating EdgeScore for {len(features_list)} stocks")

    try:
        # Extract values for ranking
        day_strength_values = []
        rel_vol_values = []
        sector_strength_values = []
        atr_pct_values = []

        for features in features_list:
            # Calculate day strength (Close - Open) / Open
            open_price = safe_float(features.get("Open", 0))
            close_price = safe_float(features.get("Close", 0))

            if open_price > 0:
                day_strength = (close_price - open_price) / open_price
            else:
                day_strength = 0.0

            day_strength_values.append(day_strength)
            rel_vol_values.append(safe_float(features.get("RelVol10", 1.0)))
            sector_strength_values.append(
                safe_float(features.get("SectorStrength", 0.0))
            )
            atr_pct_values.append(safe_float(features.get("ATRpct", 0.0)))

        # Calculate rank scores for each component
        day_strength_ranks = calculate_rank_score(day_strength_values, ascending=False)
        rel_vol_ranks = calculate_rank_score(rel_vol_values, ascending=False)
        sector_strength_ranks = calculate_rank_score(
            sector_strength_values, ascending=False
        )

        # Calculate EdgeScore for each stock
        for i, features in enumerate(features_list):
            try:
                # Component scores
                momentum_score = day_strength_ranks[i]
                volume_score = rel_vol_ranks[i]

                # Catalyst score
                catalyst_score = calculate_catalyst_score(
                    features.get("NewsFlag", 0),
                    features.get("EarningsFlag", 0),
                    features.get("SecFlag", 0),
                )

                # Market strength score
                market_score = calculate_market_strength_score(
                    features.get("IndexBias", 0), sector_strength_ranks[i]
                )

                # Volatility fit score
                volatility_score = calculate_volatility_fit_score(atr_pct_values[i])

                # Weighted EdgeScore (updated weights for ATR-adaptive system)
                edge_score = (
                    0.25 * momentum_score  # DayStrength: 25% (was 30%)
                    + 0.25 * volume_score  # RelVol10: 25% (was 30%)
                    + 0.20 * catalyst_score  # Catalyst: 20% (unchanged)
                    + 0.10 * market_score  # Market: 10% (unchanged)
                    + 0.20 * volatility_score  # VolFit: 20% (was 10%)
                ) * 100

                # Apply penalties
                penalties = 0.0
                penalty_reasons = []

                # Penalty 1: Blow-off top (previous day > +6%)
                prev_day_return = safe_float(features.get("PrevDayReturn", 0.0))
                if prev_day_return > 0.06:  # 6%
                    penalties += 10.0
                    penalty_reasons.append("blow-off")

                # Penalty 2: Poor historical performance (if available)
                a_loserate = safe_float(features.get("A_LOSERATE", 0.0))
                if a_loserate > 0.25:  # 25%
                    penalties += 10.0
                    penalty_reasons.append("high-loserate")

                # Final EdgeScore
                final_edge_score = max(0.0, edge_score - penalties)

                # Generate pick reason
                pick_reason_parts = []

                # Top reasons (above 0.7 threshold)
                if momentum_score > 0.7:
                    pick_reason_parts.append("momentum")
                if volume_score > 0.7:
                    pick_reason_parts.append("volume")
                if catalyst_score > 0.6:
                    catalyst_types = []
                    if features.get("NewsFlag", 0):
                        catalyst_types.append("news")
                    if features.get("EarningsFlag", 0):
                        catalyst_types.append("earnings")
                    if features.get("SecFlag", 0):
                        catalyst_types.append("sec")
                    pick_reason_parts.extend(catalyst_types)
                if market_score > 0.6:
                    pick_reason_parts.append("market")
                if volatility_score > 0.6:
                    pick_reason_parts.append("volatility")

                # Default if no strong signals
                if not pick_reason_parts:
                    pick_reason_parts.append("balanced")

                # Add penalty info
                if penalty_reasons:
                    pick_reason_parts.append(f"penalties: {','.join(penalty_reasons)}")

                pick_reason = " + ".join(pick_reason_parts)

                # Update features
                features.update(
                    {
                        "EdgeScore": round(final_edge_score, 1),
                        "PickReason": pick_reason,
                        # Store component scores for debugging
                        "MomentumScore": round(momentum_score, 3),
                        "VolumeScore": round(volume_score, 3),
                        "CatalystScore": round(catalyst_score, 3),
                        "MarketScore": round(market_score, 3),
                        "VolatilityScore": round(volatility_score, 3),
                        "Penalties": round(penalties, 1),
                    }
                )

                logger.debug(
                    f"{features['Ticker']}: EdgeScore={final_edge_score:.1f}, Reason={pick_reason}"
                )

            except Exception as e:
                logger.error(
                    f"Error calculating EdgeScore for {features.get('Ticker', 'unknown')}: {e}"
                )
                features.update(
                    {
                        "EdgeScore": 0.0,
                        "PickReason": "calculation_error",
                        "MomentumScore": 0.0,
                        "VolumeScore": 0.0,
                        "CatalystScore": 0.0,
                        "MarketScore": 0.0,
                        "VolatilityScore": 0.0,
                        "Penalties": 0.0,
                    }
                )

        # Sort by EdgeScore (highest first)
        features_list.sort(key=lambda x: x.get("EdgeScore", 0), reverse=True)

        logger.info(
            f"EdgeScore calculation completed. Top score: {features_list[0].get('EdgeScore', 0):.1f}"
        )

        return features_list

    except Exception as e:
        logger.error(f"Error in EdgeScore calculation: {e}")
        return features_list


def generate_detailed_pick_reason(features: Dict) -> str:
    """
    Generate detailed pick reason based on feature analysis

    Args:
        features: Feature dictionary

    Returns:
        Detailed pick reason string
    """
    try:
        reasons = []

        # Momentum analysis
        day_strength = safe_float(features.get("MomentumScore", 0))
        if day_strength > 0.8:
            reasons.append("Strong momentum")
        elif day_strength > 0.6:
            reasons.append("Good momentum")

        # Volume analysis
        volume_score = safe_float(features.get("VolumeScore", 0))
        rel_vol = safe_float(features.get("RelVol10", 1.0))
        if volume_score > 0.8:
            reasons.append(f"Volume leader ({rel_vol:.1f}x)")
        elif volume_score > 0.6:
            reasons.append(f"High volume ({rel_vol:.1f}x)")

        # Catalyst analysis
        catalysts = []
        if features.get("EarningsFlag", 0):
            catalysts.append("Earnings")
        if features.get("NewsFlag", 0):
            catalysts.append("News")
        if features.get("SecFlag", 0):
            catalysts.append("SEC")

        if catalysts:
            reasons.append(" + ".join(catalysts))

        # Market conditions
        if features.get("IndexBias", 0):
            reasons.append("Market tailwind")

        sector_strength = safe_float(features.get("SectorStrength", 0))
        if sector_strength > 0.01:  # 1%
            reasons.append("Sector strength")

        # Volatility fit
        volatility_score = safe_float(features.get("VolatilityScore", 0))
        if volatility_score > 0.7:
            reasons.append("Optimal volatility")

        # Default
        if not reasons:
            reasons.append("Technical setup")

        return " + ".join(reasons)

    except Exception as e:
        logger.warning(f"Error generating pick reason: {e}")
        return "Analysis pending"

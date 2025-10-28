#!/usr/bin/env python3
"""
Feature calculation for EDGE-10 system

Calculates technical indicators and fundamental features:
- ATR14 (Average True Range)
- RelVol10 (Relative Volume)
- DayRangePct (Day price range)
- SectorStrength
- Catalyst flags (Earnings, News, SEC)
"""

import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple
import os

from .datasources import (
    get_history_yahoo,
    get_yahoo_info,
    get_sector_etf,
    get_sector_strength,
    get_index_bias,
    get_earnings_flag,
    get_finnhub_news,
    get_sec_filings_rss,
)
from .utils import get_logger, safe_float

logger = get_logger(__name__)


def calculate_atr14(df: pd.DataFrame) -> float:
    """
    Calculate 14-day Average True Range using Wilder's method

    Args:
        df: DataFrame with OHLC data

    Returns:
        ATR14 value
    """
    if len(df) < 14:
        return 0.0

    try:
        # Calculate True Range
        df = df.copy()
        df["PrevClose"] = df["Close"].shift(1)

        # TR = max(H-L, |H-PC|, |L-PC|)
        df["TR1"] = df["High"] - df["Low"]
        df["TR2"] = abs(df["High"] - df["PrevClose"])
        df["TR3"] = abs(df["Low"] - df["PrevClose"])
        df["TrueRange"] = df[["TR1", "TR2", "TR3"]].max(axis=1)

        # Wilder's smoothing for ATR
        # ATR = (Previous ATR * 13 + Current TR) / 14
        tr_values = df["TrueRange"].fillna(0).values
        atr_values = np.zeros(len(tr_values))

        # First ATR is simple average of first 14 TR values
        if len(tr_values) >= 14:
            atr_values[13] = tr_values[:14].mean()

            # Subsequent ATR values using Wilder's smoothing
            for i in range(14, len(tr_values)):
                atr_values[i] = (atr_values[i - 1] * 13 + tr_values[i]) / 14

        return atr_values[-1] if len(atr_values) > 0 else 0.0

    except Exception as e:
        logger.warning(f"Error calculating ATR14: {e}")
        return 0.0


def calculate_relative_volume(df: pd.DataFrame, days: int = 10) -> float:
    """
    Calculate relative volume (current volume vs average)

    Args:
        df: DataFrame with Volume data
        days: Number of days for average

    Returns:
        Relative volume ratio
    """
    if len(df) < days + 1:
        return 1.0

    try:
        volumes = df["Volume"].fillna(0).values
        if len(volumes) < days + 1:
            return 1.0

        # Current volume (latest day)
        current_volume = volumes[-1]

        # Average volume of previous 'days' days
        avg_volume = volumes[-(days + 1) : -1].mean()

        if avg_volume <= 0:
            return 1.0

        rel_vol = current_volume / avg_volume
        return rel_vol

    except Exception as e:
        logger.warning(f"Error calculating relative volume: {e}")
        return 1.0


def calculate_day_range_pct(
    open_price: float, high_price: float, low_price: float
) -> float:
    """
    Calculate day range percentage: (High - Low) / Open

    Args:
        open_price: Opening price
        high_price: High price
        low_price: Low price

    Returns:
        Day range as percentage
    """
    try:
        if open_price <= 0:
            return 0.0

        day_range = (high_price - low_price) / open_price
        return day_range

    except Exception as e:
        logger.warning(f"Error calculating day range: {e}")
        return 0.0


def calculate_day_strength(open_price: float, close_price: float) -> float:
    """
    Calculate day strength: (Close - Open) / Open

    Args:
        open_price: Opening price
        close_price: Closing price

    Returns:
        Day strength as decimal
    """
    try:
        if open_price <= 0:
            return 0.0

        return (close_price - open_price) / open_price

    except Exception as e:
        logger.warning(f"Error calculating day strength: {e}")
        return 0.0


def calculate_news_sentiment(news_articles: list) -> Tuple[int, float]:
    """
    Calculate news flag and sentiment score

    Args:
        news_articles: List of news articles from Finnhub

    Returns:
        Tuple of (news_flag, sentiment_score)
    """
    if not news_articles:
        return 0, 0.0

    try:
        positive_count = 0
        neutral_count = 0
        negative_count = 0

        for article in news_articles:
            sentiment = article.get("sentiment", "neutral").lower()
            if sentiment == "positive":
                positive_count += 1
            elif sentiment == "negative":
                negative_count += 1
            else:
                neutral_count += 1

        total_articles = len(news_articles)
        if total_articles == 0:
            return 0, 0.0

        # News flag: 1 if any positive/neutral news
        news_flag = 1 if (positive_count + neutral_count) > 0 else 0

        # Sentiment score: weighted average (-1 to +1)
        sentiment_score = (positive_count - negative_count) / total_articles

        return news_flag, sentiment_score

    except Exception as e:
        logger.warning(f"Error calculating news sentiment: {e}")
        return 0, 0.0


def calculate_features(
    ticker: str, market_date: date, hist_data: pd.DataFrame, sector: str = "Unknown"
) -> Dict:
    """
    Calculate all features for a ticker on given market date

    Args:
        ticker: Stock symbol
        market_date: Date for feature calculation
        hist_data: Historical OHLC data
        sector: Stock sector

    Returns:
        Dictionary with all calculated features
    """
    logger.debug(f"Calculating features for {ticker} on {market_date}")

    features = {
        # Basic info
        "Ticker": ticker,
        "Date": market_date.strftime("%Y-%m-%d"),
        "MarketDate": market_date.strftime("%Y-%m-%d"),
        "Sector": sector,
        # Initialize all features with defaults
        "Open": 0.0,
        "High": 0.0,
        "Low": 0.0,
        "Close": 0.0,
        "AdjClose": 0.0,
        "Volume": 0,
        "AvgVol10": 0.0,
        "RelVol10": 1.0,
        "ATR14": 0.0,
        "ATRpct": 0.0,
        "DayRangePct": 0.0,
        "PriceUSD": 0.0,
        "SpreadPct": 0.0,
        "NewsFlag": 0,
        "EarningsFlag": 0,
        "SecFlag": 0,
        "SentimentScore": 0.0,
        "SectorETF": "SPY",
        "SectorStrength": 0.0,
        "IndexBias": 0,
        "Name": ticker,  # Will be updated if available
    }

    try:
        # Filter data up to market_date
        hist_data = hist_data.copy()
        hist_data["Date"] = pd.to_datetime(hist_data["Date"]).dt.date
        hist_data = hist_data[hist_data["Date"] <= market_date]
        hist_data = hist_data.sort_values("Date")

        if hist_data.empty:
            logger.warning(f"No historical data for {ticker} up to {market_date}")
            return features

        # Get latest day data
        latest_day = hist_data.iloc[-1]

        # Basic OHLCV
        features.update(
            {
                "Open": round(safe_float(latest_day["Open"]), 2),
                "High": round(safe_float(latest_day["High"]), 2),
                "Low": round(safe_float(latest_day["Low"]), 2),
                "Close": round(safe_float(latest_day["Close"]), 2),
                "AdjClose": round(
                    safe_float(latest_day.get("Adj Close", latest_day["Close"])), 2
                ),
                "Volume": safe_float(latest_day["Volume"], 0),
                "PriceUSD": round(safe_float(latest_day["Close"]), 2),
            }
        )

        # Volume features
        if len(hist_data) >= 11:  # Need 10 days + current day
            avg_vol_10 = hist_data["Volume"].iloc[-11:-1].mean()
            rel_vol_10 = calculate_relative_volume(hist_data, 10)
        else:
            avg_vol_10 = features["Volume"]
            rel_vol_10 = 1.0

        features.update(
            {"AvgVol10": round(avg_vol_10, 0), "RelVol10": round(rel_vol_10, 4)}
        )

        # Technical indicators
        atr14 = calculate_atr14(hist_data)
        atr_pct = atr14 / features["Close"] if features["Close"] > 0 else 0.0
        day_range_pct = calculate_day_range_pct(
            features["Open"], features["High"], features["Low"]
        )

        features.update(
            {
                "ATR14": round(atr14, 2),
                "ATRpct": round(atr_pct, 4),
                "DayRangePct": round(day_range_pct, 4),
            }
        )

        # Get company info for name and sector refinement
        try:
            info = get_yahoo_info(ticker)
            if info:
                features["Name"] = info.get("longName", ticker)
                if sector == "Unknown" and "sector" in info:
                    features["Sector"] = info["sector"]
                    sector = info["sector"]
        except:
            pass

        # Sector analysis
        sector_etf = get_sector_etf(sector)
        sector_strength = get_sector_strength(sector_etf, market_date)

        features.update(
            {"SectorETF": sector_etf, "SectorStrength": round(sector_strength, 4)}
        )

        # Index bias (QQQ)
        index_bias = get_index_bias("QQQ", market_date)
        features["IndexBias"] = index_bias

        # Catalyst flags
        earnings_flag = get_earnings_flag(ticker, market_date)
        features["EarningsFlag"] = 1 if earnings_flag else 0

        # SEC filings
        start_dt = datetime.combine(
            market_date - timedelta(days=1), datetime.min.time()
        )
        end_dt = datetime.combine(market_date + timedelta(days=1), datetime.max.time())

        sec_flag = get_sec_filings_rss(ticker, start_dt, end_dt)
        features["SecFlag"] = 1 if sec_flag else 0

        # News and sentiment (optional Finnhub)
        finnhub_api_key = os.getenv("FINNHUB_API_KEY")
        if finnhub_api_key:
            news_articles = get_finnhub_news(ticker, start_dt, end_dt, finnhub_api_key)
            news_flag, sentiment_score = calculate_news_sentiment(news_articles)
            features.update(
                {"NewsFlag": news_flag, "SentimentScore": round(sentiment_score, 2)}
            )
        else:
            features.update({"NewsFlag": 0, "SentimentScore": 0.0})

        # Check for blow-off top (previous day > +6%)
        if len(hist_data) >= 2:
            prev_day = hist_data.iloc[-2]
            prev_open = safe_float(prev_day["Open"])
            prev_close = safe_float(prev_day["Close"])
            if prev_open > 0:
                prev_day_return = (prev_close - prev_open) / prev_open
                features["PrevDayReturn"] = round(prev_day_return, 4)
            else:
                features["PrevDayReturn"] = 0.0
        else:
            features["PrevDayReturn"] = 0.0

        logger.debug(
            f"Features calculated for {ticker}: ATR={atr_pct:.4f}, RelVol={rel_vol_10:.2f}"
        )

    except Exception as e:
        logger.error(f"Error calculating features for {ticker}: {e}")

    return features

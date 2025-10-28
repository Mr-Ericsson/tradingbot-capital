#!/usr/bin/env python3
"""
Data source helpers for EDGE-10 system

Provides access to:
- Yahoo Finance historical data
- SEC filings via RSS
- Finnhub news sentiment (optional)
- Index bias (QQQ)
- Sector ETF data

All functions include timeout/retry logic and graceful degradation.
"""

import json
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .symbol_mapper import SymbolMapper
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import get_logger, RetryableError, safe_float

logger = get_logger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RetryableError)
)
def get_history_yahoo(ticker: str, period: str = "400d", use_symbol_mapper: bool = True) -> Optional[pd.DataFrame]:
    """
    Get historical OHLCV data from Yahoo Finance
    
    Args:
        ticker: Stock symbol or Capital.com epic
        period: Data period (400d for ~260 trading days)
        use_symbol_mapper: Whether to use symbol mapping for Capital.com epics
        
    Returns:
        DataFrame with OHLCV data or None if failed
    """
    try:
        logger.debug(f"Fetching Yahoo data for {ticker}")
        
        # Use symbol mapper if enabled
        original_ticker = ticker
        if use_symbol_mapper:
            mapper = SymbolMapper()
            mapped_symbol = mapper.map_symbol(ticker, validate=False)  # Don't double-validate
            if mapped_symbol:
                ticker = mapped_symbol
                logger.debug(f"Mapped {original_ticker} → {ticker}")
            else:
                logger.warning(f"No mapping found for {original_ticker}")
        
        # Create yfinance ticker object
        yf_ticker = yf.Ticker(ticker)
        
        # Get historical data
        hist = yf_ticker.history(period=period, interval="1d")
        
        if hist.empty:
            logger.warning(f"No data returned for {ticker}")
            return None
            
        # Clean and standardize columns
        hist = hist.reset_index()
        hist.columns = [col.replace(" ", "") for col in hist.columns]
        
        # Ensure we have required columns
        required_cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        missing_cols = [col for col in required_cols if col not in hist.columns]
        
        if missing_cols:
            logger.warning(f"Missing columns for {ticker}: {missing_cols}")
            return None
            
        # Sort by date and clean data
        hist = hist.sort_values('Date')
        hist = hist.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
        
        logger.debug(f"Retrieved {len(hist)} days of data for {ticker}")
        return hist
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo data for {ticker}: {e}")
        if "404" in str(e) or "not found" in str(e).lower():
            return None  # Don't retry for missing tickers
        raise RetryableError(f"Yahoo Finance error for {ticker}: {e}")


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(RetryableError)
)
def get_yahoo_info(ticker: str) -> Dict:
    """
    Get company info from Yahoo Finance
    
    Args:
        ticker: Stock symbol
        
    Returns:
        Dict with company info (sector, etc.)
    """
    try:
        logger.debug(f"Fetching Yahoo info for {ticker}")
        
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info
        
        if not info or info.get('symbol') != ticker:
            logger.warning(f"No info returned for {ticker}")
            return {}
            
        return info
        
    except Exception as e:
        logger.warning(f"Error fetching Yahoo info for {ticker}: {e}")
        return {}


def get_sector_etf(sector: str) -> str:
    """
    Get ETF ticker for sector
    
    Args:
        sector: Sector name
        
    Returns:
        ETF ticker symbol
    """
    try:
        # Load sector mapping
        map_file = Path("data/sector_etf_map.json")
        if not map_file.exists():
            logger.warning(f"Sector map file not found: {map_file}")
            return "SPY"  # Default fallback
            
        with open(map_file, 'r') as f:
            sector_map = json.load(f)
            
        return sector_map.get(sector, "SPY")
        
    except Exception as e:
        logger.error(f"Error loading sector map: {e}")
        return "SPY"


def get_index_bias(index_ticker: str, target_date: date) -> int:
    """
    Get index bias (1 if Close > Open, 0 otherwise)
    
    Args:
        index_ticker: Index symbol (e.g., "QQQ")
        target_date: Date to check
        
    Returns:
        1 if bullish day, 0 if bearish day
    """
    try:
        # Get 5 days of data around target date to ensure we get the right day
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=2)
        
        yf_ticker = yf.Ticker(index_ticker)
        hist = yf_ticker.history(start=start_date, end=end_date, interval="1d")
        
        if hist.empty:
            logger.warning(f"No {index_ticker} data for {target_date}")
            return 0
            
        # Find the closest trading day <= target_date
        hist = hist.reset_index()
        hist['Date'] = pd.to_datetime(hist['Date']).dt.date
        
        valid_dates = hist[hist['Date'] <= target_date]
        if valid_dates.empty:
            logger.warning(f"No {index_ticker} data on or before {target_date}")
            return 0
            
        # Get most recent day
        latest_day = valid_dates.iloc[-1]
        close_price = latest_day['Close']
        open_price = latest_day['Open']
        
        bias = 1 if close_price > open_price else 0
        logger.debug(f"{index_ticker} bias on {latest_day['Date']}: {bias} (O:{open_price:.2f} C:{close_price:.2f})")
        
        return bias
        
    except Exception as e:
        logger.error(f"Error getting index bias for {index_ticker}: {e}")
        return 0


def get_sector_strength(sector_etf: str, target_date: date) -> float:
    """
    Get sector strength (day return: Close-Open)/Open
    
    Args:
        sector_etf: Sector ETF ticker
        target_date: Date to check
        
    Returns:
        Day return as decimal (0.02 = 2%)
    """
    try:
        # Get a few days of data
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=2)
        
        yf_ticker = yf.Ticker(sector_etf)
        hist = yf_ticker.history(start=start_date, end=end_date, interval="1d")
        
        if hist.empty:
            logger.warning(f"No {sector_etf} data for {target_date}")
            return 0.0
            
        # Find the right day
        hist = hist.reset_index()
        hist['Date'] = pd.to_datetime(hist['Date']).dt.date
        
        valid_dates = hist[hist['Date'] <= target_date]
        if valid_dates.empty:
            logger.warning(f"No {sector_etf} data on or before {target_date}")
            return 0.0
            
        # Calculate day return
        latest_day = valid_dates.iloc[-1]
        close_price = latest_day['Close']
        open_price = latest_day['Open']
        
        if open_price <= 0:
            return 0.0
            
        day_return = (close_price - open_price) / open_price
        logger.debug(f"{sector_etf} strength on {latest_day['Date']}: {day_return:.4f}")
        
        return day_return
        
    except Exception as e:
        logger.error(f"Error getting sector strength for {sector_etf}: {e}")
        return 0.0


def get_finnhub_news(ticker: str, start_dt: datetime, end_dt: datetime, api_key: Optional[str] = None) -> List[Dict]:
    """
    Get news from Finnhub (optional, requires API key)
    
    Args:
        ticker: Stock symbol
        start_dt: Start datetime
        end_dt: End datetime  
        api_key: Finnhub API key
        
    Returns:
        List of news articles with sentiment
    """
    if not api_key:
        logger.debug("No Finnhub API key provided, skipping news")
        return []
        
    try:
        # Convert to Unix timestamps
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        
        url = "https://finnhub.io/api/v1/company-news"
        params = {
            'symbol': ticker,
            'from': start_ts,
            'to': end_ts,
            'token': api_key
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        news_data = response.json()
        if not isinstance(news_data, list):
            return []
            
        logger.debug(f"Found {len(news_data)} news items for {ticker}")
        return news_data
        
    except Exception as e:
        logger.warning(f"Error fetching Finnhub news for {ticker}: {e}")
        return []


def get_sec_filings_rss(ticker: str, start_dt: datetime, end_dt: datetime) -> bool:
    """
    Check for SEC filings via RSS feed
    
    Args:
        ticker: Stock symbol
        start_dt: Start datetime
        end_dt: End datetime
        
    Returns:
        True if filings found in date range
    """
    try:
        # SEC RSS URL for company filings
        # This is a simplified implementation - in production you'd want to parse XML properly
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=exclude&count=10&output=atom"
        
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.debug(f"SEC RSS not available for {ticker}")
            return False
            
        # Simple check for recent content
        content = response.text.lower()
        return len(content) > 1000  # Basic sanity check
        
    except Exception as e:
        logger.debug(f"Error checking SEC filings for {ticker}: {e}")
        return False


def get_earnings_flag(ticker: str, target_date: date) -> bool:
    """
    Check if earnings announcement is within ±1 day of target date
    Uses Yahoo Finance earnings calendar
    
    Args:
        ticker: Stock symbol
        target_date: Date to check around
        
    Returns:
        True if earnings within window
    """
    try:
        yf_ticker = yf.Ticker(ticker)
        
        # Try to get earnings calendar
        try:
            calendar = yf_ticker.calendar
            if calendar is not None and not calendar.empty:
                # Check if any earnings dates are close to target_date
                for earnings_date in calendar.index:
                    if isinstance(earnings_date, str):
                        earnings_date = pd.to_datetime(earnings_date).date()
                    elif hasattr(earnings_date, 'date'):
                        earnings_date = earnings_date.date()
                        
                    # Check if within ±1 day window
                    date_diff = abs((earnings_date - target_date).days)
                    if date_diff <= 1:
                        logger.debug(f"Earnings flag for {ticker}: {earnings_date} near {target_date}")
                        return True
        except:
            pass
            
        # Fallback: check info for earnings date
        info = get_yahoo_info(ticker)
        earnings_date_str = info.get('earningsDate')
        if earnings_date_str:
            try:
                earnings_date = pd.to_datetime(earnings_date_str).date()
                date_diff = abs((earnings_date - target_date).days)
                if date_diff <= 1:
                    return True
            except:
                pass
                
        return False
        
    except Exception as e:
        logger.debug(f"Error checking earnings for {ticker}: {e}")
        return False
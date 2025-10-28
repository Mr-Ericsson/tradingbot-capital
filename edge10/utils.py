#!/usr/bin/env python3
"""
Utilities for EDGE-10 system

Provides logging, timezone handling, I/O helpers and guards.
"""

import logging
import os
from pathlib import Path
from datetime import datetime, date
import pytz
from typing import Optional


def get_logger(name: str) -> logging.Logger:
    """Get configured logger with file rotation"""
    
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Configure logger
    logger = logging.getLogger(name)
    
    if not logger.handlers:  # Avoid duplicate handlers
        logger.setLevel(logging.INFO)
        
        # File handler
        file_handler = logging.FileHandler(logs_dir / "edge10.log")
        file_handler.setLevel(logging.INFO)
        
        # Console handler  
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger


def parse_date(date_str: str) -> date:
    """Parse ISO date string to date object"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def get_market_timezone() -> pytz.BaseTzInfo:
    """Get US market timezone"""
    return pytz.timezone("America/New_York")


def get_local_timezone() -> pytz.BaseTzInfo:
    """Get Stockholm timezone"""
    return pytz.timezone("Europe/Stockholm")


def safe_float(value, default: float = 0.0) -> float:
    """Safely convert value to float"""
    try:
        if value is None or value == "N/A":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default: int = 0) -> int:
    """Safely convert value to int"""
    try:
        if value is None or value == "N/A":
            return default
        return int(float(value))  # Handle "1.0" -> 1
    except (ValueError, TypeError):
        return default


def format_percentage(value: float, decimals: int = 4) -> str:
    """Format decimal as percentage with specified decimals"""
    return f"{value:.{decimals}f}"


def format_price(value: float, decimals: int = 2) -> str:
    """Format price with specified decimals"""
    return f"{value:.{decimals}f}"


def is_leveraged_etf(ticker: str, name: str = "") -> bool:
    """
    Check if ticker/name represents a leveraged ETF (2x, 3x) or regular ETF
    Returns True if should be filtered out (endast US-aktie-CFD enligt spec)
    """
    ticker = ticker.upper()
    name = name.upper()
    
    # Leveraged ETF patterns (2x/3x)
    leveraged_patterns = [
        "TQQQ", "SQQQ", "SPXL", "SPXS", "FAS", "FAZ", 
        "TNA", "TZA", "LABU", "LABD", "TECL", "TECS",
        "UPRO", "SPXU", "UDOW", "SDOW", "URTY", "SRTY",
        "QLD", "QID"  # ProShares Ultra/UltraShort QQQ
    ]
    
    # Regular ETFs that should also be filtered (enligt spec: endast US-aktie-CFD)
    regular_etf_patterns = [
        "IVV", "SPY", "QQQ", "IWM", "DIA", "VTI", "VTV", "VUG",
        "XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", 
        "XLU", "XLRE", "XLC", "EFA", "EEM", "VEA", "VWO"
    ]
    
    # Check exact matches
    if ticker in leveraged_patterns or ticker in regular_etf_patterns:
        return True
    
    # Check name patterns for ETFs
    etf_name_patterns = [
        "ETF", "FUND", "TRUST", "INDEX", "ISHARES", "VANGUARD",
        "2X", "3X", "ULTRA", "DIREXION", "PROSHARES"
    ]
    
    for pattern in etf_name_patterns:
        if pattern in name:
            return True
    
    return False


def ensure_directory(path: str) -> Path:
    """Ensure directory exists, create if not"""
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


class RetryableError(Exception):
    """Exception that should trigger retry logic"""
    pass


class FatalError(Exception):
    """Exception that should stop processing"""
    pass
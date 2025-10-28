#!/usr/bin/env python3
"""
EDGE-10 v1.0 - Advanced Trading System

This package implements a sophisticated stock ranking system using:
- Yahoo Finance data (free)
- SEC filings monitoring 
- Finnhub news sentiment (optional)
- Technical analysis with ATR, relative volume, sector strength
- EdgeScore algorithm with multiple weighted factors

Usage:
    python -m edge10.cli_universe_rank --csv data/all_instruments_capital.csv --date 2025-10-27
"""

__version__ = "1.0.0"
__author__ = "GitHub Copilot & ChatGPT"

from .utils import get_logger
from .datasources import get_history_yahoo, get_yahoo_info, get_sector_etf
from .features import calculate_features
from .scoring import calculate_edge_score  
from .ranking import rank_universe

__all__ = [
    "get_logger",
    "get_history_yahoo", 
    "get_yahoo_info",
    "get_sector_etf",
    "calculate_features",
    "calculate_edge_score",
    "rank_universe"
]
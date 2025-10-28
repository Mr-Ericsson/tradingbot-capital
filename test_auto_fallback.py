#!/usr/bin/env python3
"""
Test Auto-Fallback Datum Testing
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from universe_run_optimized import test_date_with_fallback, setup_logging


def test_fallback():
    """Testa auto-fallback funktionaliteten"""
    logger = setup_logging()

    # Test cases
    test_cases = [
        "2025-10-28",  # Idag (tisdag) - borde fallback till måndag
        "2025-10-27",  # Måndag - borde fungera
        "2025-10-26",  # Söndag - borde fallback till fredag
        "2025-10-25",  # Fredag - borde fungera
    ]

    for test_date in test_cases:
        logger.info(f"\n" + "=" * 60)
        logger.info(f"Testing datum: {test_date}")
        result = test_date_with_fallback(test_date)
        logger.info(f"Result: {result}")


if __name__ == "__main__":
    test_fallback()

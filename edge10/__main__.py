#!/usr/bin/env python3
"""
EDGE-10 CLI entry point

Allows running the ranking system as a module:
    python -m edge10 --csv data/instruments.csv --date 2025-10-27
"""

from .cli_universe_rank import main

if __name__ == "__main__":
    main()
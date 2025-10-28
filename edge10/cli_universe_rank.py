#!/usr/bin/env python3
"""
Command-line interface for EDGE-10 universe ranking

Usage:
    python -m edge10.cli_universe_rank --csv data/all_instruments_capital.csv --date 2025-10-27
    python -m edge10.cli_universe_rank --csv data/all_instruments_capital.csv --date 2025-10-27 --output custom_output --min-relvol 1.4
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .ranking import rank_universe
from .utils import get_logger

logger = get_logger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string in format YYYY-MM-DD"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: {date_str}. Use YYYY-MM-DD"
        )


def validate_csv_file(csv_path: str) -> str:
    """Validate CSV file exists and is readable"""
    path = Path(csv_path)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"CSV file not found: {csv_path}")
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Not a file: {csv_path}")
    if not csv_path.lower().endswith(".csv"):
        raise argparse.ArgumentTypeError(f"File must be a CSV: {csv_path}")
    return str(path.absolute())


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="EDGE-10 Universe Ranking System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic ranking with default settings
  python -m edge10.cli_universe_rank --csv data/all_instruments_capital.csv --date 2025-10-27
  
  # Custom output directory and RelVol threshold
  python -m edge10.cli_universe_rank --csv instruments.csv --date 2025-10-27 --output results --min-relvol 1.4
  
  # Disable RelVol relaxation
  python -m edge10.cli_universe_rank --csv instruments.csv --date 2025-10-27 --no-relax-relvol
        """,
    )

    # Required arguments
    parser.add_argument(
        "--csv",
        required=True,
        type=validate_csv_file,
        help="Path to Capital.com instruments CSV file (required columns: epic, ticker, name)",
    )

    parser.add_argument(
        "--date",
        required=True,
        type=parse_date,
        help="Analysis date in YYYY-MM-DD format",
    )

    # Optional arguments
    parser.add_argument(
        "--output", default="out", help="Output directory for results (default: out)"
    )

    parser.add_argument(
        "--min-relvol",
        type=float,
        default=1.30,
        help="Minimum relative volume threshold (default: 1.30)",
    )

    parser.add_argument(
        "--no-relax-relvol",
        action="store_true",
        help="Disable RelVol threshold relaxation if too few candidates",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    # Parse arguments
    args = parser.parse_args()

    # Configure logging level
    if args.debug:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    elif args.verbose:
        import logging

        logging.getLogger().setLevel(logging.INFO)
        logger.info("Verbose logging enabled")

    # Validate arguments
    if args.min_relvol < 1.0:
        logger.error("min-relvol must be >= 1.0")
        sys.exit(1)

    if args.min_relvol > 3.0:
        logger.warning(
            f"Very high min-relvol ({args.min_relvol}) may result in no candidates"
        )

    # Show configuration
    logger.info("EDGE-10 Universe Ranking Starting")
    logger.info("=" * 50)
    logger.info(f"CSV File: {args.csv}")
    logger.info(f"Analysis Date: {args.date}")
    logger.info(f"Output Directory: {args.output}")
    logger.info(f"Min RelVol: {args.min_relvol}")
    logger.info(
        f"RelVol Relaxation: {'disabled' if args.no_relax_relvol else 'enabled'}"
    )
    logger.info("=" * 50)

    try:
        # Run ranking pipeline
        top_100_file, top_10_file = rank_universe(
            csv_file=args.csv,
            analysis_date=args.date,
            output_dir=args.output,
            min_relvol=args.min_relvol,
            relax_relvol=not args.no_relax_relvol,
        )

        # Success message
        print("\n" + "=" * 60)
        print("🎯 EDGE-10 RANKING COMPLETE!")
        print("=" * 60)
        print(f"📈 TOP-100 results: {top_100_file}")
        print(f"🏆 TOP-10 results:  {top_10_file}")
        print("=" * 60)
        print("✅ Ready for trading! Use TOP-10 file for order placement.")

        sys.exit(0)

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Ranking failed: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

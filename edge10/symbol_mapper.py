"""
EDGE-10 Symbol Mapper
Smart mapping between Capital.com epics and Yahoo Finance symbols.

Features:
- Static mapping table for known symbols
- Smart pattern matching for variations (e.g., "US.TSLA" → "TSLA")
- Validation against Yahoo Finance
- Support for multiple data sources
- Automatic mapping table updates
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class SymbolMapper:
    """Maps Capital.com epics to Yahoo Finance symbols."""

    def __init__(self, mapping_file: str = "data/symbol_mapping.json"):
        self.mapping_file = Path(mapping_file)
        self.mapping = self._load_mapping()
        self.validation_cache = {}  # Cache for validated symbols

    def _load_mapping(self) -> Dict[str, str]:
        """Load symbol mapping from JSON file."""
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load mapping file: {e}")

        # Return empty mapping if file doesn't exist
        return {}

    def _save_mapping(self):
        """Save mapping to JSON file."""
        try:
            self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.mapping_file, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(self.mapping)} mappings to {self.mapping_file}")
        except Exception as e:
            logger.error(f"Failed to save mapping: {e}")

    def _smart_symbol_guess(self, capital_epic: str) -> List[str]:
        """Generate smart guesses for Yahoo Finance symbol."""
        guesses = []
        epic = capital_epic.upper()

        # Remove common prefixes
        patterns = [
            r"^US\.(.+)$",  # "US.TSLA" → "TSLA"
            r"^USA\.(.+)$",  # "USA.AAPL" → "AAPL"
            r"^NYSE\.(.+)$",  # "NYSE.MSFT" → "MSFT"
            r"^NASDAQ\.(.+)$",  # "NASDAQ.GOOGL" → "GOOGL"
        ]

        for pattern in patterns:
            match = re.match(pattern, epic)
            if match:
                guesses.append(match.group(1))

        # Add original epic as fallback
        if epic not in guesses:
            guesses.append(epic)

        # Common variations
        if "." in epic:
            # Try without dot
            no_dot = epic.replace(".", "")
            if no_dot not in guesses:
                guesses.append(no_dot)

        return guesses

    def _validate_yahoo_symbol(self, symbol: str) -> bool:
        """Validate if symbol exists on Yahoo Finance."""
        if symbol in self.validation_cache:
            return self.validation_cache[symbol]

        try:
            ticker = yf.Ticker(symbol)
            # Try to get basic info - if it fails, symbol doesn't exist
            info = ticker.info

            # Check if we got actual data
            is_valid = info and info.get("symbol") == symbol and "longName" in info

            self.validation_cache[symbol] = is_valid
            return is_valid

        except Exception:
            self.validation_cache[symbol] = False
            return False

    def map_symbol(self, capital_epic: str, validate: bool = True) -> Optional[str]:
        """
        Map Capital.com epic to Yahoo Finance symbol.

        Args:
            capital_epic: Capital.com epic (e.g., "US.TSLA", "AAPL")
            validate: Whether to validate against Yahoo Finance

        Returns:
            Yahoo Finance symbol or None if not found
        """
        # Check existing mapping first
        if capital_epic in self.mapping:
            symbol = self.mapping[capital_epic]
            if not validate or self._validate_yahoo_symbol(symbol):
                return symbol
            else:
                # Remove invalid mapping
                logger.warning(f"Removing invalid mapping: {capital_epic} → {symbol}")
                del self.mapping[capital_epic]

        # Try smart guessing
        guesses = self._smart_symbol_guess(capital_epic)

        for guess in guesses:
            if validate and not self._validate_yahoo_symbol(guess):
                continue

            # Found valid mapping - save it
            self.mapping[capital_epic] = guess
            logger.info(f"New mapping: {capital_epic} → {guess}")
            return guess

        # No valid mapping found
        logger.warning(f"No valid Yahoo symbol found for: {capital_epic}")
        return None

    def batch_map_symbols(
        self, capital_epics: List[str], validate: bool = True
    ) -> Dict[str, Optional[str]]:
        """
        Map multiple symbols at once.

        Args:
            capital_epics: List of Capital.com epics
            validate: Whether to validate against Yahoo Finance

        Returns:
            Dictionary mapping epic → yahoo_symbol (or None)
        """
        results = {}
        new_mappings = 0

        for epic in capital_epics:
            result = self.map_symbol(epic, validate)
            results[epic] = result

            if result and epic not in self.mapping:
                new_mappings += 1

        # Save mappings if we found new ones
        if new_mappings > 0:
            self._save_mapping()
            logger.info(f"Added {new_mappings} new symbol mappings")

        return results

    def get_mapping_stats(self) -> Dict[str, int]:
        """Get statistics about current mapping."""
        total_mappings = len(self.mapping)
        validated_count = sum(
            1
            for epic, symbol in self.mapping.items()
            if self._validate_yahoo_symbol(symbol)
        )

        return {
            "total_mappings": total_mappings,
            "validated_mappings": validated_count,
            "invalid_mappings": total_mappings - validated_count,
        }

    def cleanup_invalid_mappings(self):
        """Remove invalid mappings from the table."""
        invalid_epics = []

        for epic, symbol in self.mapping.items():
            if not self._validate_yahoo_symbol(symbol):
                invalid_epics.append(epic)

        for epic in invalid_epics:
            del self.mapping[epic]

        if invalid_epics:
            self._save_mapping()
            logger.info(f"Cleaned up {len(invalid_epics)} invalid mappings")

        return len(invalid_epics)


def create_initial_mapping_from_capital_data(
    csv_file: str, output_file: str = "data/symbol_mapping.json"
):
    """
    Create initial symbol mapping by analyzing Capital.com data.

    Args:
        csv_file: Path to Capital.com instruments CSV
        output_file: Path to save mapping JSON
    """
    logger.info(f"Creating initial mapping from {csv_file}")

    # Load Capital.com data
    df = pd.read_csv(csv_file)

    # Filter to likely US stocks
    us_stocks = df[
        (df.get("is_us_stock", False) == True)
        | (df["epic"].str.match(r"^[A-Z]{1,5}$"))  # Simple symbols
        | (df["epic"].str.match(r"^US\.[A-Z]+$"))  # US.* symbols
    ].copy()

    logger.info(f"Found {len(us_stocks)} potential US stocks")

    # Create mapper and process
    mapper = SymbolMapper(output_file)
    epics = us_stocks["epic"].tolist()

    # Process in batches to avoid hitting Yahoo too hard
    batch_size = 50
    all_results = {}

    for i in range(0, len(epics), batch_size):
        batch = epics[i : i + batch_size]
        logger.info(
            f"Processing batch {i//batch_size + 1}/{(len(epics)-1)//batch_size + 1}"
        )

        batch_results = mapper.batch_map_symbols(batch, validate=True)
        all_results.update(batch_results)

        # Small delay between batches
        import time

        time.sleep(1)

    # Print results
    successful = sum(1 for v in all_results.values() if v is not None)
    logger.info(
        f"Mapping complete: {successful}/{len(epics)} symbols mapped successfully"
    )

    return mapper, all_results


if __name__ == "__main__":
    # Test the mapper
    mapper = SymbolMapper()

    test_epics = ["US.TSLA", "AAPL", "MSFT", "GOOGL", "INVALID_SYMBOL"]
    results = mapper.batch_map_symbols(test_epics)

    print("Test results:")
    for epic, symbol in results.items():
        status = "✅" if symbol else "❌"
        print(f"  {epic} → {symbol} {status}")

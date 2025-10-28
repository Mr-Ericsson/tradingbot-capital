#!/usr/bin/env python3
"""
Auto-close all open positions 30 minutes before US market close.

Handles NYSE/Nasdaq calendar with holidays, early close days, and DST transitions.
Supports both demo and live accounts with robust error handling and comprehensive logging.
"""

import os
import sys
import time
import logging
import argparse
import signal
import csv
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import json
from dataclasses import dataclass

import requests
import pandas as pd
import pytz
from zoneinfo import ZoneInfo
import exchange_calendars as ecals
from dotenv import load_dotenv

# Import existing Capital.com client functionality
from capital_session import get_session

# Constants
BASE_URL_DEMO = "https://demo-api-capital.backend-capital.com"
BASE_URL_LIVE = "https://api-capital.backend-capital.com"
DEFAULT_CLOSE_OFFSET_MINUTES = 30
DEFAULT_POLL_INTERVAL_SECONDS = 30


@dataclass
class Config:
    """Configuration for auto-close service"""

    api_key: str
    account_mode: str  # 'demo' or 'live'
    market: str = "NYSE"
    close_offset_minutes: int = DEFAULT_CLOSE_OFFSET_MINUTES
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    dry_run: bool = False
    log_level: str = "INFO"


class CapitalCloseClient:
    """Capital.com client for position management"""

    def __init__(self, config: Config):
        self.config = config
        self.base_url = (
            BASE_URL_DEMO if config.account_mode == "demo" else BASE_URL_LIVE
        )
        self.session = None
        self.headers = None
        self._initialize_session()

    def _initialize_session(self):
        """Initialize Capital.com session and headers"""
        try:
            # Use existing session logic
            session_data = get_session()
            self.headers = {
                "X-CAP-API-KEY": self.config.api_key,
                "CST": session_data["CST"],
                "X-SECURITY-TOKEN": session_data["X-SECURITY-TOKEN"],
                "Content-Type": "application/json",
            }
            logging.info(
                f"Capital.com session initialized for {self.config.account_mode} account"
            )
        except Exception as e:
            logging.error(f"Failed to initialize Capital session: {e}")
            raise

    def get_open_positions(self) -> List[Dict]:
        """Fetch all open positions from Capital.com"""
        try:
            url = f"{self.base_url}/api/v1/positions"
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                positions = data.get("positions", [])
                logging.info(f"Retrieved {len(positions)} open positions")
                return positions
            else:
                logging.error(
                    f"Failed to fetch positions: {response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logging.error(f"Exception fetching positions: {e}")
            return []

    def close_position_market(
        self, epic: str, direction: str, size: float, position_id: str = None
    ) -> Dict:
        """Close a position using DELETE method or opposite market order"""

        # Try DELETE method first (common for closing positions)
        if position_id:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    url = f"{self.base_url}/api/v1/positions/{position_id}"
                    response = requests.delete(url, headers=self.headers, timeout=30)

                    if response.status_code in [200, 201, 204]:
                        return {
                            "status": "DELETED",
                            "dealId": position_id,
                            "method": "DELETE",
                        }
                    elif response.status_code == 429:
                        wait_time = (2**attempt) * 1
                        logging.warning(f"Rate limit on DELETE, waiting {wait_time}s")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.warning(
                            f"DELETE failed: {response.status_code} - {response.text}"
                        )
                        break

                except Exception as e:
                    logging.warning(f"DELETE attempt failed: {e}")
                    break

        # Fallback to POST method with opposite direction
        payload = {
            "epic": epic,
            "direction": direction,  # Opposite of current position
            "type": "MARKET",
            "size": abs(float(size)),  # Always positive
        }

        max_retries = 5
        for attempt in range(max_retries):
            try:
                url = f"{self.base_url}/api/v1/positions"
                response = requests.post(
                    url, json=payload, headers=self.headers, timeout=30
                )

                if response.status_code in [200, 201]:
                    result = response.json()
                    deal_reference = result.get("dealReference")

                    # Wait and get confirmation
                    time.sleep(1.0)
                    confirmation = self._get_deal_confirmation(deal_reference)

                    status = "UNKNOWN"
                    deal_id = None

                    if confirmation:
                        status = confirmation.get("dealStatus", "UNKNOWN")
                        affected_deals = confirmation.get("affectedDeals", [])
                        if affected_deals:
                            deal_id = affected_deals[0].get("dealId")

                    return {
                        "status": status,
                        "dealId": deal_id,
                        "dealReference": deal_reference,
                    }

                elif response.status_code == 429:
                    # Rate limit - exponential backoff
                    wait_time = (2**attempt) * 1
                    logging.warning(
                        f"Rate limit hit, waiting {wait_time}s before retry {attempt+1}/{max_retries}"
                    )
                    time.sleep(wait_time)
                    continue

                elif response.status_code >= 500:
                    # Server error - retry
                    wait_time = (2**attempt) * 2
                    logging.warning(
                        f"Server error {response.status_code}, waiting {wait_time}s before retry {attempt+1}/{max_retries}"
                    )
                    time.sleep(wait_time)
                    continue

                else:
                    # Client error - don't retry
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get("errorCode", response.text)
                    logging.error(
                        f"Client error closing {epic}: {response.status_code} - {error_msg}"
                    )
                    return {
                        "status": "CLIENT_ERROR",
                        "dealId": None,
                        "error": error_msg,
                    }

            except Exception as e:
                logging.error(f"Exception closing {epic} (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    return {"status": "EXCEPTION", "dealId": None, "error": str(e)}
                time.sleep(2**attempt)

        return {
            "status": "MAX_RETRIES_EXCEEDED",
            "dealId": None,
            "error": "Failed after all retries",
        }

    def _get_deal_confirmation(self, deal_reference: str) -> Optional[Dict]:
        """Get deal confirmation status"""
        try:
            url = f"{self.base_url}/api/v1/confirms/{deal_reference}"
            response = requests.get(url, headers=self.headers, timeout=15)

            if response.status_code == 200:
                return response.json()
            else:
                logging.warning(
                    f"Could not get confirmation for {deal_reference}: {response.status_code}"
                )
                return None

        except Exception as e:
            logging.warning(f"Exception getting confirmation for {deal_reference}: {e}")
            return None


class MarketScheduler:
    """Handle NYSE market schedule calculations"""

    def __init__(self, market: str = "NYSE"):
        self.market = market
        try:
            self.calendar = ecals.get_calendar("XNYS")  # NYSE calendar
            logging.info(f"Initialized {market} calendar")
        except Exception as e:
            logging.error(f"Failed to initialize market calendar: {e}")
            raise

    def get_next_trigger_time(
        self, now_et: datetime, offset_minutes: int
    ) -> Tuple[datetime, datetime]:
        """
        Calculate next market close trigger time.

        Returns:
            (trigger_time_et, market_close_et)
        """
        try:
            # Get trading sessions for next 10 days
            start_date = now_et.date()
            end_date = now_et.date() + timedelta(days=10)

            sessions = self.calendar.sessions_in_range(start_date, end_date)

            # Find next trading session with market_close > now_et
            for session_date in sessions:
                market_close_utc = self.calendar.session_close(session_date)
                market_close_et = market_close_utc.tz_convert("America/New_York")

                if market_close_et > now_et:
                    trigger_time_et = market_close_et - timedelta(
                        minutes=offset_minutes
                    )
                    logging.debug(
                        f"Next market close: {market_close_et}, trigger: {trigger_time_et}"
                    )
                    return trigger_time_et, market_close_et

            # Fallback - should not happen with 10-day lookahead
            raise Exception("No trading sessions found in next 10 days")

        except Exception as e:
            logging.error(f"Error calculating next trigger time: {e}")
            raise

    def is_trading_day(self, date_obj: date) -> bool:
        """Check if given date is a trading day"""
        try:
            return self.calendar.is_session(date_obj)
        except:
            return False


class AutoCloseService:
    """Main service for auto-closing positions"""

    def __init__(self, config: Config):
        self.config = config
        self.capital_client = CapitalCloseClient(config)
        self.scheduler = MarketScheduler(config.market)
        self.running = True
        self._setup_signal_handlers()
        self._setup_logging()

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""

        def signal_handler(signum, frame):
            logging.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _setup_logging(self):
        """Setup logging to both file and console"""
        # Create logs directory
        Path("logs").mkdir(exist_ok=True)

        # Configure logging
        log_format = "%(asctime)s - %(levelname)s - %(message)s"
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format=log_format,
            handlers=[
                logging.FileHandler("logs/auto_close_positions.log"),
                logging.StreamHandler(sys.stdout),
            ],
        )

    def run(self):
        """Main service loop"""
        logging.info("=== Auto-Close Positions Service Started ===")
        logging.info(f"Account: {self.config.account_mode}")
        logging.info(f"Market: {self.config.market}")
        logging.info(f"Close offset: {self.config.close_offset_minutes} minutes")
        logging.info(f"Poll interval: {self.config.poll_interval_seconds} seconds")
        logging.info(f"Dry run: {self.config.dry_run}")

        while self.running:
            try:
                # Calculate next trigger time
                now_et = datetime.now(ZoneInfo("America/New_York"))
                now_local = datetime.now(ZoneInfo("Europe/Stockholm"))

                trigger_time_et, market_close_et = self.scheduler.get_next_trigger_time(
                    now_et, self.config.close_offset_minutes
                )

                trigger_time_local = trigger_time_et.astimezone(
                    ZoneInfo("Europe/Stockholm")
                )
                market_close_local = market_close_et.astimezone(
                    ZoneInfo("Europe/Stockholm")
                )

                logging.info(
                    f"Current time: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')} | {now_local.strftime('%H:%M:%S %Z')}"
                )
                logging.info(
                    f"Next trigger: {trigger_time_et.strftime('%Y-%m-%d %H:%M:%S %Z')} | {trigger_time_local.strftime('%H:%M:%S %Z')}"
                )
                logging.info(
                    f"Market close: {market_close_et.strftime('%Y-%m-%d %H:%M:%S %Z')} | {market_close_local.strftime('%H:%M:%S %Z')}"
                )

                # Wait until trigger time
                if now_et >= trigger_time_et:
                    # Check if market is still open (not past close time)
                    if now_et >= market_close_et:
                        logging.info(
                            "🏁 Market is closed, moving to next trading day..."
                        )
                        # Sleep until next trading day
                        time.sleep(3600)  # Sleep 1 hour then recalculate
                        continue

                    logging.info(
                        "🔥 TRIGGER TIME REACHED - Starting position closure..."
                    )
                    self._close_all_positions()

                    # After closure, wait until market closes then move to next day
                    logging.info(
                        "✅ Position closure completed. Waiting until market close..."
                    )
                    remaining_time = (market_close_et - now_et).total_seconds()
                    if remaining_time > 0:
                        time.sleep(
                            min(remaining_time + 60, 3600)
                        )  # Wait until close + buffer
                    continue
                else:
                    # Sleep until next poll or trigger
                    sleep_seconds = min(
                        self.config.poll_interval_seconds,
                        int((trigger_time_et - now_et).total_seconds()),
                    )

                    if sleep_seconds > 0:
                        logging.info(
                            f"Waiting {sleep_seconds} seconds until next check..."
                        )
                        time.sleep(sleep_seconds)

            except KeyboardInterrupt:
                logging.info("Keyboard interrupt received, shutting down...")
                break
            except Exception as e:
                logging.error(f"Error in main loop: {e}")
                time.sleep(60)  # Wait before retrying

        logging.info("=== Auto-Close Positions Service Stopped ===")

    def _close_all_positions(self):
        """Close all open positions"""
        try:
            # Get all open positions
            positions = self.capital_client.get_open_positions()

            if not positions:
                logging.info("📭 No open positions to close")
                self._log_close_run([], "NO_POSITIONS")
                return

            logging.info(f"📊 Found {len(positions)} open positions to close")

            # Prepare results tracking
            close_results = []
            successful_closes = 0
            failed_closes = 0

            # Close each position
            for position in positions:
                # Data är i nested objekt
                position_data = position.get("position", {})
                market_data = position.get("market", {})

                epic = market_data.get("epic", "UNKNOWN")
                current_direction = position_data.get("direction", "UNKNOWN")
                size = abs(float(position_data.get("size", 0)))
                unrealized_pnl = position_data.get("upl", 0)
                position_id = position_data.get("dealId")

                # Determine closing direction (opposite of current)
                close_direction = "SELL" if current_direction == "BUY" else "BUY"

                logging.info(
                    f"🔄 Closing {epic}: {size} shares ({current_direction} → {close_direction}), PnL: {unrealized_pnl}, ID: {position_id}"
                )

                if self.config.dry_run:
                    logging.info(
                        f"🧪 DRY RUN: Would close {epic} {size} shares via DELETE"
                    )
                    close_results.append(
                        {
                            "epic": epic,
                            "direction": close_direction,
                            "size": size,
                            "status": "DRY_RUN",
                            "deal_id": "DRY_RUN",
                            "unrealized_pnl": unrealized_pnl,
                            "notes": "dry_run_mode",
                        }
                    )
                    successful_closes += 1
                else:
                    # Execute actual close with position ID
                    result = self.capital_client.close_position_market(
                        epic, close_direction, size, position_id
                    )

                    close_results.append(
                        {
                            "epic": epic,
                            "direction": close_direction,
                            "size": size,
                            "status": result["status"],
                            "deal_id": result.get("dealId", ""),
                            "unrealized_pnl": unrealized_pnl,
                            "notes": result.get("error", ""),
                        }
                    )

                    if result["status"] in ["ACCEPTED", "OPEN", "EXECUTED", "DELETED"]:
                        successful_closes += 1
                        logging.info(
                            f"✅ Successfully closed {epic}, Deal ID: {result.get('dealId')}"
                        )
                    elif (
                        result["status"] == "DELETED"
                        and "already" in result.get("error", "").lower()
                    ):
                        # Position was already closed - count as success
                        successful_closes += 1
                        logging.info(f"✅ {epic} was already closed")
                    else:
                        failed_closes += 1
                        logging.error(
                            f"❌ Failed to close {epic}: {result['status']} - {result.get('error', '')}"
                        )

                    # Small delay between closes to avoid rate limits
                    time.sleep(0.5)

            # Log summary
            total_positions = len(positions)
            logging.info(
                f"📈 CLOSURE SUMMARY: {successful_closes}/{total_positions} successful, {failed_closes} failed"
            )

            # Save detailed CSV log
            self._log_close_run(close_results, "COMPLETED")

        except Exception as e:
            logging.error(f"Error in close_all_positions: {e}")
            self._log_close_run([], f"ERROR: {str(e)}")

    def _log_close_run(self, results: List[Dict], run_status: str):
        """Log closure run to CSV file"""
        try:
            # Create logs directory
            Path("logs").mkdir(exist_ok=True)

            # CSV filename with date
            today = datetime.now().strftime("%Y%m%d")
            csv_file = f"logs/close_run_{today}.csv"

            # Check if file exists to determine if we need headers
            file_exists = Path(csv_file).exists()

            with open(csv_file, "a", newline="", encoding="utf-8") as f:
                fieldnames = [
                    "utc_timestamp",
                    "account_mode",
                    "run_status",
                    "epic",
                    "direction",
                    "size",
                    "status",
                    "deal_id",
                    "unrealized_pnl",
                    "notes",
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)

                if not file_exists:
                    writer.writeheader()

                utc_timestamp = datetime.utcnow().isoformat()

                if not results:
                    # Log single row for no positions or error
                    writer.writerow(
                        {
                            "utc_timestamp": utc_timestamp,
                            "account_mode": self.config.account_mode,
                            "run_status": run_status,
                            "epic": "",
                            "direction": "",
                            "size": 0,
                            "status": run_status,
                            "deal_id": "",
                            "unrealized_pnl": 0,
                            "notes": (
                                "no_positions"
                                if run_status == "NO_POSITIONS"
                                else run_status
                            ),
                        }
                    )
                else:
                    # Log each position closure
                    for result in results:
                        writer.writerow(
                            {
                                "utc_timestamp": utc_timestamp,
                                "account_mode": self.config.account_mode,
                                "run_status": run_status,
                                "epic": result["epic"],
                                "direction": result["direction"],
                                "size": result["size"],
                                "status": result["status"],
                                "deal_id": result["deal_id"],
                                "unrealized_pnl": result["unrealized_pnl"],
                                "notes": result["notes"],
                            }
                        )

            logging.info(f"📝 Closure run logged to: {csv_file}")

        except Exception as e:
            logging.error(f"Failed to log close run to CSV: {e}")


def load_config() -> Config:
    """Load configuration from environment and CLI args"""
    # Load .env file if it exists
    load_dotenv()

    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Auto-close all positions before US market close"
    )
    parser.add_argument(
        "--account-mode",
        default=os.getenv("CAPITAL_ACCOUNT_MODE", "demo"),
        choices=["demo", "live"],
        help="Account mode",
    )
    parser.add_argument(
        "--close-offset",
        type=int,
        default=int(os.getenv("CLOSE_OFFSET_MINUTES", DEFAULT_CLOSE_OFFSET_MINUTES)),
        help="Minutes before market close to trigger closure",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.getenv("POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)),
        help="Seconds between schedule checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "false").lower() == "true",
        help="Log what would be closed without executing",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Get API credentials
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise ValueError("API_KEY environment variable required")

    return Config(
        api_key=api_key,
        account_mode=args.account_mode,
        market=os.getenv("MARKET", "NYSE"),
        close_offset_minutes=args.close_offset,
        poll_interval_seconds=args.poll_interval,
        dry_run=args.dry_run,
        log_level=args.log_level,
    )


def main():
    """Main entry point"""
    try:
        config = load_config()
        service = AutoCloseService(config)
        service.run()
    except KeyboardInterrupt:
        logging.info("Service interrupted by user")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Service failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

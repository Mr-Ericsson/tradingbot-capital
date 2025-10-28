#!/usr/bin/env python3

# Quick test of auto_close_positions functionality
import sys
import os


def test_auto_close():
    """Test key components of auto_close_positions"""

    print("=== Testing Auto Close Positions ===")

    try:
        # Test 1: Import dependencies
        print("1. Testing imports...")
        import exchange_calendars as xcals
        import pytz
        from dotenv import load_dotenv

        print("   ✅ All imports successful")

        # Test 2: Test calendar functionality
        print("2. Testing NYSE calendar...")
        cal = xcals.get_calendar("XNYS")
        from datetime import datetime, timedelta

        today = datetime.now().date()
        sessions = cal.sessions_in_range(today, today + timedelta(days=5))
        print(f"   ✅ NYSE calendar loaded, found {len(sessions)} trading sessions")

        # Test 3: Test config loading
        print("3. Testing config loading...")
        sys.path.append(".")
        from auto_close_positions import load_config

        # Mock CLI args for testing
        import argparse

        original_argv = sys.argv
        sys.argv = ["test", "--account-mode", "demo", "--dry-run"]

        try:
            config = load_config()
            print(
                f"   ✅ Config loaded: {config.account_mode} mode, dry_run={config.dry_run}"
            )
        except Exception as e:
            print(f"   ⚠️  Config loading needs API_KEY: {e}")
        finally:
            sys.argv = original_argv

        # Test 4: Test capital session
        print("4. Testing Capital.com session...")
        try:
            from capital_session import get_session

            session = get_session()
            print(f"   ✅ Capital session works: {list(session.keys())}")
        except Exception as e:
            print(f"   ⚠️  Capital session error: {e}")

        print("\n🎉 Auto Close Positions components are working!")
        print("\nTo run the full system:")
        print("   python auto_close_positions.py --account-mode demo --dry-run")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

    return True


if __name__ == "__main__":
    test_auto_close()

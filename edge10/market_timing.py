#!/usr/bin/env python3
"""
EDGE-10 Market Timing Utilities
Korrekt US-marknadstiming med svensk DST (CET/CEST) hantering
"""

import pytz
import pandas as pd
import exchange_calendars as xcals
from datetime import datetime, timedelta, time
from typing import Tuple, Optional


# Timezone setup
NY_TZ = pytz.timezone("America/New_York")
SE_TZ = pytz.timezone("Europe/Stockholm")

def get_nyse_calendar():
    """Hämta NYSE kalender"""
    return xcals.get_calendar("XNYS")


def is_us_market_open_now() -> bool:
    """
    Kontrollera om US marknaden är öppen just nu
    Använder NYSE kalender för exakt timing
    """
    try:
        cal = get_nyse_calendar()
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        return cal.is_open_at_time(now_utc)
    except Exception:
        # Fallback: enkel tid-check (09:30-16:00 ET)
        now_et = datetime.now(NY_TZ)
        market_open = time(9, 30)  # 09:30 ET
        market_close = time(16, 0)  # 16:00 ET
        current_time = now_et.time()
        
        # Undvik helger
        if now_et.weekday() >= 5:  # Lördag/Söndag
            return False
            
        return market_open <= current_time <= market_close


def next_open_close_se_times() -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Hitta nästa marknad öppning/stängning i svensk tid
    Returnerar (open_se, close_se) som datetime objekt i Europe/Stockholm
    """
    try:
        cal = get_nyse_calendar()
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        
        # Sök sessions inom nästa 5 dagar
        end_search = now_utc.date() + timedelta(days=5)
        sessions = cal.sessions_in_range(now_utc.date(), end_search)
        
        for session in sessions:
            # Hämta öppning och stängning i UTC
            open_utc = cal.session_open(session)
            close_utc = cal.session_close(session)
            
            # Om stängning är i framtiden, är detta vår nästa session
            if close_utc > now_utc:
                open_se = open_utc.astimezone(SE_TZ)
                close_se = close_utc.astimezone(SE_TZ)
                return open_se, close_se
        
        return None, None
        
    except Exception:
        # Fallback: approximation baserat på standard trading hours
        now_se = datetime.now(SE_TZ)
        
        # Standard US market: 09:30-16:00 ET = 15:30-22:00 CET (vinter) eller 14:30-21:00 CEST (sommar)
        # Approximera baserat på aktuell tid
        for days_ahead in range(5):
            check_date = now_se.date() + timedelta(days=days_ahead)
            
            # Skippa helger
            if check_date.weekday() >= 5:
                continue
            
            # Approximera öppning/stängning (justeras automatiskt för DST)
            open_et = NY_TZ.localize(datetime.combine(check_date, time(9, 30)))
            close_et = NY_TZ.localize(datetime.combine(check_date, time(16, 0)))
            
            open_se = open_et.astimezone(SE_TZ)
            close_se = close_et.astimezone(SE_TZ)
            
            # Om denna stängning är i framtiden
            if close_se > now_se:
                return open_se, close_se
        
        return None, None


def within_open_window_se() -> bool:
    """
    Kontrollera om vi är inom marknadens öppettider (svensk tid)
    """
    try:
        open_se, close_se = next_open_close_se_times()
        if not open_se or not close_se:
            return False
            
        now_se = datetime.now(SE_TZ)
        
        # Kontrollera om samma dag och inom fönster
        if (open_se.date() == now_se.date() and 
            open_se <= now_se <= close_se):
            return True
            
        return False
        
    except Exception:
        return False


def get_auto_close_trigger_se(minutes_before_close: int = 30) -> Optional[datetime]:
    """
    Beräkna auto-close trigger tid i svensk tid
    Default: 30 minuter före marknadsstängning
    """
    try:
        open_se, close_se = next_open_close_se_times()
        if not close_se:
            return None
            
        trigger_se = close_se - timedelta(minutes=minutes_before_close)
        return trigger_se
        
    except Exception:
        return None


def market_status_summary() -> dict:
    """
    Sammanfattning av marknadsstatus för debugging/logging
    """
    try:
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        now_et = now_utc.astimezone(NY_TZ)
        now_se = now_utc.astimezone(SE_TZ)
        
        open_se, close_se = next_open_close_se_times()
        trigger_se = get_auto_close_trigger_se()
        
        return {
            "current_time_et": now_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "current_time_se": now_se.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "is_market_open": is_us_market_open_now(),
            "within_trading_window": within_open_window_se(),
            "next_open_se": open_se.strftime("%Y-%m-%d %H:%M:%S %Z") if open_se else None,
            "next_close_se": close_se.strftime("%Y-%m-%d %H:%M:%S %Z") if close_se else None,
            "auto_close_trigger_se": trigger_se.strftime("%Y-%m-%d %H:%M:%S %Z") if trigger_se else None,
        }
        
    except Exception as e:
        return {"error": str(e)}


def wait_for_market_open(timeout_minutes: int = 60) -> bool:
    """
    Vänta tills marknaden öppnar (med timeout)
    Returnerar True om marknaden öppnade, False vid timeout
    """
    start_time = datetime.utcnow()
    timeout_time = start_time + timedelta(minutes=timeout_minutes)
    
    while datetime.utcnow() < timeout_time:
        if is_us_market_open_now():
            return True
        
        # Vänta 30 sekunder innan nästa check
        import time
        time.sleep(30)
    
    return False


if __name__ == "__main__":
    # Test av alla funktioner
    print("🇺🇸 US Market Timing Status:")
    print("=" * 50)
    
    status = market_status_summary()
    for key, value in status.items():
        if key != "error":
            print(f"{key:25}: {value}")
    
    print("\n🔍 Quick Tests:")
    print(f"Market Open Now:     {is_us_market_open_now()}")
    print(f"Within Trading Window: {within_open_window_se()}")
    
    trigger = get_auto_close_trigger_se()
    if trigger:
        print(f"Auto-close Trigger:  {trigger.strftime('%H:%M')} svensk tid")
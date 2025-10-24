import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
LOGIN_ID = os.getenv("LOGIN_ID")
PASSWORD = os.getenv("PASSWORD")

_session_cache = None  # återanvänd session


def get_session():
    global _session_cache

    # Återanvänd session om vi redan är inloggade
    if _session_cache:
        return _session_cache

    url = f"{BASE_URL}/api/v1/session"
    headers = {"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"}
    data = {"identifier": LOGIN_ID, "password": PASSWORD}

    for attempt in range(3):
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            _session_cache = {
                "CST": response.headers.get("CST"),
                "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
            }
            return _session_cache
        elif response.status_code == 429:  # för många requests
            wait_time = 2 * (attempt + 1)
            print(f"[LOGIN] Rate limited 🚫 – försöker igen om {wait_time} sek...")
            time.sleep(wait_time)
        else:
            raise Exception(f"Login failed: {response.text}")

    raise Exception("Login failed after retries")

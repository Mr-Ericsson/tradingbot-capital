#!/usr/bin/env python3
"""
QUICK EMERGENCY CLOSE - Stäng alla positioner NU!
"""

import sys

sys.path.append("src")
from brokers.capitalcom.session import get_session
import requests
import os
from dotenv import load_dotenv
import time

load_dotenv()


def emergency_close_all():
    """Stäng alla positioner snabbt"""

    print("🚨 EMERGENCY CLOSE - Stänger alla positioner!")

    # Get session
    session_data = get_session()
    headers = {
        "X-CAP-API-KEY": os.getenv("API_KEY"),
        "CST": session_data["CST"],
        "X-SECURITY-TOKEN": session_data["X-SECURITY-TOKEN"],
        "Content-Type": "application/json",
    }

    base_url = os.getenv("BASE_URL")

    # Get positions
    print("📊 Hämtar positioner...")
    url = f"{base_url}/api/v1/positions"
    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        print(
            f"❌ Kunde inte hämta positioner: {response.status_code} - {response.text}"
        )
        return

    data = response.json()
    positions = data.get("positions", [])
    print(f"📈 Hittade {len(positions)} positioner")

    if not positions:
        print("✅ Inga positioner att stänga!")
        return

    # Close each position using different methods
    for i, pos in enumerate(positions):
        # Data är i nested objekt
        position_data = pos.get("position", {})
        market_data = pos.get("market", {})

        epic = market_data.get("epic")
        direction = position_data.get("direction")
        size = position_data.get("size")
        position_id = position_data.get("dealId")

        print(f"\n🔄 [{i+1}/{len(positions)}] Stänger {epic}")
        print(f"   Direction: {direction}, Size: {size}, ID: {position_id}")

        if not epic or not direction or size is None:
            print("   ❌ Saknar nödvändig data, hoppar över...")
            continue

        # Method 1: Try DELETE
        if position_id:
            try:
                delete_url = f"{base_url}/api/v1/positions/{position_id}"
                print(f"   Försöker DELETE: {delete_url}")
                response = requests.delete(delete_url, headers=headers, timeout=10)

                if response.status_code in [200, 201, 204]:
                    print(f"   ✅ DELETE lyckades!")
                    continue
                else:
                    print(f"   ⚠️ DELETE misslyckades: {response.status_code}")
            except Exception as e:
                print(f"   ⚠️ DELETE error: {e}")

        # Method 2: Try PUT to modify position
        if position_id:
            try:
                put_url = f"{base_url}/api/v1/positions/{position_id}"
                # Try to set size to 0 or reverse
                put_payload = {"size": 0}
                print(f"   Försöker PUT size=0")
                response = requests.put(
                    put_url, json=put_payload, headers=headers, timeout=10
                )

                if response.status_code in [200, 201]:
                    print(f"   ✅ PUT lyckades!")
                    continue
                else:
                    print(f"   ⚠️ PUT misslyckades: {response.status_code}")
            except Exception as e:
                print(f"   ⚠️ PUT error: {e}")

        # Method 3: Try POST opposite (without specifying size first)
        try:
            close_direction = "SELL" if direction == "BUY" else "BUY"

            # Try with just epic and direction first
            payload = {"epic": epic, "direction": close_direction, "type": "MARKET"}

            print(f"   Försöker POST utan size...")
            url = f"{base_url}/api/v1/positions"
            response = requests.post(url, json=payload, headers=headers, timeout=10)

            if response.status_code in [200, 201]:
                print(f"   ✅ POST utan size lyckades!")
                continue
            else:
                print(
                    f"   ⚠️ POST utan size: {response.status_code} - {response.text[:100]}"
                )

                # Try with absolute size
                payload["size"] = abs(float(size))
                print(f"   Försöker POST med size {payload['size']}...")
                response = requests.post(url, json=payload, headers=headers, timeout=10)

                if response.status_code in [200, 201]:
                    print(f"   ✅ POST med size lyckades!")
                else:
                    print(f"   ❌ POST med size misslyckades: {response.status_code}")
                    print(f"   Response: {response.text[:200]}")

        except Exception as e:
            print(f"   ❌ POST error: {e}")

        # Small delay between attempts
        time.sleep(0.5)

    print(f"\n🏁 Klar! Kontrollera dina positioner manuellt.")


if __name__ == "__main__":
    emergency_close_all()

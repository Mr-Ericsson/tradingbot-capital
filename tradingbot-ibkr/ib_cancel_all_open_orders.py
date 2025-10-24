# ib_cancel_all_open_orders.py
from ib_insync import IB
import time

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 990

ib = IB()
print("[connect]")
ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)

# Visa vad som ligger öppet just nu
oo = ib.openOrders()
print(f"[before] open orders: {len(oo)}")

# Skicka GLOBAL CANCEL (stoppar allt som är pending)
print("[action] reqGlobalCancel()")
ib.reqGlobalCancel()

# Vänta in TWS
deadline = time.time() + 10
while time.time() < deadline:
    ib.waitOnUpdate(timeout=1)

# Dubbelkolla
oo_after = ib.openOrders()
print(f"[after] open orders: {len(oo_after)}")

ib.disconnect()
print("[done]")

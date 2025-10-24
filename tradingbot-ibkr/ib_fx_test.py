from ib_insync import *
import time

# --- CONNECT ---
ib = IB()
ib.connect("127.0.0.1", 7497, clientId=1)  # 7497 = paper trading

print("[ib] connected:", ib.isConnected())

# --- KONTRAKT (FX EURUSD) ---
fx = Forex("EURUSD")

# --- ORDER ---
order = MarketOrder("BUY", 1000)  # Köp 1K EUR
trade = ib.placeOrder(fx, order)
print("[order] Skickad, väntar fill...")

# --- VÄNTA PÅ FILL ---
while trade.orderStatus.status not in ("Filled", "Cancelled"):
    print("  status =", trade.orderStatus.status, "filled =", trade.orderStatus.filled)
    ib.sleep(1)

print("[result]", trade.orderStatus.status, "filled =", trade.orderStatus.filled)
ib.disconnect()

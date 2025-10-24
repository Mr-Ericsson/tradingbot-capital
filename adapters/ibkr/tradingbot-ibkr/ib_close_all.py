# ib_close_all.py
# Stänger ALLA öppna positioner (alla instrument) och avbryter alla öppna ordrar.
# Kör mot TWS Paper (port 7497).

from ib_insync import IB, MarketOrder
import time

HOST = "127.0.0.1"
PORT = 7497  # TWS Paper
CLIENT_ID = 902  # valfritt unikt
OUTSIDE_RTH = True  # True = kan handla i extended hours
TIMEOUT_SEC = 120

ib = IB()
print(f"[connect] {HOST}:{PORT} cid={CLIENT_ID}")
ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)

# 1) Avbryt alla öppna ordrar först (så inget krockar)
open_orders = ib.openOrders()
for o in open_orders:
    try:
        ib.cancelOrder(o)
    except Exception as e:
        print(f"[warn] cancel failed for orderId={getattr(o,'orderId',None)}: {e}")
ib.waitOnUpdate(timeout=1)
print(f"[info] cancelled open orders: {len(open_orders)}")

# 2) Hämta aktuella positioner
positions = ib.positions()
if not positions:
    print("[done] inga öppna positioner.")
    ib.disconnect()
    raise SystemExit(0)

print("[info] öppna positioner:")
for p in positions:
    print(
        f"  - {p.account} {p.contract.localSymbol or p.contract.symbol} qty={p.position} avg={p.avgCost}"
    )

# 3) Lägg stängningsordrar (MKT) för alla
trades = []
for p in positions:
    qty = abs(int(p.position))
    if qty == 0:
        continue
    action = "SELL" if p.position > 0 else "BUY"  # long -> SELL, short -> BUY
    order = MarketOrder(action, qty, outsideRth=OUTSIDE_RTH, tif="DAY", transmit=True)
    # Viktigt: använd kontraktet från positionen (rätt typ/primärbörs)
    print(
        f"[close] {p.contract.localSymbol or p.contract.symbol}: {action} {qty} @ MKT"
    )
    t = ib.placeOrder(p.contract, order)
    trades.append(t)

# 4) Vänta på fills (upp till TIMEOUT_SEC)
deadline = time.time() + TIMEOUT_SEC
while time.time() < deadline and any(
    t.orderStatus.status not in ("Filled", "ApiCancelled", "Cancelled") for t in trades
):
    ib.waitOnUpdate(timeout=1)

# 5) Summering
left = [
    t
    for t in trades
    if t.orderStatus.status not in ("Filled", "ApiCancelled", "Cancelled")
]
print(f"[summary] filled={len(trades)-len(left)} pending/cancelled={len(left)}")

# 6) Avbryt ev. kvarvarande ordrar ändå
for t in left:
    try:
        ib.cancelOrder(t.order)
    except Exception as e:
        print(f"[warn] late-cancel failed: {e}")
ib.waitOnUpdate(timeout=1)

# 7) Visa kvarvarande positioner
positions_after = ib.positions()
if positions_after:
    print("[note] positioner kvar:")
    for p in positions_after:
        print(f"  - {p.contract.localSymbol or p.contract.symbol} qty={p.position}")
else:
    print("[done] alla positioner stängda.")

ib.disconnect()

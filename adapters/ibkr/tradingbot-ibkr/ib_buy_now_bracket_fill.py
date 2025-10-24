# ib_buy_now_bracket_fill.py
# Köp nu @ MKT och lägg TP/SL = ±3% från faktiskt fillpris (med OCA så bara en av dem exekveras)

from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder
import time, math

HOST = "127.0.0.1"
PORT = 7497  # TWS Paper
CLIENT_ID = 201  # valfritt unikt heltal
SYMBOL = "TSLA"
QTY = 1
PCT = 0.03  # 3%
OUTSIDE_RTH = True  # sätt False om du vill blockera utanför RTH
FILL_TIMEOUT_SEC = 120


def round_to_min_tick(price: float, min_tick: float) -> float:
    mt = min_tick if (min_tick and min_tick > 0) else 0.01
    return round(price / mt) * mt


ib = IB()
print(f"[connect] {HOST}:{PORT} cid={CLIENT_ID}")
ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)

# 1) Kontrakt + minTick
contract = Stock(SYMBOL, "SMART", "USD")
[qc] = ib.qualifyContracts(contract)
cds = ib.reqContractDetails(qc)
min_tick = cds[0].minTick if (cds and cds[0].minTick) else 0.01

# 2) Lägg parent MARKETSORDER och TRANSMIT = True (annars fylls den aldrig)
parent = MarketOrder("BUY", QTY, outsideRth=OUTSIDE_RTH, tif="DAY", transmit=True)
parent.orderId = ib.client.getReqId()
print(f"[place] {SYMBOL} BUY {QTY} @ MKT (orderId={parent.orderId})")
trade = ib.placeOrder(qc, parent)

# 3) Vänta på fill (robust)
fill_price = None
deadline = time.time() + FILL_TIMEOUT_SEC
while time.time() < deadline:
    ib.waitOnUpdate(timeout=1)
    st = trade.orderStatus.status
    ap = trade.orderStatus.avgFillPrice
    if ap and st in ("Filled", "Partial"):
        fill_price = float(ap)
        break

# fallback: plocka från fills-listan om avgFill ej satt
if not fill_price and trade.fills:
    ex = trade.fills[-1].execution
    fill_price = float(ex.avgPrice or ex.price or 0)

if not fill_price or not math.isfinite(fill_price) or fill_price <= 0:
    print("[error] Fick inget fillpris på parent inom timeout – avbryter.")
    ib.disconnect()
    raise SystemExit(1)

print(f"[filled] {SYMBOL} avgFill={fill_price:.2f}")

# 4) Räkna TP/SL från fillpris
tp_price = round_to_min_tick(fill_price * (1 + PCT), min_tick)
sl_price = round_to_min_tick(fill_price * (1 - PCT), min_tick)
print(f"[bracket] TP={tp_price} | SL={sl_price} (minTick={min_tick})")

# 5) Lägg TP/SL som OCA-par (så bara en fylls)
oca = f"OCA-{parent.orderId}"
tp = LimitOrder("SELL", QTY, tp_price, outsideRth=OUTSIDE_RTH, tif="DAY", transmit=True)
sl = StopOrder("SELL", QTY, sl_price, outsideRth=OUTSIDE_RTH, tif="DAY", transmit=True)
tp.parentId = parent.orderId
sl.parentId = parent.orderId
tp.ocaGroup = oca
sl.ocaGroup = oca
tp.ocaType = 1  # CANCEL_WITH_BLOCK
sl.ocaType = 1

ib.placeOrder(qc, tp)
ib.placeOrder(qc, sl)

# 6) Kort status
ib.waitOnUpdate(timeout=1)
print("[status] openOrders:", ib.openOrders())
print(
    "[status] trades:",
    [
        (t.contract.symbol, t.order.action, t.order.orderType, t.orderStatus.status)
        for t in ib.trades()
    ],
)

ib.disconnect()

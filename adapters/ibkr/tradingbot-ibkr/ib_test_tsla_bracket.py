# ib_test_tsla_bracket.py
# Ren TSLA-bracket: MKT BUY + TP/SL ±3% via TWS Paper, utan live market data-subscription.
import math
import asyncio
from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder

HOST = "127.0.0.1"
PORT = 7497  # TWS Paper
CLIENT_ID = 123  # unikt heltal
QTY = 1
TP_PCT = 0.03
SL_PCT = 0.03
OUTSIDE_RTH = False


def safe_tick(x, default=0.01):
    try:
        v = float(x)
        if v > 0 and math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def safe_price(x, default=None):
    try:
        v = float(x)
        if v > 0 and math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def round_to_min_tick(price: float, min_tick: float) -> float:
    mt = safe_tick(min_tick, 0.01)
    return round(price / mt) * mt


async def main():
    ib = IB()
    print(f"[connect] {HOST}:{PORT} clientId={CLIENT_ID}")
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    if not ib.isConnected():
        print("[error] Not connected to TWS (Paper).")
        return

    # Använd INTE reqMktData -> undvik 10089. Vi hämtar dagsbar(er) istället.
    contract = Stock("TSLA", "SMART", "USD")
    [qc] = await ib.qualifyContractsAsync(contract)

    cds = await ib.reqContractDetailsAsync(qc)
    min_tick = safe_tick(cds[0].minTick if cds else 0.01, 0.01)

    # Försök få ett referenspris från historiska dagbars (fungerar oftast på Paper utan abonnemang)
    bars = await ib.reqHistoricalDataAsync(
        qc,
        endDateTime="",
        durationStr="2 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    ref_price = None
    if bars:
        # ta senaste stängning
        ref_price = safe_price(bars[-1].close)

    # Om fortfarande None: använd konservativ fallback
    if ref_price is None:
        ref_price = 250.0  # fallback ifall IB inte gav historiska priser

    tp_price = round_to_min_tick(ref_price * (1 + TP_PCT), min_tick)
    sl_price = round_to_min_tick(ref_price * (1 - SL_PCT), min_tick)

    print(
        f"[info] TSLA ref={ref_price:.2f}  minTick={min_tick}  TP={tp_price}  SL={sl_price}"
    )

    # Klassisk bracket: parent transmit=False, TP transmit=False, SL transmit=True (sista skjuter iväg allt)
    parent = MarketOrder("BUY", QTY, outsideRth=OUTSIDE_RTH, transmit=False)
    takeProfit = LimitOrder(
        "SELL", QTY, tp_price, outsideRth=OUTSIDE_RTH, transmit=False
    )
    stopLoss = StopOrder("SELL", QTY, sl_price, outsideRth=OUTSIDE_RTH, transmit=True)

    parent.orderId = ib.client.getReqId()
    takeProfit.parentId = parent.orderId
    stopLoss.parentId = parent.orderId

    print(f"[place] TSLA BUY {QTY} @ MKT | TP={tp_price} | SL={sl_price}")
    t_parent = ib.placeOrder(qc, parent)
    await asyncio.sleep(0.25)
    t_tp = ib.placeOrder(qc, takeProfit)
    await asyncio.sleep(0.10)
    t_sl = ib.placeOrder(qc, stopLoss)

    await asyncio.sleep(1.0)
    print("[status] openOrders:", ib.openOrders())
    print(
        "[status] trades:",
        [
            (t.contract.symbol, t.order.action, t.order.orderType, t.orderStatus.status)
            for t in ib.trades()
        ],
    )

    await asyncio.sleep(2.0)
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

# ib_buy_stock1.py — MARKETABEL LIMIT som fyller när öppet
from ib_insync import *
import time

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 12
SYMBOL = "F"  # eller 'AAPL'/'NVDA'
QTY = 1
TIF = "DAY"
OUTSIDE_RTH = True  # tillåt pre/post om tillgängligt


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    contract = Stock(SYMBOL, "SMART", "USD")
    contract = ib.qualifyContracts(contract)[0]
    print(f"[contract] {contract}")

    # Hämta quote för att sätta marketabel limit
    ib.reqMarketDataType(4)  # delayed-frozen om live saknas
    t = ib.reqMktData(contract, "", False, False)
    ib.sleep(1.0)
    mprice = t.marketPrice() or t.ask or t.last or t.close
    ib.cancelMktData(contract)
    if not mprice or mprice <= 0:
        mprice = 999999 if True else 1  # failsafe

    # marketabel LMT för KÖP: sätt LMT över aktuellt pris
    limit_price = round(float(mprice) * 1.02, 2)  # +2% headroom
    order = LimitOrder("BUY", QTY, limit_price, tif=TIF)
    order.outsideRth = OUTSIDE_RTH

    trade = ib.placeOrder(contract, order)
    print(f"[order] LMT {QTY} @ {limit_price} skickad, väntar på fill ...")

    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        ib.sleep(0.25)
        s = trade.orderStatus.status
        if s != last:
            print(
                f"[status] {s} filled={trade.orderStatus.filled} avg={trade.orderStatus.avgFillPrice}"
            )
            last = s
        if s in ("Filled", "Cancelled", "ApiCancelled", "Error"):
            break

    print(
        f"[done] status={trade.orderStatus.status} filled={trade.orderStatus.filled} avg={trade.orderStatus.avgFillPrice}"
    )
    ib.disconnect()


if __name__ == "__main__":
    main()

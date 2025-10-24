# ib_buy_stock1.py — säker MARKET-fill på US-aktie via SMART
from ib_insync import *
import time

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 12

SYMBOL = "F"  # Billig aktie (Ford). Byt till t.ex. 'AAPL' eller 'NVDA' om du vill.
QTY = 1  # 1 st aktie
TIF = "DAY"  # Standard
OUTSIDE_RTH = True  # Tillåt handel utanför RTH när det finns


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    print(f"[ib] connected: {ib.isConnected()} (via port={PORT})")

    # Standard-aktiekontrakt på SMART
    contract = Stock(SYMBOL, "SMART", "USD")
    contract = ib.qualifyContracts(contract)[0]
    print(f"[contract] {contract}")

    # MARKET-order
    order = MarketOrder("BUY", QTY, tif=TIF)
    order.outsideRth = OUTSIDE_RTH

    trade = ib.placeOrder(contract, order)
    print("[order] skickad, väntar på fill ...")

    deadline = time.time() + 20
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

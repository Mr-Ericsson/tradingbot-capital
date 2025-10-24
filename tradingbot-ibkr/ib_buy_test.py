from ib_insync import *
import time

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 12


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    print("[ib] connected:", ib.isConnected())

    # Köp Apple – funkar alltid på Paper
    contract = Stock("AAPL", "SMART", "USD")
    contract = ib.qualifyContracts(contract)[0]
    print("[contract]", contract)

    # Sätt ett pris som garanterar att ordern går igenom
    # Apple ligger alltid mellan 100–300 USD
    limit_price = 500  # över marknadspris = garanterad fill

    order = LimitOrder("BUY", 1, limit_price, tif="DAY")
    order.outsideRth = True  # tillåt utanför ordinarie tid

    trade = ib.placeOrder(contract, order)
    print("[order] skickad, väntar på fill ...")

    for _ in range(40):
        ib.sleep(0.5)
        print("[status]", trade.orderStatus.status, "filled=", trade.orderStatus.filled)
        if trade.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Error"):
            break

    print("[DONE]", trade.orderStatus.status, "filled", trade.orderStatus.filled)
    ib.disconnect()


if __name__ == "__main__":
    main()

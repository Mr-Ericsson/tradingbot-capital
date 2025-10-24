# ib_clean_slate.py
import os
import sys
import time
from typing import List
from ib_insync import IB, MarketOrder, util

# ---- Anslutning (läser .env om du har IB_HOST/IB_PORT/IB_CLIENT_ID där) ----
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))  # 7497 (paper), 7496 (live)
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))

FORCE = "--force" in sys.argv  # kör på riktigt bara om --force anges


def connect() -> IB:
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=False)
    return ib


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def list_open(ib: IB):
    print_header("ÖPPNA POSITIONER")
    positions = ib.positions()
    if not positions:
        print("Inga positioner.")
    else:
        for p in positions:
            print(
                f"{p.contract.symbol:8s}  {p.contract.secType:5s}  qty={p.position}  avgCost={p.avgCost}"
            )

    print_header("ÖPPNA ORDRAR")
    open_trades = ib.openTrades()
    if not open_trades:
        print("Inga öppna ordrar.")
    else:
        for t in open_trades:
            c = t.contract
            o = t.order
            s = t.orderStatus
            print(
                f"{c.symbol:8s}  {o.action:4s} {o.orderType:5s} qty={o.totalQuantity}  "
                f"lmt={getattr(o,'lmtPrice',None)} stop={getattr(o,'auxPrice',None)}  status={s.status}"
            )


def cancel_all_orders(ib: IB):
    open_trades = ib.openTrades()
    if not open_trades:
        print("Inga ordrar att avbryta.")
        return

    print_header("AVBRYTER ÖPPNA ORDRAR")
    for t in open_trades:
        try:
            ib.cancelOrder(t.order)
            print(
                f"Cancel: {t.contract.symbol}  {t.order.action} {t.order.orderType} qty={t.order.totalQuantity}"
            )
        except Exception as e:
            print(f"  ! Kunde inte avbryta {t.contract.symbol}: {e}")

    # Vänta lite och visa status
    for _ in range(20):
        ib.waitOnUpdate(timeout=0.2)
    remaining = ib.openTrades()
    if remaining:
        print("Varning: följande ordrar verkar fortfarande vara aktiva:")
        for t in remaining:
            print(
                f"  -> {t.contract.symbol} {t.order.action} {t.order.orderType} status={t.orderStatus.status}"
            )
    else:
        print("Alla öppna ordrar avbrutna.")


def close_all_positions(ib: IB):
    positions = ib.positions()
    if not positions:
        print("Inga positioner att stänga.")
        return

    print_header("STÄNGER ALLA POSITIONER (MARKET)")
    trades = []
    for p in positions:
        qty = p.position
        if qty == 0:
            continue
        action = "SELL" if qty > 0 else "BUY"  # long -> sälj, short -> köp tillbaks
        abs_qty = abs(int(qty))

        o = MarketOrder(action, abs_qty, transmit=True)
        # Tillåt fill även utanför RTH i paper
        o.outsideRth = True
        o.eTradeOnly = False
        o.firmQuoteOnly = False
        o.overridePercentageConstraints = True
        o.tif = "DAY"

        try:
            tr = ib.placeOrder(p.contract, o)
            print(f"Close: {p.contract.symbol:8s} {action:4s} qty={abs_qty}")
            trades.append(tr)
        except Exception as e:
            print(f"  ! Kunde inte skicka stängningsorder för {p.contract.symbol}: {e}")

    # Vänta på fills
    print("Väntar på fills...")
    for _ in range(120):  # ~120 sek max
        done = all(tr.isDone() for tr in trades)
        if done:
            break
        ib.waitOnUpdate(timeout=0.5)

    # Sammanfattning
    all_done = True
    for tr in trades:
        s = tr.orderStatus
        if not tr.isDone():
            all_done = False
        print(
            f"  {tr.contract.symbol:8s} status={s.status:10s} filled={s.filled} remaining={s.remaining} avgFill={s.avgFillPrice}"
        )

    if all_done:
        print("Alla positioner verkar stängda.")
    else:
        print(
            "Varning: någon/några positioner är inte fullt stängda – kontrollera TWS/IBKR Desktop."
        )


def main():
    print_header("IBKR CLEAN SLATE")
    print(f"Host={IB_HOST}  Port={IB_PORT}  ClientID={IB_CLIENT_ID}")
    ib = connect()
    print(f"API connected: {ib.isConnected()} (Paper om port=7497)")

    list_open(ib)

    if not FORCE:
        print_header("DRY-RUN")
        print("Detta var en DRY-RUN. Inget har avbrutits/stängts.")
        print("KÖR PÅ RIKTIGT MED:  python ib_clean_slate.py --force")
        ib.disconnect()
        return

    print_header("KÖR RENSNING (FORCE)")
    cancel_all_orders(ib)
    # Kort paus innan stängning, så inga barnordrar jäklas
    time.sleep(1.0)
    close_all_positions(ib)

    print_header("EFTERKONTROLL")
    # Avbryt eventuella nya ordrar som skapats av stängningar (edge case)
    cancel_all_orders(ib)
    # Lista slutstatus
    list_open(ib)

    ib.disconnect()
    print("\nKLART.")


if __name__ == "__main__":
    main()

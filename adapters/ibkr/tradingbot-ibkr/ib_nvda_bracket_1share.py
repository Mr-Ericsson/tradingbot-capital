# ib_nvda_bracket_1share.py
import time, math
from ib_insync import *

HOST = "127.0.0.1"
PORT = 7497  # Paper TWS
CLIENT_ID = 1

SYMBOL = "NVDA"
TP_SL_PCT = 0.03  # 3%


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)

    # Kontrakt
    c = Stock(SYMBOL, "SMART", "USD", primaryExchange="NASDAQ")

    print("[ib] connected:", ib.isConnected())
    print(f"[plan] Köp 1 {SYMBOL} med bracket TP/SL ±{int(TP_SL_PCT*100)}%")

    # Parent: MARKET BUY 1
    parent = MarketOrder("BUY", 1, outsideRth=True)
    parent.tif = "DAY"

    tr_parent = ib.placeOrder(c, parent)
    print("[parent] MARKET skickad, väntar på fill...")

    # Vänta fill upp till ~90s
    for i in range(90):
        ib.waitOnUpdate(timeout=1.0)
        st = tr_parent.orderStatus
        if tr_parent.isDone():
            break
        if i % 5 == 0:
            print(
                f"  waiting... status={st.status}, filled={st.filled}, remaining={st.remaining}"
            )

    st = tr_parent.orderStatus
    avg = st.avgFillPrice or (
        tr_parent.fills[-1].execution.avgPrice if tr_parent.fills else None
    )
    print(f"[status] {st.status}  filled={st.filled}  avg={avg}")

    if not avg or st.filled < 1:
        print("[abort] ingen fill – kunde inte köpa NVDA.")
        ib.disconnect()
        return

    # TP/SL baserade på fillpriset
    tp_px = round(avg * (1 + TP_SL_PCT), 2)
    sl_px = round(avg * (1 - TP_SL_PCT), 2)
    print(f"[levels] TP={tp_px}  SL={sl_px}")

    # OCA-par (en fylls -> andra avbryts)
    oca = f"oca_{int(time.time())}"

    tp = LimitOrder("SELL", 1, tp_px, transmit=False)
    sl = StopOrder("SELL", 1, sl_px, transmit=True)

    for o in (tp, sl):
        o.ocaGroup = oca
        o.ocaType = 1
        o.tif = "GTC"
        o.outsideRth = True

    t_tp = ib.placeOrder(c, tp)
    t_sl = ib.placeOrder(c, sl)

    print(f"[children] TP id={t_tp.order.orderId}  SL id={t_sl.order.orderId}")
    print("[done] NVDA 1 st köpt. TP/SL lagda som OCA.")

    ib.disconnect()


if __name__ == "__main__":
    main()

import os, time, math
from ib_insync import IB, MarketOrder, LimitOrder, StopOrder
from ibkr_client import connect_ib, ensure_stock

SYMBOL = "NVDA"
NOTIONAL_USD = 100.0
TP_SL_PCT = 0.03  # 3%


def ref_price_from_ticker(t):
    vals = []
    for v in (t.midpoint(), t.last, t.close, t.marketPrice()):
        try:
            if v and math.isfinite(v) and v > 0:
                vals.append(v)
        except Exception:
            pass
    return vals[0] if vals else None


def get_reference_price(ib: IB, contract):
    ib.reqMarketDataType(4)  # 4 = delayed
    t = ib.reqMktData(contract, "", False, False)
    for _ in range(50):
        ib.waitOnUpdate(timeout=0.1)
        px = ref_price_from_ticker(t)
        if px:
            return px
    # sista fallback: försök en snabb historical snapshot (kan vara delayed)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="1 D",
        barSizeSetting="5 mins",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )
    if bars:
        return float(bars[-1].close)
    return None


def main():
    ib: IB = connect_ib()
    print("[ib] connected:", ib.isConnected())
    c = ensure_stock(ib, SYMBOL)

    px = get_reference_price(ib, c)
    if not px:
        print("[abort] Hittar inget referenspris för NVDA (delayed data).")
        ib.disconnect()
        return

    qty = round(NOTIONAL_USD / px, 4)  # fraktioner
    if qty <= 0:
        print("[abort] Qty blev 0, px=", px)
        ib.disconnect()
        return

    print(f"[plan] Köper ~${NOTIONAL_USD} NVDA @ ref {px:.2f} -> qty {qty}")

    parent = MarketOrder("BUY", qty, transmit=True)
    parent.outsideRth = True
    parent.eTradeOnly = False
    parent.firmQuoteOnly = False
    parent.overridePercentageConstraints = True
    parent.tif = "DAY"

    tr = ib.placeOrder(c, parent)
    print("[parent] MARKET skickad, väntar fill...")

    # vänta på fill upp till ~90s
    for i in range(90):
        ib.waitOnUpdate(timeout=1.0)
        if tr.isDone():
            break
        if i % 5 == 0:
            s = tr.orderStatus
            print(
                f"  waiting... status={s.status}, filled={s.filled}, remaining={s.remaining}"
            )

    avg = tr.orderStatus.avgFillPrice or (
        tr.fills[-1].execution.avgPrice if tr.fills else None
    )
    print(f"[status] {tr.orderStatus.status} filled={tr.orderStatus.filled} avg={avg}")
    if not avg:
        print("[abort] Ingen fill – kunde inte köpa NVDA.")
        ib.disconnect()
        return

    tp_px = round(avg * (1 + TP_SL_PCT), 4)
    sl_px = round(avg * (1 - TP_SL_PCT), 4)
    print(f"[levels] TP={tp_px}  SL={sl_px}")

    oca = f"oca_{int(time.time())}"
    tp = LimitOrder("SELL", qty, tp_px, transmit=False)
    sl = StopOrder("SELL", qty, sl_px, transmit=True)
    for o in (tp, sl):
        o.ocaGroup = oca
        o.ocaType = 1
        o.outsideRth = True
        o.overridePercentageConstraints = True
        o.tif = "GTC"

    t_tp = ib.placeOrder(c, tp)
    t_sl = ib.placeOrder(c, sl)
    print(f"[children] TP id={t_tp.order.orderId}  SL id={t_sl.order.orderId}")

    ib.sleep(1.0)
    print("[done] NVDA köpt för ~100 USD. TP/SL ±3% lagda (OCA).")
    ib.disconnect()


if __name__ == "__main__":
    main()

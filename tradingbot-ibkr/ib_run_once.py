import os, time
from ib_insync import IB
from ibkr_client import (
    connect_ib,
    ensure_stock,
    get_mid_or_last,
    calc_tp_sl,
    build_bracket,
)

# ===== Konfig (override:a via ENV) =====
SYMBOL = os.getenv("SYMBOL", "TSLA")
SIDE = os.getenv("SIDE", "BUY").upper()  # rekommenderat: BUY i Paper
QTY = int(os.getenv("QTY", "1"))
TP_SL_PCT = float(os.getenv("TP_SL_PCT", "0.03"))  # 3%

# Prislogik
MID_TIMEOUT = 2.5  # hur länge vi försöker få mid/last
AGGR_LIMIT_UP = 1.50  # om ingen data: BUY-limit = 10% över referens
AGGR_LIMIT_DN = 0.90  # om SELL: 10% under referens

# Fill-logik
PARENT_WAIT = 90.0  # hur länge vi totalt jagar fill
REPRICE_EVERY = 2.0  # hur ofta vi re-prissätter parent (sek)
REPRICE_BUMP = 0.01  # hur mycket vi flyttar pris per bump (0.2%)

# Fallback ifall vi *saknar* data helt
NO_DATA_REF = (
    250.0  # referenspris om inget kan hämtas (TSLA ignorerar ändå via aggressiv LIMIT)
)


def place_and_chase(ib: IB, contract, side: str, qty: int, entry_limit: float):
    """
    Lägger parent LIMIT och prisjagar (modifierar) tills fill eller timeout.
    Returnerar (filled: bool, fill_price: float).
    """
    # Build barnnivåer baserat på initial entry
    tp_price, sl_price = calc_tp_sl(entry_limit, TP_SL_PCT, side)
    oca = f"OCA_{SYMBOL}_{int(time.time())}"

    # Skapa bracket (parent transmit=False; SL transmit=True)
    parent, tp, sl = build_bracket(side, qty, entry_limit, tp_price, sl_price, oca)

    # 1) Placera parent och få orderId
    trade_parent = ib.placeOrder(contract, parent)
    parent_id = trade_parent.order.orderId

    # 2) Sätt parentId på barn och placera – SL (transmit=True) skickar kedjan
    tp.parentId = parent_id
    sl.parentId = parent_id
    ib.placeOrder(contract, tp)
    ib.placeOrder(contract, sl)

    print(f"[parent] LIMIT @ {entry_limit} | tp={tp_price} sl={sl_price} (OCA={oca})")
    print(f"         parentId={parent_id}  (TP/SL lagda som OCA)")

    # 3) Vänta och pris-jaga tills fill eller timeout
    t0 = time.time()
    bumps = 0
    while time.time() - t0 < PARENT_WAIT:
        ib.sleep(0.5)
        st = trade_parent.orderStatus.status or ""
        avg = trade_parent.orderStatus.avgFillPrice or 0.0
        rem = trade_parent.orderStatus.remaining
        print(f"  wait... status={st} avg={avg} rem={rem}")

        if avg and avg > 0:
            # Filled
            return True, float(avg)

        # Om fortfarande ej filled – bump:a priset var REPRICE_EVERY sekund
        if (time.time() - t0) // REPRICE_EVERY > bumps:
            bumps += 1
            if side == "BUY":
                entry_limit = round(entry_limit * (1 + REPRICE_BUMP), 4)
            else:
                entry_limit = round(entry_limit * (1 - REPRICE_BUMP), 4)
            # Gör modify

            trade_parent.order.lmtPrice = entry_limit
            ib.placeOrder(contract, trade_parent.order)  # re-send -> modifies
            ib.sleep(0.2)
            print(f"  repriced parent -> {entry_limit}")

    # Timeout
    return False, 0.0


def main():
    ib = connect_ib()
    print("[ib] connected:", ib.isConnected())
    contract = ensure_stock(ib, SYMBOL)

    # 1) Försök få mid/last (delayed funkar)
    ref = get_mid_or_last(ib, contract, timeout=MID_TIMEOUT)

    # 2) Bestäm initial LIMIT-entry
    if ref:
        if SIDE == "BUY":
            entry = round(ref * 1.0015, 4)  # 0.15% över mid → bör fyllas snabbt
        else:
            entry = round(ref * 0.9985, 4)  # 0.15% under mid
    else:
        # Ingen data alls: använd en aggressiv LIMIT kring NO_DATA_REF
        base = NO_DATA_REF
        if SIDE == "BUY":
            entry = round(base * AGGR_LIMIT_UP, 4)
        else:
            entry = round(base * AGGR_LIMIT_DN, 4)

    print(f"[plan] {SIDE} {SYMBOL} x{QTY} via LIMIT {entry}  (ref={ref})")

    filled, fill_px = place_and_chase(ib, contract, SIDE, QTY, entry)

    if filled:
        print(f"[fill] entry={fill_px}")
        print("[done] TP/SL lades vid orderstart (bracket). Klart.")
    else:
        print(
            "[abort] Ingen fill trots prisjakt. Kolla TWS-loggar/precautions eller höj REPRICE_BUMP/PARENT_WAIT."
        )

    ib.disconnect()


if __name__ == "__main__":
    main()

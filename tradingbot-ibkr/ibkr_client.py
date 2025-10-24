import os, time
from typing import Tuple
from dotenv import load_dotenv
from ib_insync import IB, Stock, Contract, Ticker, MarketOrder, LimitOrder, StopOrder

load_dotenv()
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))


def connect_ib() -> IB:
    """
    Koppla upp mot TWS/Gateway och välj delayed market data om live saknas.
    """
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=False)
    # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
    ib.reqMarketDataType(4)
    return ib


def ensure_stock(ib: IB, symbol: str) -> Contract:
    """
    Kvalificera SMART/USD-aktie för symbolen.
    """
    c = Stock(symbol, "SMART", "USD")
    q = ib.qualifyContracts(c)
    if not q:
        raise RuntimeError(f"Kunde inte kvalificera kontrakt för {symbol}")
    return q[0]


def get_mid_or_last(ib: IB, contract: Contract, timeout: float = 2.5) -> float | None:
    """
    Försök hämta bid/ask och returnera mid. Faller tillbaka till last.
    Returnerar None om inget finns inom timeout.
    """
    t: Ticker = ib.reqMktData(contract, "", False, False)
    t0 = time.time()
    while time.time() - t0 < timeout:
        ib.sleep(0.1)
        if t.bid and t.ask and t.bid > 0 and t.ask > 0:
            return (float(t.bid) + float(t.ask)) / 2.0
        if t.last and float(t.last) > 0:
            return float(t.last)
    return None


def calc_tp_sl(entry: float, pct: float, side: str) -> Tuple[float, float]:
    """
    Beräkna TP/SL runt entry med given procent i absolut termer (ej ticks).
    """
    side = side.upper()
    if side == "BUY":
        tp = entry * (1 + pct)
        sl = entry * (1 - pct)
    else:
        tp = entry * (1 - pct)
        sl = entry * (1 + pct)
    # 4 decimaler brukar räcka för aktier
    return round(tp, 4), round(sl, 4)


def build_bracket(
    side: str,
    qty: int,
    limit_price: float,
    tp_price: float,
    sl_price: float,
    oca_group: str,
):
    """
    Skapa parent LIMIT + TP (LIMIT) + SL (STOP) som OCA-barn.
    IBKR-standard: parent.transmit=False, TP.transmit=False, SL.transmit=True (sista i kedjan).
    Barnen får parentId sättas i koden när orderId blivit tilldelat.
    """
    side = side.upper()
    exit_side = "SELL" if side == "BUY" else "BUY"

    parent = LimitOrder(side, qty, limit_price, outsideRth=True, transmit=False)
    tp = LimitOrder(exit_side, qty, tp_price, outsideRth=True, transmit=False)
    sl = StopOrder(exit_side, qty, sl_price, outsideRth=True, transmit=True)

    # Sätt OCA på barnen
    for o in (tp, sl):
        o.ocaGroup = oca_group
        o.ocaType = 1  # CANCEL_WITH_BLOCK

    return parent, tp, sl

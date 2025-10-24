# run_auto.py
import os, time
from dotenv import load_dotenv
from ib_insync import IB, MarketOrder, LimitOrder, StopOrder, Stock
from scanner import pick_top

load_dotenv()
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))

TP_SL_PCT = float(os.getenv("TP_SL_PCT", "0.03"))  # 3%
MAX_POS = int(os.getenv("MAX_POS", "10"))
QTY = int(os.getenv("QTY", "1"))

UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "META",
    "TSLA",
    "AMZN",
    "AMD",
    "GOOGL",
    "NFLX",
    "AVGO",
    "QCOM",
    "COST",
    "CRM",
    "ORCL",
    "SHOP",
    "SPY",
    "QQQ",
    "IWM",
    "XLF",
    "XLE",
]


def ensure_stock(ib: IB, symbol: str):
    c = Stock(symbol, "SMART", "USD")
    q = ib.qualifyContracts(c)
    if not q:
        raise RuntimeError(f"Kunde inte kvalificera {symbol}")
    return q[0]


def place_bracket(ib: IB, symbol: str, qty: int, tp_pct: float, sl_pct: float):
    contract = ensure_stock(ib, symbol)
    # Använd fördröjd data (räcker i Paper)
    ib.reqMarketDataType(4)
    t = ib.reqMktData(contract, "", False, False)

    # Vänta lite på mid/last
    mid = None
    for _ in range(30):
        ib.sleep(0.1)
        if t.bid and t.ask and t.bid > 0 and t.ask > 0:
            mid = (float(t.bid) + float(t.ask)) / 2
            break
        if t.last and t.last > 0:
            mid = float(t.last)
            break
    if mid is None:
        mid = 100.0

    # Parent som LIMIT nära mid ⇒ marketable
    entry = round(mid * 1.0015, 4)
    parent = LimitOrder("BUY", qty, entry, outsideRth=True, transmit=False)

    # Bracket TP/SL
    tp = round(entry * (1 + tp_pct), 4)
    sl = round(entry * (1 - sl_pct), 4)

    oca = f"OCA_{symbol}_{int(time.time())}"
    take = LimitOrder("SELL", qty, tp, outsideRth=True, transmit=False)
    stop = StopOrder("SELL", qty, sl, outsideRth=True, transmit=True)
    for o in (take, stop):
        o.ocaGroup = oca
        o.ocaType = 1

    tr = ib.placeOrder(contract, parent)
    pid = tr.order.orderId
    take.parentId = pid
    stop.parentId = pid
    ib.placeOrder(contract, take)
    ib.placeOrder(contract, stop)
    print(f"[order] {symbol} entry={entry} tp={tp} sl={sl} (orderId={pid})")


def main():
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=False)
    ib.reqMarketDataType(4)

    # Kolla hur många positioner vi redan har
    open_positions = {p.contract.symbol: p for p in ib.positions()}
    if len(open_positions) >= MAX_POS:
        print(f"[skip] Redan {len(open_positions)} positioner ≥ MAX_POS={MAX_POS}")
        return

    # Välj kandidater via AI-scanner
    picks = pick_top(UNIVERSE, top_n=MAX_POS - len(open_positions))
    print("[picks]", picks)

    # Lägg ordrar för nya symboler (undvik dubbletter)
    for sym, score, last in picks:
        if sym in open_positions:
            continue
        place_bracket(ib, sym, QTY, TP_SL_PCT, TP_SL_PCT)
        ib.sleep(0.5)

    ib.disconnect()


if __name__ == "__main__":
    main()

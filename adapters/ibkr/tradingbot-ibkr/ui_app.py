# ui_app.py
import os, time
import streamlit as st
from dotenv import load_dotenv
from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder

load_dotenv()
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))


@st.cache_resource(show_spinner=False)
def get_ib() -> IB:
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=False)
    ib.reqMarketDataType(4)  # delayed-frozen räcker i Paper
    return ib


def ensure_stock(ib: IB, symbol: str):
    c = Stock(symbol.upper(), "SMART", "USD")
    q = ib.qualifyContracts(c)
    if not q:
        raise RuntimeError(f"Kunde inte kvalificera {symbol}")
    return q[0]


def place_bracket(
    ib: IB,
    symbol: str,
    side: str,
    qty: int,
    tp_pct: float,
    sl_pct: float,
    use_limit: bool,
    limit_pad: float,
):
    side = side.upper()
    exit_side = "SELL" if side == "BUY" else "BUY"
    contract = ensure_stock(ib, symbol)

    # Hämta enkel referens (delayed ok)
    t = ib.reqMktData(contract, "", False, False)
    for _ in range(30):
        ib.sleep(0.1)
        if t.bid and t.ask and t.bid > 0 and t.ask > 0:
            mid = (float(t.bid) + float(t.ask)) / 2
            break
        if t.last and t.last > 0:
            mid = float(t.last)
            break
    else:
        mid = 100.0

    # Parent
    if use_limit:
        px = round(mid * (1 + limit_pad if side == "BUY" else 1 - limit_pad), 4)
        parent = LimitOrder(side, qty, px, outsideRth=True, transmit=False)
        entry_ref = px
    else:
        parent = MarketOrder(side, qty, outsideRth=True, transmit=False)
        entry_ref = mid

    # TP/SL
    if side == "BUY":
        tp_px = round(entry_ref * (1 + tp_pct), 4)
        sl_px = round(entry_ref * (1 - sl_pct), 4)
    else:
        tp_px = round(entry_ref * (1 - tp_pct), 4)
        sl_px = round(entry_ref * (1 + sl_pct), 4)

    oca = f"OCA_{symbol}_{int(time.time())}"
    tp = LimitOrder(exit_side, qty, tp_px, outsideRth=True, transmit=False)
    sl = StopOrder(exit_side, qty, sl_px, outsideRth=True, transmit=True)
    for o in (tp, sl):
        o.ocaGroup = oca
        o.ocaType = 1

    tr = ib.placeOrder(contract, parent)
    pid = tr.order.orderId
    tp.parentId = pid
    sl.parentId = pid
    ib.placeOrder(contract, tp)
    ib.placeOrder(contract, sl)

    return {
        "entry_type": "LIMIT" if use_limit else "MARKET",
        "entry_ref": entry_ref,
        "tp": tp_px,
        "sl": sl_px,
        "orderId": pid,
    }


def cancel_all_open_orders(ib: IB):
    for o in ib.openOrders():
        ib.cancelOrder(o)


def close_all_positions(ib: IB):
    for p in ib.positions():
        c = p.contract
        qty = abs(p.position)
        action = "SELL" if p.position > 0 else "BUY"
        ib.placeOrder(c, MarketOrder(action, qty, outsideRth=True, transmit=True))


# --- UI ---
st.set_page_config(page_title="IBKR Dashboard", layout="wide")
st.title("🟢 IBKR Dashboard (Paper)")
ib = get_ib()

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Connected", "Yes" if ib.isConnected() else "No")
with c2:
    vals = {v.tag: v.value for v in ib.accountValues()}
    netliq = vals.get("NetLiquidation")
    st.metric("NetLiq", f"${float(netliq):,.2f}" if netliq else "—")
with c3:
    st.button("🔁 Refresh")

st.subheader("Öppna positioner")
rows = [
    {
        "Symbol": p.contract.symbol,
        "Qty": p.position,
        "Avg Px": p.avgCost,
        "Market Value": p.marketValue,
    }
    for p in ib.positions()
]
st.dataframe(rows, use_container_width=True)

st.subheader("Öppna ordrar")
ords = [
    {
        "OrderId": tr.order.orderId,
        "Symbol": tr.contract.symbol,
        "Action": tr.order.action,
        "Type": tr.order.orderType,
        "Lmt/Stop": getattr(tr.order, "lmtPrice", getattr(tr.order, "auxPrice", None)),
        "Status": tr.orderStatus.status,
    }
    for tr in ib.openTrades()
]
st.dataframe(ords, use_container_width=True)

st.divider()
st.subheader("Lägg order (med TP/SL)")

with st.form("orderform"):
    a, b, c, d, e = st.columns([1.2, 1, 1, 1, 1.2])
    symbol = a.text_input("Symbol", "TSLA")
    side = b.selectbox("Riktning", ["BUY", "SELL"])
    qty = c.number_input("Antal", min_value=1, value=1, step=1)
    tp_pct = (
        d.number_input("TP %", min_value=0.1, max_value=20.0, value=3.0, step=0.5)
        / 100.0
    )
    sl_pct = (
        e.number_input("SL %", min_value=0.1, max_value=20.0, value=3.0, step=0.5)
        / 100.0
    )

    f, g = st.columns([1, 1])
    use_limit = f.toggle("Använd LIMIT (rekommenderas)", value=True)
    limit_pad = g.slider("LIMIT-påslag (%)", 0.0, 2.0, 0.15, 0.05) / 100.0

    submitted = st.form_submit_button("Lägg bracket-order")
    if submitted:
        try:
            info = place_bracket(
                ib, symbol, side, int(qty), tp_pct, sl_pct, use_limit, limit_pad
            )
            st.success(
                f"Order: {info['entry_type']} @ {info['entry_ref']} | TP {info['tp']} | SL {info['sl']} (id {info['orderId']})"
            )
        except Exception as ex:
            st.error(f"Fel: {ex}")

st.divider()
x, y = st.columns(2)
with x:
    if st.button("🧹 Avbryt alla öppna ordrar"):
        cancel_all_open_orders(ib)
        st.info("Avbryter…")
with y:
    if st.button("🛑 Stäng alla positioner (MARKET)"):
        close_all_positions(ib)
        st.warning("Stänger…")

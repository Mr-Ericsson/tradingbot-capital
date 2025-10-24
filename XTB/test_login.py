from websocket import create_connection
from xtb_quick_trade import WS_URL, xtb_login, get_tick


def has_symbol(ws, sym):
    from xtb_quick_trade import ws_call

    try:
        info = ws_call(ws, "getSymbol", {"symbol": sym})
        return info.get("status", False)
    except Exception:
        return False


ws = create_connection(WS_URL)
try:
    xtb_login(ws)
    for sym in ["US500", "US100", "AAPL.US"]:
        try:
            ask, bid = get_tick(ws, sym)
            print(f"{sym}  ask={ask}  bid={bid}")
        except Exception as e:
            print(f"{sym}: {e}")
finally:
    ws.close()

# xtb_quick_trade.py
# XTB DEMO: login -> hämta pris -> MARKET BUY med TP/SL -> visa status
import os, sys, json, time, math
from datetime import datetime, timezone
from websocket import create_connection  # pip install websocket-client

XTB_USER = os.environ.get("XTB_USER")
XTB_PASS = os.environ.get("XTB_PASS")
if not XTB_USER or not XTB_PASS:
    print("[fel] Sätt miljövariablerna XTB_USER och XTB_PASS först.")
    sys.exit(1)

# DEMO-server:
WS_URL = "wss://ws.xapi.pro/demo"


def ws_call(ws, command, arguments=None):
    payload = {"command": command}
    if arguments is not None:
        payload["arguments"] = arguments
    ws.send(json.dumps(payload))
    resp = json.loads(ws.recv())
    if not resp.get("status", False):
        raise SystemExit(f"[{command}] ERROR: {resp}")
    return resp


def xtb_login(ws):
    args = {"userId": int(XTB_USER), "password": XTB_PASS, "appName": "ME-bot"}
    r = ws_call(ws, "login", args)
    sid = r.get("streamSessionId")
    print(f"[login] OK. streamSessionId={sid}")
    return sid


def get_symbol_info(ws, symbol):
    r = ws_call(ws, "getSymbol", {"symbol": symbol})
    return r["returnData"]


def get_tick(ws, symbol):
    # Hämta senaste ask/bid via getSymbol (stabilt på alla gateways)
    r = ws_call(ws, "getSymbol", {"symbol": symbol})
    d = r["returnData"]
    return float(d["ask"]), float(d["bid"])


def normalize_price(price, tick_size):
    return round(round(price / tick_size) * tick_size, 5)


def ensure_min_distance(price_buy, sl, tp):
    if sl >= price_buy:
        sl = price_buy * 0.99
    if tp <= price_buy:
        tp = price_buy * 1.01
    return sl, tp


def open_market_buy(ws, symbol, volume, tp_pct=0.03, sl_pct=0.03):
    ask, bid = get_tick(ws, symbol)
    print(f"[pris] {symbol} ask={ask} bid={bid} (spread={ask-bid:.5f})")

    info = get_symbol_info(ws, symbol)
    lot_min = float(info["lotMin"])
    lot_step = float(info["lotStep"])
    tick = float(info["tickSize"])

    if volume < lot_min:
        print(f"[volym] Justerar volume -> lotMin {lot_min}")
        volume = lot_min
    steps = round(volume / lot_step)
    volume = steps * lot_step

    tp = ask * (1.0 + tp_pct)
    sl = ask * (1.0 - sl_pct)
    sl, tp = ensure_min_distance(ask, sl, tp)
    sl = normalize_price(sl, tick)
    tp = normalize_price(tp, tick)

    print(f"[nivåer] BUY {symbol} vol={volume}  SL={sl}  TP={tp}")

    tradeTransInfo = {
        "cmd": 0,
        "type": 0,
        "symbol": symbol,
        "volume": float(volume),
        "sl": sl,
        "tp": tp,
        "price": ask,
        "comment": "ME-bot",
        "offset": 0,
        "expiration": 0,
    }
    r = ws_call(ws, "tradeTransaction", {"tradeTransInfo": tradeTransInfo})
    order_id = r["returnData"]["order"]
    print(f"[order skickad] order={order_id}")

    st = ws_call(ws, "tradeTransactionStatus", {"order": order_id})
    print(
        f"[status] {st['returnData']['requestStatus']} | {st['returnData'].get('message','')}"
    )
    return order_id


def list_open_trades(ws, symbol=None):
    r = ws_call(ws, "getTrades", {"openedOnly": True})
    trades = r["returnData"]
    if symbol:
        trades = [t for t in trades if t["symbol"] == symbol]
    return trades


def close_all(ws, symbol=None):
    r = ws_call(ws, "getTrades", {"openedOnly": True})
    trades = r["returnData"]
    if not trades:
        print("[close] Inga öppna positioner.")
        return
    for t in trades:
        _, bid = get_tick(ws, t["symbol"])
        tradeTransInfo = {
            "cmd": 1,
            "type": 2,
            "order": int(t["position"]),
            "symbol": t["symbol"],
            "volume": float(t["volume"]),
            "price": bid,
            "sl": 0.0,
            "tp": 0.0,
            "offset": 0,
            "expiration": 0,
            "comment": "ME-bot-close",
        }
        r2 = ws_call(ws, "tradeTransaction", {"tradeTransInfo": tradeTransInfo})
        st = ws_call(ws, "tradeTransactionStatus", {"order": r2["returnData"]["order"]})
        print(
            f"[close] {t['symbol']} pos={t['position']} -> {st['returnData']['requestStatus']}"
        )


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "US500"  # index nästan 24/5
    volume = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
    tp_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 0.03
    sl_pct = float(sys.argv[4]) if len(sys.argv) > 4 else 0.03

    ws = create_connection(WS_URL)
    try:
        xtb_login(ws)
        open_market_buy(ws, symbol, volume, tp_pct, sl_pct)
        trades = list_open_trades(ws, symbol)
        if trades:
            print("[öppna trades]")
            for t in trades:
                print(
                    f"  pos={t['position']} {t['symbol']} vol={t['volume']} open_price={t['open_price']}"
                )
    finally:
        ws.close()


if __name__ == "__main__":
    main()

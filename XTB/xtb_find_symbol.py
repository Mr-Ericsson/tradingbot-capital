# xtb_find_symbol.py
import os, json
from websocket import create_connection
from xtb_quick_trade import WS_URL, xtb_login, ws_call

QUERY = "AAPL"  # ändra till vilken aktie du vill hitta

ws = create_connection(WS_URL)
try:
    xtb_login(ws)
    allsyms = ws_call(ws, "getAllSymbols")["returnData"]
    hits = []
    for s in allsyms:
        sym = s.get("symbol", "")
        desc = s.get("description", "")
        cat = s.get("categoryName", "")
        gr = s.get("groupName", "")
        if QUERY.lower() in sym.lower() or QUERY.lower() in desc.lower():
            hits.append((sym, desc, cat, gr))
    hits.sort()
    print(f"Found {len(hits)} matches for '{QUERY}':")
    for sym, desc, cat, gr in hits:
        print(f"{sym:15} | {desc:30} | {cat:8} | {gr}")
finally:
    ws.close()

import os, json
from websocket import create_connection

USER = os.environ.get("XTB_USER")
PASS = os.environ.get("XTB_PASS")
if not USER or not PASS:
    raise SystemExit("Sätt XTB_USER och XTB_PASS först.")

ENDPOINTS = [
    # Vanliga officiella:
    "wss://ws.xtb.com/demo",
    "wss://ws.xtb.com/real",  # (ska inte logga in med demo, men bra test av reachability)
    # Alternativa gateways som ibland används:
    "wss://ws.xtbapi.com/demo",
    "wss://xapi.xtb.com/demo",
    "wss://wss.xtb.com/demo",
    "wss://ws.xapi.pro/demo",  # äldre alias
]


def try_login(url):
    print(f"\n== Testar {url} ==")
    ws = create_connection(url, timeout=10)
    try:
        payload = {
            "command": "login",
            "arguments": {"userId": int(USER), "password": PASS, "appName": "ME-bot"},
        }
        ws.send(json.dumps(payload))
        resp = json.loads(ws.recv())
        print("Svar:", resp)
        ok = resp.get("status")
        if ok:
            sid = resp.get("streamSessionId")
            print("[OK] Login lyckades. streamSessionId:", sid)
            return True
        else:
            print("[FEL] Login misslyckades:", resp)
            return False
    finally:
        ws.close()


any_ok = False
for ep in ENDPOINTS:
    try:
        if try_login(ep):
            any_ok = True
            break
    except Exception as e:
        print("Exception:", repr(e))

if not any_ok:
    print(
        "\nIngen endpoint funkade. Vi kan:"
        "\n 1) dubbelkolla USER/PASS,"
        "\n 2) prova annan internet/DNS,"
        "\n 3) öppna port 443 för WSS,"
        "\n 4) jag ger en fallback med HTTP-proxy parametrar."
    )

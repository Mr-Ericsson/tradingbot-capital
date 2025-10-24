# ib_live_trade_top10.py
# Kör:  python ib_live_trade_top10.py
# Kräver: pip install ib-insync pandas

import asyncio, math, datetime as dt
from ib_insync import *
import pandas as pd
import datetime as dt
from datetime import UTC


# ====== SNABB-KONFIG ======
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 42  # papperskonto: 7497
LEVERAGE = 10  # upp till X10 (ändra om du vill)
USD_PER_TRADE = 100  # varje position värde
MAX_SPREAD_PCT = 0.03  # 0.5% max spread
GAP_SKIP_PCT = (
    0.20  # skippa aktier som gappar >3% från gårdagens close (proxy för nyheter)
)
MARKET = "SMART"
CURRENCY = "USD"
USE_MARKET_ORDER = True  # True=Market, False=Limit at last
ENTRY_WINDOW_MIN_AFTER_OPEN = 15  # vänta till 15m efter öppning innan vi börjar trigga
END_WINDOW_MIN_AFTER_OPEN = 45  # sluta vänta efter 45m (failsafe)
USE_SPREAD_FILTER = False  # tillfälligt av idag
# ==========================


def us_market_open_today():
    # US equity regular open 09:30 ET
    # Vi tar systemets lokaltid och räknar ET via IB-klocka
    return dt.time(9, 30)


def to_et(ib_dt):
    # ib.reqCurrentTime() ger UTC; IB har ingen timezonehelper här – vi antar New York (ET) med pytz via IB datetime is naive.
    # Vi gör allt relativt IB server time (UTC) -> ET offset = -4 eller -5 beroende på DST; IB tar hand om sessioner ändå.
    # För enkelhet: vi använder IB:s börsbytesdata för första 1m bar tidsstämplar istället (nedan), så vi behöver inte exakt ET-offset här.
    return ib_dt


async def ensure_connection(ib: IB):
    if not ib.isConnected():
        await ib.connectAsync(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=10)


async def qualify_stock(ib: IB, symbol: str):
    # försök få primexchange/conId
    c = Stock(symbol, MARKET, CURRENCY)
    cds = await ib.reqContractDetailsAsync(c)
    if not cds:
        return None
    # välj "vanlig" aktienotering
    # prioritet: NASDAQ/NYSE/ARCA primärt
    preferred = ["NASDAQ", "NYSE", "ARCA", "BATS", "IEX"]
    best = None
    for cd in cds:
        ex = (cd.contract.primaryExchange or "").upper()
        if ex in preferred:
            best = cd.contract
            break
    if not best:
        best = cds[0].contract
    return best


async def get_daily_history(ib: IB, contract: Contract, days=220):
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr=f"{days} D",  # <-- 220 dagar
        barSizeSetting="1 day",  # <-- dagliga bars
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    return bars


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


async def compute_ma200_and_gap(ib: IB, contract: Contract):
    bars = await get_daily_history(ib, contract, days=220)
    if not bars or len(bars) < 200:
        return None, None, None
    closes = [b.close for b in bars]
    ma200 = sma(closes, 200)
    yclose = closes[-2]  # gårdagens close (senaste stängda bar)
    last = closes[-1]  # senaste (igår om före öppning, idag om efter RTH)
    gap = abs(last - yclose) / yclose if yclose else 0.0
    return ma200, yclose, gap


async def get_spread_ok(ib: IB, contract: Contract):
    # Om vi inte vill använda spread-filtret (t.ex. saknar MktData) -> släpp igenom
    if not USE_SPREAD_FILTER:
        return True, 0.0, 0.0

    # Annars försök snapshot 2-3 ggr; om IB blockar (10089) -> släpp igenom för idag
    for _ in range(3):
        try:
            t = ib.reqMktData(contract, "", True, False)  # snapshot=True
            await asyncio.sleep(1.0)
            bid = t.bid if t.bid is not None else 0.0
            ask = t.ask if t.ask is not None else 0.0
            last = t.last if t.last is not None else 0.0
            ib.cancelMktData(contract)

            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                spread = (ask - bid) / mid if mid else 1.0
                return spread <= MAX_SPREAD_PCT, bid, ask

            if last > 0:
                return True, 0.0, 0.0  # fallback om bara last finns

        except Exception:
            # typiskt 10089 → släpp igenom idag
            return True, 0.0, 0.0

        await asyncio.sleep(0.5)

    return False, 0.0, 0.0


def make_bracket(action: str, qty: int, entry_price: float, tp: float, sl: float):
    # IB bracket (parent MKT/LMT + takeProfit + stopLoss)
    parent = (
        MarketOrder(action, qty)
        if USE_MARKET_ORDER
        else LimitOrder(action, qty, entry_price)
    )
    parent.transmit = False
    take = LimitOrder("SELL" if action == "BUY" else "BUY", qty, tp)
    take.parentId = 0
    take.transmit = False
    stop = StopOrder("SELL" if action == "BUY" else "BUY", qty, sl)
    stop.parentId = 0
    stop.transmit = True
    return parent, take, stop


async def fetch_1min_bars(ib: IB, contract: Contract):
    """
    Hämta sista 30 min i 1-min bars. Testa TRADES -> MIDPOINT -> BID_ASK.
    Returnerar (bars, whatToShow) eller ([], None) om inget finns (HMDS 162).
    """
    for what in ("TRADES", "MIDPOINT", "BID_ASK"):
        try:
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="1800 S",  # 30 minuter
                barSizeSetting="1 min",
                whatToShow=what,
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
            if bars:
                return bars, what
        except Exception as e:
            # IB ger HMDS 162 när ingen data finns för den typen
            if "HMDS query returned no data" in str(e) or "Error 162" in str(e):
                continue
            else:
                raise
    return [], None


async def wait_for_open_then_trigger(
    ib: IB, contract: Contract, side: str, entry_level: float, tp: float, sl: float
):
    """
    Väntar tills första 15 min (1m-bars) är klara, beräknar 15m high/low och en snabb MA20 (5m),
    och bevakar sedan ett *strömmande* (delayed streaming) pris tills nivån bryts.
    Lägger därefter bracket-order (±3%) med sizing ≈ $100 * x10.
    """
    # 1) Vänta tills vi har 15 st 1-min bar för idag och räkna nivåerna
    deadline = dt.datetime.utcnow() + dt.timedelta(
        minutes=END_WINDOW_MIN_AFTER_OPEN + 60
    )  # failsafe

    hi15 = lo15 = None
    ma20_5m = None

    while dt.datetime.utcnow() < deadline and (hi15 is None or lo15 is None):
        bars, src = await fetch_1min_bars(ib, contract)
        if not bars:
            print(
                f"[SKIP] {contract.symbol}: ingen intradaghistorik – hoppar (HMDS 162)."
            )
            return None

        # Dagens barer
        last_day = bars[-1].date.date()
        today = [b for b in bars if b.date.date() == last_day]

        if len(today) < 16:  # minst 16 st 1m-bars => 15 min kompletta + 1 påbörjad
            await asyncio.sleep(3)
            continue

        # Första 15 min
        first15 = today[:15]
        hi15 = max(b.high for b in first15)
        lo15 = min(b.low for b in first15)

        # Aggregera till 5m för snabb MA20 (om möjligt)
        fives = []
        for i in range(0, len(today), 5):
            chunk = today[i : i + 5]
            if len(chunk) == 5:
                o = chunk[0].open
                h = max(x.high for x in chunk)
                l = min(x.low for x in chunk)
                c = chunk[-1].close
                fives.append((o, h, l, c))
        if len(fives) >= 20:
            closes = [c for (_, _, _, c) in fives]
            ma20_5m = sum(closes[-20:]) / 20.0
        break  # nivåer klara

    if hi15 is None or lo15 is None:
        print(f"[SKIP] {contract.symbol}: lyckades inte räkna 15m-nivåer i tid.")
        return None

    # 2) Bevaka *strömmande* pris (delayed streaming) tills bryt sker eller vi timeoutar
    t = ib.reqMktData(contract, "", False, False)  # snapshot=False => stream
    triggered = False
    last_seen = None
    try:
        while dt.datetime.utcnow() < deadline and not triggered:
            # plocka senaste pris vi kan lita på
            last = t.last or t.close
            if not last:
                if t.bid is not None and t.ask is not None and (t.bid + t.ask) > 0:
                    last = (t.bid + t.ask) / 2.0

            if last:
                last_seen = float(last)
                if side == "LONG":
                    cond = (last_seen >= hi15) and (
                        ma20_5m is None or last_seen > ma20_5m
                    )
                    if cond:
                        triggered = True
                        break
                else:
                    cond = (last_seen <= lo15) and (
                        ma20_5m is None or last_seen < ma20_5m
                    )
                    if cond:
                        triggered = True
                        break

            await asyncio.sleep(0.5)
    finally:
        ib.cancelMktData(contract)

    if not triggered or last_seen is None:
        print(f"[SKIP] {contract.symbol}: ingen trigger inom fönstret.")
        return None

    # 3) Sizing & bracket runt faktisk entry (±3 %)
    entry_px = round(last_seen, 4)
    qty = max(1, math.floor((USD_PER_TRADE * LEVERAGE) / entry_px))
    if side == "SHORT":
        qty = max(1, math.floor((USD_PER_TRADE * LEVERAGE) / entry_px))  # samma sizing

    tp_price = round(entry_px * (1.03 if side == "LONG" else 0.97), 4)
    sl_price = round(entry_px * (0.97 if side == "LONG" else 1.03), 4)

    print(
        f"[*] {contract.symbol}: trigger {side} @ {entry_px} (hi15={hi15:.4f}/lo15={lo15:.4f}) – skickar order …"
    )

    parent, take, stop = make_bracket(
        "BUY" if side == "LONG" else "SELL", qty, entry_px, tp_price, sl_price
    )
    return parent, take, stop, qty, entry_px, tp_price, sl_price


async def main():
    ib = IB()
    await ensure_connection(ib)
    ib.reqMarketDataType(3)  # 4 = Delayed-Frozen Data, 3 = Delayed Streaming

    # Läs dina 10 bästa från scannern
    top = pd.read_csv("top10.csv")
    # förväntade kolumner i din fil (från din logg): symbol, side, entry, tp, sl
    # vi säkrar namn
    cols = {c.lower(): c for c in top.columns}
    sym_col = cols.get("symbol", "symbol")
    side_col = cols.get("side", "side")

    # Pre-check per symbol: IB qualify, MA200, spread, gap
    qualified = []
    for _, row in top.iterrows():
        sym = str(row[sym_col]).upper().replace(".", "-")
        side = str(row[side_col]).upper()

        contract = await qualify_stock(ib, sym)
        if not contract:
            print(f"[SKIP] {sym}: ej tradable hos IB (no contract).")
            continue

        # MA200 + gap
        ma200, yclose, gap = await compute_ma200_and_gap(ib, contract)
        if ma200 is None:
            print(f"[SKIP] {sym}: MA200 ej tillgänglig.")
            continue

        # Trendfilter: LONG kräver pris > MA200, SHORT kräver pris < MA200
        # använd senaste close (yesterday) som proxy tills vi triggar live
        if side == "LONG" and yclose <= ma200:
            print(f"[SKIP] {sym}: under MA200 för LONG.")
            continue
        if side == "SHORT" and yclose >= ma200:
            print(f"[SKIP] {sym}: över MA200 för SHORT.")
            continue

        # nyhets-proxy: skippa stora gap > 3%
        if gap is not None and gap > GAP_SKIP_PCT:
            print(f"[SKIP] {sym}: gap {gap:.1%} > {GAP_SKIP_PCT:.0%}.")
            continue

        # spread
        ok, bid, ask = await get_spread_ok(ib, contract)
        if not ok:
            print(f"[SKIP] {sym}: spread för bred (bid={bid}, ask={ask}).")
            continue

        qualified.append((contract, side))

    if not qualified:
        print("[STOP] Inga kandidater efter filter.")
        return

    print(f"[OK] {len(qualified)} kandidater kvar efter MA200/gap/spread/IB.")

    # Vänta tills första 15 min har gått, sedan trigga och lägg order
    tasks = []
    for contract, side in qualified:
        # Hämta triggers och lägg bracket när pris tar ut nivå
        tasks.append(wait_for_open_then_trigger(ib, contract, side, None, None, None))

    results = await asyncio.gather(*tasks)
    # Skicka ordrar
    for res, pair in zip(results, qualified):
        if not res:
            continue
        parent, take, stop, qty, entry_px, tp_price, sl_price = res
        contract, side = pair
        # ParentID sätts efter placeOrder return – IB-insync sköter kedjan enklast så här:
        trade = ib.placeOrder(contract, parent)
        await trade.filledEvent

        # koppla barn till parentID
        take.parentId = trade.order.orderId
        stop.parentId = trade.order.orderId
        ib.placeOrder(contract, take)
        ib.placeOrder(contract, stop)

        print(
            f"[ORDER] {contract.symbol} {side} qty={qty} @~{entry_px:.2f} TP={tp_price} SL={sl_price}"
        )

    print("[DONE] Alla triggers klara.")
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

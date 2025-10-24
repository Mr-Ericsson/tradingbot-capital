# ib_scan_universe_top10_fast.py
# Skannar IBKR-universum (STK US/EU/ASIA + FX + Futures (front)) utan scanner-entitlement.
# Filtrerar med MA20/MA200 + momentum + ADR≥3% och skriver top10.csv.
from ib_insync import *
import pandas as pd, numpy as np, math, time, string, datetime

IB_HOST = "127.0.0.1"
IB_PORT = 7497  # Paper
CLIENT_ID = 29
# --- toggles ---
FUT_ENABLE = False  # <— lämna False nu så stängs futures av helt
# --- US exchanges we allow for stocks/ETFs ---
US_EXCHANGES = {"NYSE", "NASDAQ", "ARCA"}
# --- bredare USA + fart ---
US_EXCHANGES_WIDE = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "IEX", "ISLAND", "SMART"}

# Max hur många vi tar vidare per bokstav/siffra (balans: brett men snabbt)
MAX_PER_BUCKET = 120  # höj till 150 om du vill trycka upp volymen ännu mer


# ---- filter & scoring ----
PRICE_MIN, PRICE_MAX = 5, 200  # för aktier
ADR_MIN = 3.0  # 3% dagsrörelse möjlighetskrav
TOP_N = 10

GOOD_US_EXCH = {"NASDAQ", "NYSE", "ARCA", "AMEX", "SMART", "ISLAND", "BATS", "IEX"}
# Bredare primärbörser i USA (fångar fler små/mid caps och ETF:er)

# Begränsa hur många kandidater vi bearbetar per bokstav/siffra (balans: brett men snabbt)
MAX_PER_BUCKET = (
    120  # 80 x 36 ≈ upp till ~2880 råkandidater – räcker för >1000 kvalificerade
)

GOOD_EU_EXCH = {"LSE", "IBIS", "SBF", "BVME", "SWX", "VIRTX", "AEB", "BATEEN", "CHIXEN"}
GOOD_ASIA_EXCH = {"SEHK", "TSE", "OSE", "SGX", "BSE", "NSE", "SZSE", "SSE"}


# FX-par (IB-format EUR.USD etc hanteras av ib_insyncs Forex('EURUSD'))
FX_PAIRS = [
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURJPY",
    "EURGBP",
    "GBPJPY",
    "AUDJPY",
    "CHFJPY",
    "EURNZD",
    "EURCAD",
]
# --- Futures (symbol, exchange) ---
FUTS = [
    ("ES", "GLOBEX"),  # E-mini S&P 500 (CME Globex)
    ("NQ", "GLOBEX"),  # E-mini Nasdaq-100 (CME Globex)
    ("YM", "ECBOT"),  # Mini Dow (CBOT)
    ("RTY", "GLOBEX"),  # E-mini Russell 2000 (CME Globex)
    ("ZB", "ECBOT"),  # 30Y T-Bond (CBOT)
    ("ZN", "ECBOT"),  # 10Y T-Note (CBOT)
    ("GC", "COMEX"),  # Gold (COMEX)
    ("SI", "COMEX"),  # Silver (COMEX)
    ("HG", "COMEX"),  # Copper (COMEX)
]

# Futures root-symboler med exchange (vi plockar frontkontrakt dynamiskt)
FUT_SPECS = [
    ("ES", "GLOBEX"),  # S&P 500
    ("NQ", "GLOBEX"),  # Nasdaq 100
    ("YM", "ECBOT"),  # Dow
    ("RTY", "NYMEX"),  # Russell 2000 (via CME/NYMEX routing)
    ("CL", "NYMEX"),  # Crude Oil
    ("GC", "NYMEX"),  # Gold (COMEX routas via NYMEX API)
    ("SI", "NYMEX"),  # Silver
    ("ZB", "ECBOT"),  # 30Y Treasury Bond
    ("ZN", "ECBOT"),  # 10Y Note
    ("HG", "NYMEX"),  # Copper
]


# ---------- helpers ----------
# --- CSV-universum: läs SYMBOL-kolumn ---
def load_universe_symbols(csv_path: str) -> list[str]:
    """
    Läser en CSV med en kolumn 'SYMBOL' (eller 'Symbol'/'Ticker').
    Returnerar en unik, övre-case lista.
    """
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return []
    cols = [c for c in df.columns]
    col = None
    for c in cols:
        if str(c).strip().upper() in {"SYMBOL", "TICKER"}:
            col = c
            break
    if col is None:
        return []
    syms = df[col].dropna().astype(str).str.strip().str.upper().unique().tolist()
    return syms


# --- helper: qualify or skip ---
from ib_insync import Contract


# --- helper: qualify or skip ---
def qualify_or_none(ib: IB, contract: Contract):
    """
    Försök kvalificera kontraktet. Returnerar kvalificerat kontrakt eller None.
    Testar först SMART, därefter primaryExchange direkt (vanligt krav för SEHK/SGX).
    """
    try:
        q = ib.qualifyContracts(contract)
        if q:
            return q[0]
    except Exception:
        pass
    # Andra försök: använd primaryExchange som exchange om den finns
    try:
        prim = getattr(contract, "primaryExchange", None)
        if prim:
            c2 = contract.clone()
            c2.exchange = prim
            q = ib.qualifyContracts(c2)
            if q:
                return q[0]
    except Exception:
        pass
    return None


def is_asia_problem(con: Contract) -> bool:
    """
    Returnerar True för kända strul-kandidater som ger Error 200:
    - Hongkong (SEHK) och Singapore (SGX)
    - Numeriska HK-symboler (t.ex. '700', '9988' osv.)
    """
    prim = getattr(con, "primaryExchange", "") or ""
    sym = getattr(con, "symbol", "") or ""
    if prim.upper() in {"SEHK", "SGX"}:
        return True
    if sym.isdigit():
        return True
    return False


def get_front_future_or_none(ib: IB, symbol: str, exchange: str):
    """
    Hämta 'front' för en future (t.ex. ES på GLOBEX) med säkert fallback.
    Returnerar kvalificerat kontrakt eller None om IB inte hittar något.
    """
    try:
        # 1) Be IB lista alla kontraktdetaljer för symbolen på given exchange
        cds = ib.reqContractDetails(Future(symbol=symbol, exchange=exchange))
    except Exception:
        return None
    if not cds:
        return None

    # 2) Sortera på närmast förfall (lastTradeDateOrContractMonth)
    def keyfun(cd):
        # lastTradeDateOrContractMonth kan vara 'YYYYMM' eller 'YYYYMMDD'
        s = cd.contract.lastTradeDateOrContractMonth or "99999999"
        return s

    cds_sorted = sorted(cds, key=keyfun)

    # 3) Plocka första som går att kvalificera
    for cd in cds_sorted:
        qc = qualify_or_none(ib, cd.contract)
        if qc:
            return qc
    return None


def info(msg):
    print(msg, flush=True)


def adr_pct(df: pd.DataFrame) -> pd.Series:
    hi = df["high"].rolling(20).max()
    lo = df["low"].rolling(20).min()
    return (hi - lo) / df["close"] * 100


def slope(s: pd.Series, n=5) -> float:
    if len(s) < n:
        return 0.0
    y = s.tail(n).values
    x = np.arange(len(y))
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def histDF(ib: IB, con: Contract, what="TRADES") -> pd.DataFrame:
    bars = ib.reqHistoricalData(
        con,
        endDateTime="",
        durationStr="270 D",
        barSizeSetting="1 day",
        whatToShow=what,
        useRTH=True,
        formatDate=2,
    )
    return util.df(bars)


def evaluate_series(df: pd.DataFrame):
    if df.empty or len(df) < 220:
        return None
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["atr20"] = (df["high"] - df["low"]).rolling(20).mean()
    df["adr%"] = adr_pct(df)
    last = df.iloc[-1]

    if float(last["adr%"]) < ADR_MIN:
        return None

    dist = abs(last["close"] - last["ma20"])
    near_ma20 = dist <= 0.5 * float(df["atr20"].iloc[-1])

    if len(df) >= 60:
        mom60 = (df["close"].iloc[-1] / df["close"].iloc[-60] - 1) * 100
    else:
        mom60 = 0.0
    ma20_s = slope(df["ma20"], 5)

    above200 = last["close"] > last["ma200"]
    below200 = last["close"] < last["ma200"]
    go_long = above200 and near_ma20 and (mom60 > 0) and (ma20_s > 0)
    go_short = below200 and near_ma20 and (mom60 < 0) and (ma20_s < 0)
    if not (go_long or go_short):
        return None

    side = "LONG" if go_long else "SHORT"
    entry = float(last["ma20"])
    tp = entry * (1.03 if side == "LONG" else 0.97)
    sl = entry * (0.97 if side == "LONG" else 1.03)

    score = (
        0.35 * (1 - min(1, dist / max(1e-6, float(df["atr20"].iloc[-1]))))
        + 0.35 * min(1, float(last["adr%"]) / 6.0)
        + 0.30 * np.tanh(abs(mom60) / 25.0)
    )

    return {
        "price": round(float(last["close"]), 4),
        "ma20": round(float(last["ma20"]), 4),
        "ma200": round(float(last["ma200"]), 4),
        "adr_pct": round(float(last["adr%"]), 2),
        "mom60": round(float(mom60), 2),
        "entry": round(entry, 4),
        "tp": round(tp, 4),
        "sl": round(sl, 4),
        "score": round(float(score), 3),
    }


def collect_from_csv_or_fallback(ib: IB, csv_path="universe_us.csv") -> list[Stock]:
    """
    Försöker läsa SYMBOL-lista från csv_path.
    Om filen saknas eller är tom: använder din befintliga fallback (collect_us_eu_asia_stocks).
    """
    syms = load_universe_symbols(csv_path)
    if not syms:
        info(
            f"[STK] Ingen '{csv_path}' hittad eller tom – använder fallback med reqMatchingSymbols …"
        )
        return collect_us_eu_asia_stocks(ib)

    info(f"[STK] Läser universum från CSV: {csv_path} ({len(syms)} symboler)")
    # Bygg SMART/USD-kontrakt och batch-kvalificera för fart & sanity
    # OBS: detta är en lätt kvalificering så vi kan hämta historik senare.
    to_qualify = []
    seen = set()
    for sym in syms:
        if not sym or sym in seen:
            continue
        seen.add(sym)
        to_qualify.append(Stock(symbol=sym, exchange="SMART", currency="USD"))

    def _chunks(lst, n=100):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]

    qualified: list[Stock] = []
    for batch in _chunks(to_qualify, 100):
        try:
            qcs = ib.qualifyContracts(*batch)
        except Exception:
            qcs = []
        for qc in qcs:
            prim = (getattr(qc, "primaryExchange", "") or "").upper()
            # hoppa skräpmarknader här också
            if prim in {"PINK", "OTC", "OTCM"}:
                continue
            qualified.append(qc)

    info(
        f"[STK/CSV] Kandidater efter kvalificering: {len(qualified)} (från {len(syms)} SYMBOL i CSV)"
    )
    return qualified


# ---------- universe builders ----------
def collect_us_eu_asia_stocks(ib: IB) -> list[Stock]:
    """
    NY version: Bygger ett stort USA-universum (NYSE, NASDAQ, ARCA) i USD.
    - Hämtar A–Z + 0–9 via reqMatchingSymbols
    - Filtrerar till STK i USD och primärbörs i US_EXCHANGES
    - Kvalificerar säkert med qualify_or_none
    - Hoppar OTC/PINK och Asien-strul (säkerhetsbälte)
    """
    import string

    chars = list(string.ascii_uppercase) + list("0123456789")

    seen = set()  # (symbol, primaryExchange)
    candidates: list[Stock] = []
    total_hits = 0

    info("[STK] Hämtar US-symboler via reqMatchingSymbols A–Z/0–9 …")
    for i, ch in enumerate(chars, 1):
        try:
            ms = ib.reqMatchingSymbols(ch)
        except Exception:
            ms = []

        # Förfiltrera till STK + USD + (primärbörs i US_EXCHANGES)
        # Förfiltrera till STK + USD + bred US-börs (primär eller exchange)
        filtered = []
        for m in ms:
            c = m.contract
            if getattr(c, "secType", "") != "STK":
                continue
            cur = (getattr(c, "currency", "") or "").upper()
            prim = (getattr(c, "primaryExchange", "") or "").upper()
            exch = (getattr(c, "exchange", "") or "").upper()
            if cur != "USD":
                continue
            if not (prim in US_EXCHANGES_WIDE or exch in US_EXCHANGES_WIDE):
                continue
            filtered.append(m)

        # Begränsa per bokstav/siffra för fart
        filtered = filtered[:MAX_PER_BUCKET]

        total_hits += len(filtered)
        try:
            info(
                f"  [{i}/{len(chars)}] US-matches={len(ms)} → kept={len(filtered)} för '{ch}'"
            )

        except Exception:
            print(f"  [{i}/{len(chars)}] US-matches={len(filtered)} för '{ch}'")

            # KÖR BATCH-KVALIFICERING (snabbt)
        to_qualify = []
        for m in filtered:
            sym = m.contract.symbol
            prim = (m.contract.primaryExchange or "").upper()
            key = sym.upper()
            if key in seen:
                continue
            seen.add(key)

            con = Stock(
                symbol=sym, exchange="SMART", currency="USD", primaryExchange=prim
            )
            if "is_asia_problem" in globals() and is_asia_problem(con):
                continue
            to_qualify.append(con)

        # kvalificera i batchar om ~50
        def _chunks(lst, n=50):
            for j in range(0, len(lst), n):
                yield lst[j : j + n]

        for batch in _chunks(to_qualify, 50):
            try:
                qcs = ib.qualifyContracts(*batch)
            except Exception:
                qcs = []
            for qc in qcs:
                prim_qc = (getattr(qc, "primaryExchange", "") or "").upper()
                if prim_qc in {"PINK", "OTC", "OTCM"}:
                    continue
                candidates.append(qc)

        # liten paus så vi inte spammar TWS
        time.sleep(0.005)

    info(
        f"[STK/US] Kandidater efter kvalificering: {len(candidates)} (hits totalt: {total_hits}, unika: {len(seen)})"
    )
    return candidates


def collect_fx(ib: IB) -> list[Forex]:
    info("[FX] Lägger till major/minor-par …")
    return [Forex(p) for p in FX_PAIRS]


def collect_futures(ib: IB):
    print("[FUT] Hämtar frontkontrakt via reqContractDetails …")
    fut_candidates = []
    for sym, exch in FUTS:
        qc = get_front_future_or_none(ib, sym, exch)
        if qc is None:
            print(f"[FUT][skip] no security definition: {sym} / {exch}")
            continue
        fut_candidates.append(qc)
    print(f"[FUT] Kandidater: {len(fut_candidates)}")
    return fut_candidates


# ---------- evaluation ----------
def evaluate_contract(ib: IB, con: Contract):
    try:
        # dämpa SEHK/SGX och numeriska HK-koder
        if is_asia_problem(con):
            return None

        qc = qualify_or_none(ib, con)
        if qc is None:
            return None
        con = qc
        if con.secType == "STK":
            df = histDF(ib, con, "TRADES")
            res = evaluate_series(df)
            if not res:
                return None
            price = res["price"]
            if not (PRICE_MIN <= price <= PRICE_MAX):
                return None
            side = "LONG" if res["entry"] >= res["sl"] else "SHORT"
            qty100 = (
                max(1, int(math.floor(100 / res["entry"]))) if res["entry"] > 0 else 1
            )
        elif con.secType == "CASH":
            df = histDF(ib, con, "MIDPOINT")
            res = evaluate_series(df)
            if not res:
                return None
            side = "LONG" if res["entry"] >= res["sl"] else "SHORT"
            qty100 = 100  # FX kvantitet hanteras senare; placeholder
        elif con.secType == "FUT":
            df = histDF(ib, con, "TRADES")
            res = evaluate_series(df)
            if not res:
                return None
            side = "LONG" if res["entry"] >= res["sl"] else "SHORT"
            qty100 = 1  # kontraktsbaserat; placeholder
        else:
            return None

        sym = con.localSymbol or con.symbol
        exch = getattr(con, "primaryExchange", "") or con.exchange

        return {
            "symbol": sym,
            "secType": con.secType,
            "exch": exch,
            **res,
            "side": side,
            "qty_$100": qty100,
        }
    except Exception:
        return None


def main():
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
    all_cons: list[Contract] = []

    # 1) Bygg universum
    stocks = collect_from_csv_or_fallback(ib, "universe_us.csv")

    fx = collect_fx(ib)
    futs = collect_futures(ib) if FUT_ENABLE else []
    if not FUT_ENABLE:
        info("[FUT] avstängt via toggle – hoppar över futures.")

    all_cons.extend(stocks)
    all_cons.extend(fx)
    all_cons.extend(futs)

    info(f"[ALL] Totalt kandidater före analys: {len(all_cons)}")

    # 2) Analys – iterativt med progress (IB har rate limits; kör snällt men tydligt)
    rows = []
    for i, con in enumerate(all_cons, 1):
        r = evaluate_contract(ib, con)
        if r:
            rows.append(r)
        if i % 25 == 0:
            info(f"  [analyse] {i}/{len(all_cons)} klart … hit: {len(rows)} träffar")
            time.sleep(0.1)  # snäll paus för IBKR

    ib.disconnect()

    if not rows:
        info("Inga kandidater uppfyllde reglerna (kan bero på market data-begr.).")
        return

    df = (
        pd.DataFrame(rows)
        .sort_values(["score", "adr_pct"], ascending=[False, False])
        .head(TOP_N)
        .reset_index(drop=True)
    )
    cols = [
        "symbol",
        "secType",
        "exch",
        "side",
        "price",
        "ma20",
        "ma200",
        "adr_pct",
        "mom60",
        "entry",
        "tp",
        "sl",
        "qty_$100",
        "score",
    ]
    print(df[cols])
    df[cols].to_csv("top10.csv", index=False)
    info(
        "\n[OK] top10.csv skapad – bekräfta 15 min efter open mot MA20 på 5–15m innan order."
    )
    info(
        "Regel: LONG om pris>MA20 och tar ut första 15m-high; SHORT om pris<MA20 och tar ut första 15m-low. SL/TP = ±3%."
    )


if __name__ == "__main__":
    main()

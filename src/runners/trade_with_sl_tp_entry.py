import os, json, requests, sys, time
import math
import requests


def _round_up_to_step(x: float, step: float) -> float:
    return math.ceil(x / step) * step


def get_instrument_rules(session: requests.Session, base_url: str, epic: str) -> dict:
    """
    Hämtar regler för storlek/stege/värde från /api/v1/markets/{epic}.
    Returnerar normaliserat dict: min_size, size_step, max_size (ev.), min_notional (ev.), contract_size
    """
    # RÄTT endpoint för single market details:
    r = session.get(f"{base_url}/api/v1/markets/{epic}")
    r.raise_for_status()
    data = r.json()

    instrument = data.get("instrument", {})  # t.ex. lotSize, type, name, currency
    rules = data.get(
        "dealingRules", {}
    )  # t.ex. minDealSize, minSizeIncrement, maxDealSize
    snapshot = data.get("snapshot", {})  # t.ex. bid/offer (pris)

    # Plocka regler med robust fallback
    min_deal = (rules.get("minDealSize", {}) or {}).get("value", None)
    size_inc = (rules.get("minSizeIncrement", {}) or {}).get("value", None)
    max_deal = (rules.get("maxDealSize", {}) or {}).get("value", None)

    # Capital.com har inte alltid “minNotional”; lämna None om saknas
    min_notional = None

    # contract_size ≈ lotSize (för aktie-CFD är det vanligen 1)
    contract_size = instrument.get("lotSize", 1)

    # Fallback om något saknas
    if min_deal is None:
        min_deal = 1
    if size_inc is None:
        size_inc = 1

    return {
        "min_size": float(min_deal),
        "size_step": float(size_inc),
        "max_size": float(max_deal) if max_deal is not None else None,
        "min_notional": float(min_notional) if min_notional is not None else None,
        "contract_size": float(contract_size),
        # Skickar med snapshot om du vill återanvända pris härifrån
        "last_bid": (
            float(snapshot.get("bid")) if snapshot.get("bid") is not None else None
        ),
        "last_offer": (
            float(snapshot.get("offer")) if snapshot.get("offer") is not None else None
        ),
    }


def get_last_price(session: requests.Session, base_url: str, epic: str) -> float:
    """
    Hämtar pris från samma /api/v1/markets/{epic} och använder offer/bid (eller mittpris).
    """
    r = session.get(f"{base_url}/api/v1/markets/{epic}")
    r.raise_for_status()
    js = r.json().get("snapshot", {})  # innehåller bid/offer
    bid = js.get("bid")
    offer = js.get("offer")

    if offer is not None and bid is not None:
        return (float(bid) + float(offer)) / 2.0  # mittpris
    if offer is not None:
        return float(offer)
    if bid is not None:
        return float(bid)
    raise RuntimeError("Kunde inte läsa pris (bid/offer) för instrumentet.")


def compute_valid_size(
    rules: dict, price: float, desired_notional: float | None = 100.0
) -> float:
    """
    desired_notional = mål i USD för positionens värde (kan vara None => lägsta tillåtna).
    """
    min_size = rules["min_size"]
    step = rules["size_step"]
    max_size = rules["max_size"]
    min_notional = rules["min_notional"]
    contract = rules["contract_size"]

    size = _round_up_to_step(min_size, step)

    if desired_notional:
        raw = desired_notional / (price * contract)
        size = max(size, _round_up_to_step(raw, step))

    if min_notional:
        needed = min_notional / (price * contract)
        size = max(size, _round_up_to_step(needed, step))

    if max_size is not None and size > max_size:
        raise ValueError(f"Beräknad storlek {size} överskrider maxSize {max_size}")

    return float(size)


def prepare_size_for_order(
    session, base_url, epic, desired_notional_usd: float | None = 100.0
) -> float:
    rules = get_instrument_rules(session, base_url, epic)
    price = get_last_price(session, base_url, epic)
    size = compute_valid_size(rules, price, desired_notional=desired_notional_usd)
    print(
        f"[rules] min_size={rules['min_size']} step={rules['size_step']} "
        f"min_notional={rules['min_notional']} contract={rules['contract_size']}"
    )
    print(f"[price] {price}  -> [size] {size}")
    return size


# === /SIZE-HELPERS ===


BASE = os.getenv("CAPITAL_BASE", "https://demo-api-capital.backend-capital.com")
LOGIN_URL = f"{BASE}/api/v1/session"
ACCOUNTS_URL = f"{BASE}/api/v1/accounts/me"
SEARCH_URL = f"{BASE}/api/v1/markets"
ORDER_URL = f"{BASE}/api/v1/positions"
CONFIRM_URL = f"{BASE}/api/v1/confirms"

EMAIL = os.getenv("CAPITAL_LOGIN")
PASS = os.getenv("CAPITAL_PASSWORD")
APIKEY = os.getenv("CAPITAL_API_KEY")

if not (EMAIL and PASS and APIKEY):
    print("[FEL] Sätt CAPITAL_LOGIN, CAPITAL_PASSWORD och CAPITAL_API_KEY.")
    sys.exit(1)

# --- base headers (API key krävs redan vid login) ---
hdr = {"X-CAP-API-KEY": APIKEY, "Content-Type": "application/json"}


def login():
    r = requests.post(
        LOGIN_URL,
        headers=hdr,
        data=json.dumps({"identifier": EMAIL, "password": PASS}),
        timeout=20,
    )
    if r.status_code != 200:
        raise SystemExit(f"Login misslyckades: {r.text}")
    hdr["X-SECURITY-TOKEN"] = r.headers["X-SECURITY-TOKEN"]
    hdr["CST"] = r.headers["CST"]
    print("[login] OK")


def search_epic(search_term="AMC"):
    r = requests.get(
        SEARCH_URL, headers=hdr, params={"searchTerm": search_term}, timeout=20
    )
    r.raise_for_status()
    items = r.json().get("markets", [])
    if not items:
        raise SystemExit(f"Hittade inga markets för '{search_term}'.")
    # ta första med epic
    for it in items:
        if "epic" in it:
            return it["epic"], it.get(
                "instrumentName", it.get("marketName", it["epic"])
            )
    raise SystemExit("Inga epic i svar.")


def size_for_margin_at_price(session, base_url, epic, margin_usd, price):
    """Size = (margin * leverage) / price, rundat till minDealSize/minSizeIncrement."""
    lev = get_leverage(session, base_url, epic)
    data = session.get(f"{base_url}/api/v1/markets/{epic}", timeout=10).json()
    rules = data.get("dealingRules", {}) or {}
    min_deal = float((rules.get("minDealSize", {}) or {}).get("value", 1))
    step = float((rules.get("minSizeIncrement", {}) or {}).get("value", 1))
    import math

    raw = (float(margin_usd) * lev) / float(price)
    size = max(raw, min_deal)
    size = math.ceil(size / step) * step
    return float(size)


def place_limit_buy_no_levels(epic: str, size: float, level: float):
    """
    Skapar en LIMIT (working) order MED SL/TP direkt.
    - Entry = level (avrundas till 2 d.p. för TSLA)
    - SL/TP ≈ ±3% från entry (justeras upp till minsta tillåtna avstånd)
    - SL/TP skickas som DISTANCE (stopDistance/limitDistance) eftersom Capital kräver det för working orders.
    """
    # 1) Entry och grundavstånd ~3%
    level = round(float(level), 2)  # TSLA har 2 decimaler
    pct = 0.03
    sl_level = level * (1 - pct)
    tp_level = level * (1 + pct)

    # 2) Avstånd (distance) i pris, avrundat till tick
    #    stopDistance = entry - SL, limitDistance = TP - entry
    stop_dist = round(level - sl_level, 2)
    limit_dist = round(tp_level - level, 2)

    # 3) Läs minimiavstånd och tick-steg från market-detaljer
    #    - minStepDistance: POINTS (tick-steg)
    #    - minStopOrProfitDistance: kan vara PERCENTAGE eller POINTS
    md = requests.get(f"{BASE}/api/v1/markets/{epic}", headers=hdr, timeout=10)
    md.raise_for_status()
    info = md.json()
    dealing = info.get("dealingRules", {}) or {}

    # Tick-steg
    min_step = float((dealing.get("minStepDistance", {}) or {}).get("value", 0.01))

    # Min stop/profit avstånd
    msop = dealing.get("minStopOrProfitDistance", {}) or {}
    msop_val = float(msop.get("value", 0.0))
    msop_unit = (msop.get("unit") or "POINTS").upper()

    if msop_unit == "PERCENTAGE":
        min_abs = round(level * (msop_val / 100.0), 2)
    else:
        min_abs = round(msop_val, 2)

    # 4) Justera upp våra avstånd till minsta tillåtna och till tick-steg
    def align_distance(d, step):
        # säkerställ minsta avstånd, och justera till multipel av step (uppåt)
        import math

        d = max(d, min_abs)
        # runda upp till närmsta step
        return round(math.ceil(d / step) * step, 2)

    stop_dist = align_distance(stop_dist, min_step)
    limit_dist = align_distance(limit_dist, min_step)

    # 5) Bygg payload – working order med distances
    payload = {
        "epic": epic,
        "expiry": "-",
        "direction": "BUY",
        "orderType": "LIMIT",
        "type": "LIMIT",  # <-- krävs (fixar error.notnull.createworkingorderrequest.type)
        "level": float(level),
        "size": float(size),
        "forceOpen": True,
        "guaranteedStop": False,
        "timeInForce": "GOOD_TILL_CANCELLED",
        "currencyCode": "USD",
        "stopDistance": float(stop_dist),  # SL som distance
        "profitDistance": float(limit_dist),
    }

    url = f"{BASE}/api/v1/workingorders"  # korrekt endpoint i din miljö

    r = requests.post(url, headers=hdr, json=payload, timeout=10)
    if r.status_code in (401, 403):
        print("[auth] session expired – re-login")
        login()
        r = requests.post(url, headers=hdr, json=payload, timeout=10)

    if r.status_code >= 400:
        print("[wo ERROR]", r.status_code, r.text)

    r.raise_for_status()
    js = r.json()
    deal_ref = js.get("dealReference")
    print(
        f"[limit-order skickad] epic={epic} size={size} level={level} stopDist={stop_dist} limitDist={limit_dist} dealRef={deal_ref}"
    )
    return deal_ref


# === SL/TP helpers ===
def _get_tick_decimals(session, base_url, epic):
    """Läser hur många decimaler instrumentet tillåter (snapshot.decimalPlacesFactor).
    Fallback: 2."""
    r = session.get(f"{base_url}/api/v1/markets/{epic}")
    r.raise_for_status()
    js = r.json()
    dec = js.get("snapshot", {}).get("decimalPlacesFactor")
    try:
        return int(dec) if dec is not None else 2
    except:
        return 2


def calc_sl_tp_levels_from_entry(entry_price: float, pct: float, decimals: int):
    """Beräknar SL/TP som ±pct från entry och rundar till tillåtna decimals."""
    tp = round(entry_price * (1.0 + pct), decimals)
    sl = round(entry_price * (1.0 - pct), decimals)
    return sl, tp


# ======================


# === SIZE FROM MARGIN ===
def get_leverage(session, base_url, epic):
    r = session.get(f"{base_url}/api/v1/markets/{epic}")
    r.raise_for_status()
    data = r.json()

    instrument = data.get("instrument", {})
    dealing = data.get("dealingRules", {})

    margin_factor = None
    margin_unit = None

    # Fall 1: instrument.marginFactor (kan vara siffra eller dict)
    mf = instrument.get("marginFactor")
    if mf is not None:
        if isinstance(mf, dict) and "value" in mf:
            margin_factor = float(mf["value"])
        else:
            margin_factor = float(mf)
        margin_unit = instrument.get("marginFactorUnit")

    # Fall 2: dealingRules.marginFactor.value
    if margin_factor is None:
        mf2 = dealing.get("marginFactor")
        if isinstance(mf2, dict) and "value" in mf2:
            margin_factor = float(mf2["value"])
            margin_unit = mf2.get("unit", margin_unit)

    # Fall 3: dealingRules.marginRequirement.value
    if margin_factor is None:
        mr = dealing.get("marginRequirement")
        if isinstance(mr, dict) and "value" in mr:
            margin_factor = float(mr["value"])
            margin_unit = mr.get("unit", margin_unit)

    if margin_factor is None:
        print(data)  # för felsökning om det skiter sig
        raise Exception(f"Hittade ingen marginFactor för {epic}")

    # Om unit är PERCENTAGE eller värdet > 1, tolka som procent (t.ex. 20 => 20%)
    if (margin_unit or "").upper() == "PERCENTAGE" or margin_factor > 1:
        margin_requirement = margin_factor / 100.0
    else:
        margin_requirement = margin_factor

    leverage = 1.0 / margin_requirement
    print(
        f"[leverage] raw={margin_factor} unit={margin_unit} -> margin_req={margin_requirement} -> lev={leverage:.4f}"
    )
    return leverage


def _get_tick_decimals(session, base_url, epic):
    r = session.get(f"{base_url}/api/v1/markets/{epic}", timeout=10)
    r.raise_for_status()
    dec = r.json().get("snapshot", {}).get("decimalPlacesFactor")
    try:
        return int(dec) if dec is not None else 2
    except:
        return 2


def attach_sl_tp(deal_id: str, sl_level: float, tp_level: float):
    """Sätter SL/TP som prisnivåer efter fill."""
    payload = {
        "stopLevel": float(sl_level),
        "profitLevel": float(tp_level),
    }
    url = f"{BASE}/api/v1/positions/{deal_id}"
    r = requests.put(url, headers=hdr, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[attach] SL={sl_level} TP={tp_level} på dealId={deal_id}")


def calculate_size_from_margin(session, base_url, epic, margin_usd):
    # Hämta marknadsdata + regler i ETT anrop
    r = session.get(f"{base_url}/api/v1/markets/{epic}")
    r.raise_for_status()
    data = r.json()

    snapshot = data.get("snapshot", {}) or {}
    offer = snapshot.get("offer") or snapshot.get("bid")
    if offer is None:
        raise Exception("Kunde inte läsa pris (offer/bid) för instrumentet.")
    price = float(offer)

    # Leverage
    # (använd samma logik som i get_leverage men vi får gärna kalla funktionen direkt)
    lev = get_leverage(session, base_url, epic)

    # Rå size som ger önskad margin
    # margin = (size * price) / lev  => size = margin * lev / price
    raw_size = (float(margin_usd) * lev) / price

    # Regler: minDealSize, minSizeIncrement, maxDealSize
    dealing = data.get("dealingRules", {}) or {}
    min_deal = (dealing.get("minDealSize", {}) or {}).get("value", 1)
    step = (dealing.get("minSizeIncrement", {}) or {}).get("value", 1)
    max_deal = (dealing.get("maxDealSize", {}) or {}).get("value", None)

    # Avrunda uppåt till step och respektera min/max
    import math

    size = max(raw_size, float(min_deal))
    size = math.ceil(size / float(step)) * float(step)
    if max_deal is not None and size > float(max_deal):
        size = float(max_deal)

    exp_margin = size * price / lev
    print(
        f"[calc] price={price} lev={lev:.4f} raw_size={raw_size:.4f} -> size={size} (min={min_deal}, step={step})"
    )
    print(
        f"[margin-check] expected margin ≈ ${exp_margin:.2f} (target ${margin_usd:.2f})"
    )
    return float(size)


# =========================
def place_market_buy_no_levels(epic: str, size: float):
    payload = {
        "epic": epic,
        "direction": "BUY",
        "orderType": "MARKET",
        "size": float(size),
        # inga stop/profit här – läggs efter fill
    }

    url = f"{BASE}/api/v1/positions"
    r = requests.post(url, headers=hdr, json=payload, timeout=10)
    if r.status_code in (401, 403):
        print("[auth] session expired – re-login")
        login()
        r = requests.post(url, headers=hdr, json=payload, timeout=10)

    r.raise_for_status()
    js = r.json()
    deal_ref = js.get("dealReference")  # t.ex. "o_...."
    print(f"[order skickad] epic={epic} size={size} dealRef={deal_ref}")
    return deal_ref


def place_market_buy(
    epic: str,
    size: float,
    stop_loss_level: float | None = None,
    take_profit_level: float | None = None,
    stop_loss_distance: float | None = None,
    take_profit_distance: float | None = None,
):
    """
    Försök i ordning:
      1) POST /api/v1/orders           (levels)
      2) POST /api/v1/orders/          (levels)
      3) POST /api/v1/orders           (distance)
      4) POST /api/v1/orders/          (distance)
      5) POST /api/v1/positions/otc    (distance, IG/Capital-stacken)
      6) re-login + (5) igen
    Returnerar dealReference om OK, annars kastar HTTPError.
    """

    def _post(url, payload, sess=None):
        s = sess or requests
        return s.post(url, headers=hdr, json=payload)

    # --- levels-payload ---
    payload_levels = {
        "epic": epic,
        "direction": "BUY",
        "orderType": "MARKET",
        "size": float(size),
    }
    if stop_loss_level is not None:
        payload_levels["stopLossLevel"] = float(stop_loss_level)
    if take_profit_level is not None:
        payload_levels["takeProfitLevel"] = float(take_profit_level)

    # --- distance-payload (”Avstånd”) ---
    payload_dist = {
        "epic": epic,
        "direction": "BUY",
        "orderType": "MARKET",
        "size": float(size),
    }
    if stop_loss_distance is not None:
        payload_dist["stopLossDistance"] = float(stop_loss_distance)
    if take_profit_distance is not None:
        payload_dist["takeProfitDistance"] = float(take_profit_distance)

    urls_orders = [f"{BASE}/api/v1/orders", f"{BASE}/api/v1/orders/"]

    # 1–4) /orders (levels → distance)
    for payload in (payload_levels, payload_dist):
        kind = (
            "levels"
            if "stopLossLevel" in payload or "takeProfitLevel" in payload
            else "distance"
        )
        for url in urls_orders:
            r = _post(url, payload)
            if r.status_code == 404:
                print(f"[warn] 404 på {url} ({kind}) – provar nästa")
                continue
            if 200 <= r.status_code < 300:
                js = r.json()
                print(f"[order skickad via /orders] epic={epic} size={size} ({kind})")
                return js.get("dealReference")
            print(f"[warn] {r.status_code} på {url} ({kind}): {r.text} – går vidare")
            break

    # 5) /positions/otc (distance) – vanligt i samma stack när /orders strular
    payload_otc = {
        "epic": epic,
        "direction": "BUY",
        "size": float(size),
        "orderType": "MARKET",
        "forceOpen": True,
        # SL/TP som avstånd – motsvarar UI “Avstånd”
        "stopDistance": (
            float(stop_loss_distance) if stop_loss_distance is not None else None
        ),
        "limitDistance": (
            float(take_profit_distance) if take_profit_distance is not None else None
        ),
        # följande fält brukar accepteras utan att behöva sättas:
        # "currencyCode": "USD", "guaranteedStop": False, "timeInForce": "FILL_OR_KILL"
    }
    # ta bort None-fält
    payload_otc = {k: v for k, v in payload_otc.items() if v is not None}

    sess = requests.Session()
    url_otc = f"{BASE}/api/v1/positions/otc"
    r = _post(url_otc, payload_otc, sess=sess)
    if r.status_code == 404:
        print("[warn] 404 på /positions/otc – provar re-login och sista försök")
        login()  # uppdaterar hdr
        r = _post(url_otc, payload_otc, sess=sess)

    if 200 <= r.status_code < 300:
        js = r.json()
        print(f"[order skickad via /positions/otc] epic={epic} size={size} (distance)")
        return js.get("dealReference")

    print("[order ERROR]", r.status_code, r.text)
    raise requests.HTTPError("Kunde inte posta order – alla rutter gav fel.")


def confirm(deal_reference: str):
    url = f"{BASE}/api/v1/confirms/{deal_reference}"
    r = requests.get(url, headers=hdr, timeout=10)
    r.raise_for_status()
    js = r.json()

    # ticket/order-id (kan inte användas för PUT /positions/{id})
    ticket_deal_id = js.get("dealId")
    status = js.get("dealStatus") or js.get("status")

    # HÄR ÄR FIXEN: hämta positionens dealId från affectedDeals
    pos_deal_id = None
    affected = js.get("affectedDeals") or []
    if isinstance(affected, list) and affected:
        pos_deal_id = affected[0].get("dealId") or affected[0].get("dealIdOrigin")

    print(f"[confirm] ticket={ticket_deal_id} position={pos_deal_id} status={status}")
    # returnera positionens id om det finns, annars fallback till ticket (så vi ser fel direkt)
    return (pos_deal_id or ticket_deal_id), status


def main():
    login()

    epic, name = search_epic("TSLA")
    print(f"[val] {name}  epic={epic}")

    session = requests.Session()
    session.headers.update(hdr)

    # ===== NYTT ENTRY-BLOCK (LIMIT ORDER) =====
    ENTRY = 440.00  # önskad ingång

    # size utifrån entry (100 USD margin, leverage samma som innan)
    size = size_for_margin_at_price(session, BASE, epic, margin_usd=100, price=ENTRY)

    # Skicka LIMIT-order (working order) – utan SL/TP än
    ref = place_limit_buy_no_levels(epic, size=size, level=ENTRY)

    # Confirmar att ordern ligger i systemet
    confirm(ref)

    print(f"[ENTRY] Working order lagd på {ENTRY} för {epic}, size={size}.")
    print(f"[ENTRY] SL/TP sätts först när ordern fylls – marknadslogik oförändrad.")
    return
    # ===== SLUT NYTT BLOCK =====

    # ===================== EXISTERANDE KÖPDEL (MARKET BUY) =====================
    size = calculate_size_from_margin(session, BASE, epic, margin_usd=100)
    deal_ref = place_market_buy_no_levels(epic, size=size)
    position_deal_id, status = confirm(deal_ref)
    ...


if __name__ == "__main__":
    main()

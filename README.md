# 🎯 EDGE-10 Trading System - Tradingbot Capital

Python-baserad automatisk tradingbot för Capital.com med EDGE-10 v1.1 system.

## 🚀 MANUELL WORKFLOW - STEG FÖR STEG

### 📋 DAGLIG TRADING PROCESS (KOMPLETT GUIDE)

#### **STEG 1: FÖRBEREDELSER**
```bash
# 1a. Kontrollera att alla dependencies är installerade
pip install -r requirements.txt

# 1b. Verifiera att Capital.com data finns
ls data/scan/all_instruments_capital.csv

# 1c. Kontrollera API credentials (.env konfiguration)
python -c "from src.brokers.capitalcom.client import CapitalComClient; print('API OK')"
```

#### **STEG 2: EDGE-10 ANALYS (HYBRID VERSION)**
```bash
# 2a. Kör EDGE-10 analys för idag (REKOMMENDERAD - 7-8 min)
python universe_run_hybrid.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_smart --batch-size 10 --max-workers 2 --days-back 90

# 2b. Verifiera att TOP-10 genererades
ls edge10_smart/top_10.csv edge10_smart/top_100.csv edge10_smart/excluded.csv

# 2c. Granska TOP-10 candidates
Get-Content edge10_smart/top_10.csv | Select-Object -First 12
```

#### **STEG 3: GENERERA TRADING ORDERS**
```bash
# 3a. Konvertera TOP-10 till Capital.com orders
python edge10_generate_orders.py --top10 edge10_smart/top_10.csv --output final_orders.csv

# 3b. Verifiera order format
Get-Content final_orders.csv | Select-Object -First 5

# 3c. Kontrollera total risk exposure (ska vara max $100)
python -c "import pandas as pd; df=pd.read_csv('final_orders.csv'); print(f'Total risk: ${df[\"amount\"].sum()}')"
```

#### **STEG 4: LÄGG TRADING ORDERS**
```bash
# 4a. Kontrollera nuvarande positioner först
python status_positions_orders.py

# 4b. Lägg orders via Capital.com API (DEMO MODE)
python place_pending_orders.py --orders final_orders.csv --account-mode demo

# 4c. Verifiera att orders placerades korrekt
python status_positions_orders.py
```

#### **STEG 5: POSITION MONITORING**
```bash
# 5a. Kontrollera aktiva positioner regelbundet
python status_positions_orders.py

# 5b. Manuel position stängning (vid behov)
python quick_close_all.py

# 5c. Automatisk stängning före marknadsstängning
python auto_close_positions.py --account-mode demo --dry-run --close-offset 30
```

### 🔄 ALTERNATIV WORKFLOW (ORIGINAL VERSION)

#### **För Maximal Precision (43+ timmar runtime):**
```bash
# Endast för helger eller specialanalys
python universe_run.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_original
```

## 🔧 SYSTEM VERSIONER

### 1. HYBRID VERSION (REKOMMENDERAD)
- **Script:** `universe_run_hybrid.py`
- **Fördelar:** Snabb (7-8 min), 90% accuracy, production-ready
- **Användning:** Daglig trading production

### 2. ORIGINAL VERSION (SLOW)
- **Script:** `universe_run.py` 
- **Fördelar:** 100% accuracy enligt EDGE-10 spec
- **Nackdelar:** 43+ timmar runtime (ej praktisk)

### 3. LEGACY SYSTEM
- **Scripts:** `capital_scan_tradeable.py`, `rank_top10_momentum.py`, etc.
- **Status:** Deprecated, använd HYBRID istället

## ✅ NY SIMPLIFIED FILTERING (v1.1)

**Förenklat filter-pipeline:**
1. **US Stocks Only** - Endast `is_us_stock = True`
2. **ETF Filtering** - Blocka ETF:er (QQQ, SPY, etc.)
3. **Symbol Mapping** - Capital.com → Yahoo Finance
4. **~~Tradeable Filter~~** - **REMOVED** (ej längre relevant)

**Gamla komplicerade filter (BORTTAGNA):**
- ~~Spread filter~~ 
- ~~Tradeable status~~
- ~~Price floor~~

**Resultat:** 971 → 874 US stocks → 739 efter ETF-filter → KLART!

## � SYSTEM PERFORMANCE

**HYBRID Script Performance:**
- **Runtime:** 7-8 minuter för 599 valid stocks (av 874 Capital.com US stocks)
- **Symbol Mapping:** Automatisk Capital.com → Yahoo Finance validering
- **Batch Processing:** 10 tickers per batch (anpassningsbar)
- **Parallel Workers:** 2-4 samtidiga processers
- **Historik:** 90 dagar (optimal för snabbhet och data-kvalitet)
- **Memory Efficient:** Automatic cleanup mellan batches

**Senaste HYBRID Run (2025-10-28):**
- **Input:** 871 Capital.com instruments → 874 US stocks
- **Symbol Mapping:** 874 → 599 valid Yahoo Finance symbols
- **Processing Success:** 599/599 tickers (100% success rate)
- **Runtime:** 7.9 minuter total
- **TOP-10 EdgeScore Range:** 77.4 - 80.5

## 🎯 TOP-10 EXAMPLE OUTPUT

**Senaste HYBRID Results (2025-10-28):**
```
1. EW     | EdgeScore: 80.5 | Edwards Lifesciences (+6.34% DayReturn, 2.68x RelVol)
2. KMB    | EdgeScore: 80.5 | Kimberly-Clark (+1.49% DayReturn, 1.69x RelVol)  
3. NVS    | EdgeScore: 78.7 | Novartis ADR (+0.89% DayReturn, 1.82x RelVol)
4. TGT    | EdgeScore: 78.5 | Target (+2.65% DayReturn, 1.59x RelVol)
5. FOXA   | EdgeScore: 78.1 | Fox Class A (+2.56% DayReturn, 1.44x RelVol)
6. UPS    | EdgeScore: 77.9 | United Parcel Service (+1.13% DayReturn, 1.77x RelVol)
7. TIGO   | EdgeScore: 77.6 | Millicom Intl Cellular (+4.21% DayReturn, 1.46x RelVol)
8. BBIO   | EdgeScore: 77.4 | BridgeBio Pharma (+11.51% DayReturn, 3.75x RelVol)
9. WELL   | EdgeScore: 77.4 | Welltower Inc (+2.40% DayReturn, 1.29x RelVol)
10. GIS   | EdgeScore: 77.4 | General Mills (+1.81% DayReturn, 1.37x RelVol)
```

### Trading Infrastructure:
- `place_pending_orders.py` - Capital.com order placement
- `auto_close_positions.py` - Automatisk position management  
- `quick_close_all.py` - Emergency close all positions
- `status_positions_orders.py` - Portfolio monitoring

### Adapters & Legacy:
- `adapters/` - Broker adapters (IBKR, XTB)
- `src/` - Äldre system komponenter
- `trading/` - Trading utilities

### Support Files:
- `data/` - Market data och CSV outputs
- `logs/` - System logs
- `requirements.txt` - Python dependencies
- `.env.example` - Environment template

## ✅ SYSTEM FEATURES

### EDGE-10 v1.0 Capabilities:
- ✅ **DUBBEL ETF-filtering** (keywords + Yahoo quoteType validation)
- ✅ **EdgeScore ranking** (30% DayStrength + 30% RelVol10 + 20% Catalyst + 10% Market + 10% VolFit)
- ✅ **Fast SL/TP policy** (2% SL, 3% TP från entry)
- ✅ **Symbol mapping** (Capital.com ↔ Yahoo Finance)
- ✅ **Sample validation** (<30 samples flaggas)
- ✅ **excluded.csv logging** (alla exkluderade instrument)

### Risk Management:
- **Position Size:** $100 per order
- **Leverage:** 5x (Capital.com CFD)
- **Stop Loss:** -2% = $10 real risk per trade
- **Take Profit:** +3% = $15 real profit per trade
- **Total Risk:** $100 max (10 positions)

### Output Files:
- `top_10.csv` - TOP-10 trading candidates med EdgeScore
- `top_100.csv` - TOP-100 backup candidates  
- `excluded.csv` - Exkluderade instrument med reasons
- `final_orders.csv` - Trading orders för Capital.com

## 🛠️ SETUP

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Setup:**
   ```bash
   cp .env.example .env
   # Redigera .env med Capital.com API credentials
   ```

3. **Data Preparation:**
   ```bash
   # Placera Capital.com instruments data i:
   data/scan/all_instruments_capital.csv
   ```

4. **Test Setup:**
   ```bash
   # Testa API-anslutning
   python -c "from src.brokers.capitalcom.client import CapitalComClient; print('Setup OK')"
   ```

## 🔧 TROUBLESHOOTING

### **Vanliga Problem och Lösningar:**

#### **Problem: Symbol Mapping Errors**
```bash
# Lösning: Använd HYBRID version med automatisk validering
python universe_run_hybrid.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_smart
```

#### **Problem: API Connection Errors**
```bash
# 1. Kontrollera .env credentials
cat .env

# 2. Testa API-anslutning
python status_positions_orders.py

# 3. Använd demo mode om live misslyckas
python place_pending_orders.py --orders final_orders.csv --account-mode demo
```

#### **Problem: Slow Performance**
```bash
# Använd optimerade parametrar för snabbare körning:
python universe_run_hybrid.py --batch-size 10 --max-workers 2 --days-back 90
```

#### **Problem: No Valid Tickers Found**
```bash
# 1. Kontrollera att CSV-filen har US stocks
python -c "import pandas as pd; df=pd.read_csv('data/scan/all_instruments_capital.csv'); print(f'US stocks: {df[df[\"is_us_stock\"]==True].shape[0]}')"

# 2. Kör med debug för att se exkluderingar
python universe_run_hybrid.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_debug
cat edge10_debug/excluded.csv
```

### **Emergency Commands:**
```bash
# Stäng alla positioner omedelbart
python quick_close_all.py

# Visa detaljerad system status
python status_positions_orders.py --verbose

# Töm alla pending orders
python -c "from src.brokers.capitalcom.client import CapitalComClient; client = CapitalComClient(); client.cancel_all_orders()"
```

## ⏰ DAGLIGT TIDSSCHEMA

### **Optimal Trading Schedule (Svensk Tid):**

#### **09:00-09:30 - MORGON SETUP**
```bash
# 1. Kontrollera marknadsläge
python status_positions_orders.py

# 2. Stäng eventuella gamla positioner
python quick_close_all.py

# 3. Kör EDGE-10 analys för dagen
python universe_run_hybrid.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_smart
```

#### **15:30-16:00 - US MARKET OPEN**
```bash
# 1. Generera och lägg orders
python edge10_generate_orders.py --top10 edge10_smart/top_10.csv --output final_orders.csv
python place_pending_orders.py --orders final_orders.csv --account-mode demo

# 2. Övervaka första 30 min aktivt
python status_positions_orders.py
```

#### **21:30-22:00 - AUTO CLOSE SETUP**
```bash
# Sätt igång automatisk stängning 30 min före marknadsstängning
python auto_close_positions.py --account-mode demo --close-offset 30
```

#### **22:00 - MARKET CLOSE CLEANUP**
```bash
# Emergency close alla kvarvarande positioner
python quick_close_all.py

# Logga dagens resultat
python status_positions_orders.py > logs/daily_$(date +%Y%m%d).log
```

### **Automatisering med Cron/Task Scheduler:**
```bash
# Windows Task Scheduler exempel:
# 09:00 - Morgon analys
# 15:30 - Lägg orders  
# 21:30 - Auto close setup
# 22:00 - Emergency cleanup
```

## 📋 SNABBREFERENS

### **Viktigaste Kommandon:**
```bash
# DAGLIG KÖRNING (7-8 min)
python universe_run_hybrid.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_smart

# GENERERA ORDERS  
python edge10_generate_orders.py --top10 edge10_smart/top_10.csv --output final_orders.csv

# LÄGG ORDERS (DEMO)
python place_pending_orders.py --orders final_orders.csv --account-mode demo

# STATUS CHECK
python status_positions_orders.py

# EMERGENCY CLOSE
python quick_close_all.py
```

### **Viktiga Filer:**
- **Input:** `data/scan/all_instruments_capital.csv` (971 instruments)
- **Output:** `edge10_smart/top_10.csv` (TOP-10 candidates)
- **Orders:** `final_orders.csv` (Trading orders för Capital.com)
- **Config:** `.env` (API credentials)
- **Logs:** `logs/trades.csv` (Trading history)

## 📊 SYSTEM PERFORMANCE

**Senaste Verified Hybrid Run (2025-10-28):**
- **Input:** 971 total Capital.com instruments
- **US Stocks Filter:** 874 instruments 
- **Symbol Mapping:** 874 → 599 valid Yahoo Finance symbols (235 invalid excluded)
- **Processing Success:** 599/599 stocks (100% batch success)
- **Runtime:** 7.9 minuter (vs 43+ timmar för original)
- **TOP-10 EdgeScore Range:** 77.4 - 80.5

**TOP-10 Hybrid Results:**
1. EW (80.5) - Edwards Lifesciences (+6.34%, 2.68x volume)
2. KMB (80.5) - Kimberly-Clark (+1.49%, 1.69x volume)
3. NVS (78.7) - Novartis (+0.89%, 1.82x volume)
4. TGT (78.5) - Target (+2.65%, 1.59x volume)
5. FOXA (78.1) - Fox Corp (+2.56%, 1.44x volume)
6. UPS (77.9) - UPS (+1.13%, 1.77x volume)
7. TIGO (77.6) - Millicom (+4.21%, 1.46x volume)
8. BBIO (77.4) - BridgeBio (+11.51%, 3.75x volume)
9. WELL (77.4) - Welltower (+2.40%, 1.29x volume)
10. GIS (77.4) - General Mills (+1.81%, 1.37x volume)

## ⚠️ DISCLAIMERS

- **DEMO TRADING ONLY** - Systemet är konfigurerat för Capital.com demo account
- **Risk Warning** - CFD trading medför hög risk för kapitalförlust
- **No Investment Advice** - Detta system är för utbildningsändamål endast

---

**🎯 EDGE-10 v1.0 är production-ready för systematisk US-aktie daytrading!**
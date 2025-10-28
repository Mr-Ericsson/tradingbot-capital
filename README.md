# 🎯 EDGE-10 Trading SystemTradingbot Capital



Python-baserad automatisk tradingbot för Capital.com med EDGE-10 v1.0 long system.Python-baserad tradingbot med Capital.com DEMO.



## 🚀 EDGE-10 v1.0 SYSTEMKORRDA SCRIPT I ORDNING:

1) python capital_scan_tradeable.py

### Huvudkommandon:2) python rank_top10_momentum.py

```bash3) python analyze_edge_score.py

# 1. Kör EDGE-10 analys (fullständig pipeline)4) python place_pending_orders.py  (valfritt - l�gger ordrar)

python universe_run.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_output5) python bot_loop.py              (hela kedjan i loop)



# 2. Generera handelsordrar från TOP-10FILTER:

python edge10_generate_orders.py --top10 edge10_output/top_10.csv --output final_orders.csv- Spread max 0.2%

- Bara TRADEABLE markets

# 3. Lägg ordrar via Capital.com API- Close Only / Suspended filtreras bort

python place_pending_orders.py --orders final_orders.csv- Crypto bortfiltrerat



# 4. Automatisk position-stängningSTRUKTUR:

python auto_close_positions.py --account-mode demo --dry-runcapital_scan_tradeable.py       -> Scan av marknader

rank_top10_momentum.py          -> Momentum ranking

# 5. Visa positioner och statusanalyze_edge_score.py           -> Edge-analys

python status_positions_orders.pyplace_pending_orders.py         -> Orderhantering

```bot_loop.py                     -> Loopar strategin



## 📁 PROJEKT STRUKTURN�sta utvecklingssteg:

- Stoploss validering

### EDGE-10 Core System:- Riskkontroll

- `universe_run.py` - Huvudanalys pipeline (971→583→TOP-10)- Fail-safes

- `edge10_generate_orders.py` - Order generation med symbol mapping

- `EDGE10_SYSTEM_DOCUMENTATION.md` - Komplett system specifikation

### Edge-10 Modules:
- `edge10/` - EDGE-10 scoring och ranking moduler
- `edge10_corrected_test/` - Senaste verifierade test output

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

4. **Run EDGE-10:**
   ```bash
   python universe_run.py --csv data/scan/all_instruments_capital.csv --date 2025-10-28 --outdir edge10_output
   ```

## 📊 SYSTEM PERFORMANCE

**Senaste Verified Run (2025-01-22):**
- **Input:** 971 total instruments
- **Efter DUBBEL ETF-filter:** 739 stocks (135 ETF:er excluded)
- **Efter alla filter:** 693 tradeable stocks
- **Processade:** 583 stocks med full data
- **TOP-10 Output:** EdgeScore 61.2-67.0 range

**TOP-10 Results:**
1. TDY (67.0) - Teledyne Technologies
2. ABT (66.6) - Abbott  
3. GLW (65.7) - Corning
4. ICE (62.7) - ICE
5. MSFT (62.6) - Microsoft
6. JNJ (61.7) - Johnson & Johnson
7. APH (61.5) - Amphenol Corp
8. MGRC (61.5) - McGrath RentCorp
9. AME (61.4) - AMETEK
10. COR (61.2) - Cencora Inc

## ⚠️ DISCLAIMERS

- **DEMO TRADING ONLY** - Systemet är konfigurerat för Capital.com demo account
- **Risk Warning** - CFD trading medför hög risk för kapitalförlust
- **No Investment Advice** - Detta system är för utbildningsändamål endast

---

**🎯 EDGE-10 v1.0 är production-ready för systematisk US-aktie daytrading!**
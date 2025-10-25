Tradingbot Capital

Python-baserad tradingbot med Capital.com DEMO.

KORRDA SCRIPT I ORDNING:
1) python capital_scan_tradeable.py
2) python rank_top10_momentum.py
3) python analyze_edge_score.py
4) python place_pending_orders.py  (valfritt - lägger ordrar)
5) python bot_loop.py              (hela kedjan i loop)

FILTER:
- Spread max 0.2%
- Bara TRADEABLE markets
- Close Only / Suspended filtreras bort
- Crypto bortfiltrerat

STRUKTUR:
capital_scan_tradeable.py       -> Scan av marknader
rank_top10_momentum.py          -> Momentum ranking
analyze_edge_score.py           -> Edge-analys
place_pending_orders.py         -> Orderhantering
bot_loop.py                     -> Loopar strategin

Nästa utvecklingssteg:
- Stoploss validering
- Riskkontroll
- Fail-safes


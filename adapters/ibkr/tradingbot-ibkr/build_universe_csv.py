#!/usr/bin/env python3
# (see previous message for full docstring)
import re, sys
from typing import List, Set
import pandas as pd

WIKI_SOURCES = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://en.wikipedia.org/wiki/Russell_1000_Index",
    "https://en.wikipedia.org/wiki/Russell_2000_Index",
    "https://en.wikipedia.org/wiki/NASDAQ-100",
    "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
]

CORE_ETFS = [
    "SPY","VOO","IVV","VTI","QQQ","IWM","DIA","EFA","EEM","HYG","AGG","LQD",
    "TLT","SHY","IEF","XLF","XLK","XLE","XLY","XLP","XLV","XLI","XLB","XLU","VNQ",
    "ARKK","SOXX","SMH","IYR","KRE","XOP","GDX","GLD","SLV","USO","UNG",
    "SDS","SQQQ","TQQQ","UPRO","SPXL","SPXS","UVXY","SVXY"
]

def clean_symbol(sym: str) -> str:
    if not isinstance(sym, str):
        return ""
    s = sym.strip().upper()
    s = s.replace(".", "-")
    s = re.sub(r"[^A-Z0-9\-]", "", s)
    return s

def extract_symbols_from_wikipedia(url: str):
    syms = []
    try:
        tables = pd.read_html(url)
    except Exception:
        return syms
    for tbl in tables:
        candidates = [c for c in tbl.columns if str(c).strip().lower() in {"symbol","ticker","ticker symbol","company symbol"}]
        if not candidates and "Symbol" in tbl.columns:
            candidates = ["Symbol"]
        if candidates:
            col = candidates[0]
            col_syms = [clean_symbol(x) for x in tbl[col].dropna().tolist()]
            syms.extend([x for x in col_syms if x])
        else:
            first_col = tbl.columns[0]
            col_syms = [clean_symbol(x) for x in tbl[first_col].dropna().tolist()]
            col_syms = [x for x in col_syms if re.search(r"[A-Z]", x)]
            col_syms = [x for x in col_syms if len(x) <= 6]
            if col_syms:
                syms.extend(col_syms)
    return syms

def make_universe():
    universe = set()
    for u in WIKI_SOURCES:
        syms = extract_symbols_from_wikipedia(u)
        universe.update([s for s in syms if s])
    universe.update(CORE_ETFS)
    items = sorted([s for s in universe if s and re.search(r"[A-Z]", s)])
    items = [s for s in items if 1 <= len(s) <= 6]
    return items

def main():
    syms = make_universe()
    if not syms:
        print("No symbols extracted. Run this script on a machine with internet access.")
        sys.exit(1)
    import pandas as pd
    pd.DataFrame({"SYMBOL": syms}).to_csv("universe_us.csv", index=False, encoding="utf-8")
    print(f"Wrote universe_us.csv with {len(syms)} symbols.")

if __name__ == "__main__":
    main()
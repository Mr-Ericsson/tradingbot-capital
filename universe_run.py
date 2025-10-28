#!/usr/bin/env python3
"""
Universe Run Script - Capital.com CSV → TOP-100 + TOP-10
Kör vår bevisade pipeline på hela universumet från Capital-listan.
"""

import argparse
import pandas as pd
import numpy as np
import yfinance as yf
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import sys
import warnings

warnings.filterwarnings("ignore")


def setup_logging():
    """Setup basic logging to stderr för debug output"""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    return logging.getLogger(__name__)


def create_output_dir(outdir: str):
    """Skapa output-directory om det inte finns"""
    os.makedirs(outdir, exist_ok=True)
    return outdir


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Universe Run - Capital.com CSV → TOP-100 + TOP-10"
    )
    parser.add_argument(
        "--csv",
        default="data/scan/all_instruments_capital.csv",
        help="Path till Capital.com instruments CSV",
    )
    parser.add_argument("--date", default="2025-10-24", help="Analysdatum (YYYY-MM-DD)")
    parser.add_argument(
        "--outdir", default="out", help="Output directory för CSV-filer"
    )
    return parser.parse_args()


def normalize_spread_pct(spread_pct):
    """
    Normalisera spread till decimal format
    - om spread_pct > 1 → tolka som % och dela på 100 (0.25 → 0.0025)
    - else om 0 < spread_pct ≤ 0.01 → tolka som redan decimal (0.0025 = 0.25%)
    - I vårt Capital CSV verkar värdena vara i %-format (0.18 = 18%), så konvertera till decimal
    """
    if pd.isna(spread_pct):
        return np.nan

    # Capital.com verkar använda decimal-format för procent (0.18 = 18%)
    # Så vi behöver dela på 100 för att få verklig decimal
    return spread_pct / 100.0


def map_capital_symbol_to_yahoo(epic: str) -> str:
    """
    Konvertera Capital.com epic till Yahoo Finance symbol
    - Ta bort US. prefix om finns → US.TSLA → TSLA
    - Om epic redan är ren (t.ex. MCK) → behåll
    """
    if pd.isna(epic) or not isinstance(epic, str):
        return epic

    # Strippa US. prefix
    if epic.startswith("US."):
        return epic[3:]

    return epic


def get_yahoo_data(
    ticker: str, end_date: str, days_back: int = 300
) -> Optional[pd.DataFrame]:
    """Hämta historisk data från Yahoo Finance"""
    try:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=days_back)

        yfin_ticker = yf.Ticker(ticker)
        df = yfin_ticker.history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d",
            prepost=False,
        )

        if df.empty:
            return None

        # Standardisera kolumnnamn - kolla om Adj Close finns
        if "Adj Close" in df.columns:
            df = df.rename(columns={"Adj Close": "AdjClose"})
        elif "AdjClose" not in df.columns:
            # Om ingen AdjClose finns, använd Close som fallback
            df["AdjClose"] = df["Close"]

        return df

    except Exception as e:
        return None


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Beräkna alla tekniska features"""
    df = df.copy()

    # MA20 och MA50
    df["MA20"] = df["AdjClose"].rolling(window=20, min_periods=20).mean()
    df["MA50"] = df["AdjClose"].rolling(window=50, min_periods=50).mean()

    # AvgVol10 (genomsnitt av föregående 10 dagar)
    df["AvgVol10"] = df["Volume"].shift(1).rolling(window=10, min_periods=10).mean()

    # RelVol10
    df["RelVol10"] = df["Volume"] / df["AvgVol10"]

    # ATR14 (Wilder's method)
    df["PrevClose"] = df["Close"].shift(1)
    df["TR"] = np.maximum.reduce(
        [
            df["High"] - df["Low"],
            np.abs(df["High"] - df["PrevClose"]),
            np.abs(df["Low"] - df["PrevClose"]),
        ]
    )

    # ATR med Wilder's EMA (alpha = 1/14)
    alpha = 1.0 / 14.0
    atr_values = []
    for i, tr in enumerate(df["TR"]):
        if pd.isna(tr):
            atr_values.append(np.nan)
        elif i == 0:
            atr_values.append(tr)
        else:
            prev_atr = atr_values[i - 1]
            if pd.isna(prev_atr):
                atr_values.append(tr)
            else:
                atr_values.append(alpha * tr + (1 - alpha) * prev_atr)

    df["ATR14"] = atr_values

    # ATRfrac
    df["ATRfrac"] = df["ATR14"] / df["Close"]

    # DayReturnPct
    df["DayReturnPct"] = ((df["Close"] / df["Open"]) - 1) * 100

    # Trend20 och Trend50
    df["Trend20"] = (df["Close"] - df["MA20"]) / df["MA20"]
    df["Trend50"] = (df["Close"] - df["MA50"]) / df["MA50"]

    return df


def label_A_B(df: pd.DataFrame, spread: float) -> pd.DataFrame:
    """Beräkna A_WIN/LOSS/AMBIG_LABEL och B_LABEL med spread-justering"""
    df = df.copy()

    if pd.isna(spread):
        df["A_WIN_LABEL"] = np.nan
        df["A_LOSS_LABEL"] = np.nan
        df["A_AMBIG_LABEL"] = np.nan
        df["B_LABEL"] = np.nan
        return df

    # Nya A-labels med detaljerad TP/SL-analys
    a_win_labels = []
    a_loss_labels = []
    a_ambig_labels = []

    for _, row in df.iterrows():
        entry = row["Open"]
        tp_brutto = entry * (1 + 0.03 + spread)
        sl_brutto = entry * (1 - 0.02 - spread)

        tp_hit = 1 if row["High"] >= tp_brutto else 0
        sl_hit = 1 if row["Low"] <= sl_brutto else 0
        both = 1 if (tp_hit == 1 and sl_hit == 1) else 0

        a_win = 1 if (tp_hit == 1 and sl_hit == 0) else 0
        a_loss = 1 if (sl_hit == 1 and tp_hit == 0) else 0
        a_ambig = both

        a_win_labels.append(a_win)
        a_loss_labels.append(a_loss)
        a_ambig_labels.append(a_ambig)

    df["A_WIN_LABEL"] = a_win_labels
    df["A_LOSS_LABEL"] = a_loss_labels
    df["A_AMBIG_LABEL"] = a_ambig_labels

    # B_LABEL: Close >= Open * (1 + spread)
    df["B_LABEL"] = (df["Close"] >= df["Open"] * (1 + spread)).astype(int)

    return df


def make_bins(
    train_df: pd.DataFrame, features: List[str], n_bins: int = 3
) -> Dict[str, np.ndarray]:
    """Skapa tertiler (bins) för varje feature"""
    bin_edges = {}

    for feature in features:
        valid_data = train_df[feature].dropna()
        if len(valid_data) >= n_bins:
            try:
                _, edges = pd.qcut(
                    valid_data, q=n_bins, retbins=True, duplicates="drop"
                )
                bin_edges[feature] = edges
            except Exception:
                # Fallback för konstanta värden
                min_val, max_val = valid_data.min(), valid_data.max()
                bin_edges[feature] = np.linspace(min_val, max_val, n_bins + 1)
        else:
            bin_edges[feature] = None

    return bin_edges


def bin_index(value: float, bin_edges: np.ndarray) -> int:
    """Returnera bin-index för ett värde"""
    if pd.isna(value) or bin_edges is None:
        return -1

    # Använd searchsorted för att hitta rätt bin
    if value <= bin_edges[0]:
        return 0
    elif value >= bin_edges[-1]:
        return len(bin_edges) - 2
    else:
        return np.searchsorted(bin_edges[1:-1], value)


def match_similar(
    train_df: pd.DataFrame,
    today_row: pd.Series,
    bin_edges: Dict[str, np.ndarray],
    features: List[str],
    n_min: int = 30,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Hitta liknande dagar med progressiv relaxering"""

    # Beräkna bin-index för idag
    today_bins = {}
    for feature in features:
        if feature in today_row and not pd.isna(today_row[feature]):
            today_bins[feature] = bin_index(today_row[feature], bin_edges.get(feature))

    # Progressiv relaxering: 5 → 3 → 2 → 1 feature(s)
    for n_features in [len(features), 3, 2, 1]:
        if n_features > len(today_bins):
            continue

        # Matcha exakt n_features av de tillgängliga
        matching_rows = []

        for idx, row in train_df.iterrows():
            matches = 0
            for feature in features:
                if (
                    feature in today_bins
                    and feature in row
                    and not pd.isna(row[feature])
                ):
                    row_bin = bin_index(row[feature], bin_edges.get(feature))
                    if row_bin == today_bins[feature]:
                        matches += 1

            if matches >= n_features:
                matching_rows.append(idx)

        sample = train_df.loc[matching_rows]

        # Filtrera endast rader med giltiga A och B labels
        sample_a = sample.dropna(
            subset=["A_WIN_LABEL", "A_LOSS_LABEL", "A_AMBIG_LABEL"]
        )
        sample_b = sample.dropna(subset=["B_LABEL"])

        if len(sample_a) >= n_min and len(sample_b) >= n_min:
            return sample_a, sample_b

    # Ingen lyckad matchning
    return pd.DataFrame(), pd.DataFrame()


def get_market_date(end_date: str) -> str:
    """Hitta senaste handelsdagen <= end_date"""
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Enkel approximation: gå bakåt max 7 dagar för att hitta en handelsdag
    for i in range(8):
        check_date = end_dt - timedelta(days=i)
        # Undvik helger (lördag=5, söndag=6)
        if check_date.weekday() < 5:  # Måndag=0 till Fredag=4
            return check_date.strftime("%Y-%m-%d")

    # Fallback
    return end_date


def get_sector_info(ticker: str) -> str:
    """Hämta sektor från Yahoo Finance"""
    try:
        yfin_ticker = yf.Ticker(ticker)
        info = yfin_ticker.get_info()
        return info.get("sector", "Unknown")
    except:
        return "Unknown"


def sec_earnings_flag(ticker: str, market_date: str) -> int:
    """
    Placeholder för SEC earnings flagga
    Returnerar 0 för alla tickers tills vi implementerar SEC integration
    """
    return 0


def format_number(value, decimals: int):
    """Formatera nummer med specifikt antal decimaler"""
    if pd.isna(value):
        return "N/A"
    return f"{value:.{decimals}f}"


def process_ticker(
    ticker_info: dict, market_date: str, analysis_date: str, logger
) -> Optional[dict]:
    """Processa en ticker och returnera alla features och winrates"""
    ticker = ticker_info["symbol_yahoo"]
    spread = ticker_info["spread_pct_norm"]

    logger.info(f"Bearbetar {ticker}...")

    # Hämta Yahoo data
    df = get_yahoo_data(ticker, analysis_date, days_back=400)
    if df is None or len(df) < 260:
        logger.warning(f"Otillräcklig data för {ticker}")
        return None

    # Beräkna features
    df = compute_features(df)

    # Lägg till A/B labels
    df = label_A_B(df, spread)

    # Hitta market_date i data
    try:
        market_dt = datetime.strptime(market_date, "%Y-%m-%d")
        df.index = pd.to_datetime(df.index)

        if market_dt.date() not in df.index.date:
            logger.warning(f"Market date {market_date} inte i data för {ticker}")
            return None

        today_row = df[df.index.date == market_dt.date()].iloc[0]
        train_df = df[df.index.date < market_dt.date()]

    except Exception as e:
        logger.warning(f"Fel vid datumhantering för {ticker}: {e}")
        return None

    # Skapa features för matchning
    features = ["RelVol10", "Trend20", "ATRfrac", "Trend50", "DayReturnPct"]

    # Skapa bins från träningsdata
    bin_edges = make_bins(train_df, features)

    # Hitta liknande dagar
    sample_a, sample_b = match_similar(train_df, today_row, bin_edges, features)

    # Beräkna winrates
    if len(sample_a) >= 30:
        a_winrate = sample_a["A_WIN_LABEL"].mean()
        a_loserate = sample_a["A_LOSS_LABEL"].mean()
        a_ambigrate = sample_a["A_AMBIG_LABEL"].mean()
        sample_size_a = len(sample_a)
    else:
        a_winrate = a_loserate = a_ambigrate = np.nan
        sample_size_a = 0

    if len(sample_b) >= 30:
        b_winrate = sample_b["B_LABEL"].mean()
        sample_size_b = len(sample_b)
    else:
        b_winrate = np.nan
        sample_size_b = 0

    # Hämta sektor
    sector = ticker_info.get("sector", "") or get_sector_info(ticker)

    # Earnings flag
    earnings_flag = sec_earnings_flag(ticker, market_date)

    # Samla alla data
    result = {
        "Ticker": ticker,
        "Name": ticker_info.get("name", ""),
        "Sector": sector,
        "Date": analysis_date,
        "market_date": market_date,
        "Open": today_row["Open"],
        "High": today_row["High"],
        "Low": today_row["Low"],
        "Close": today_row["Close"],
        "AdjClose": today_row["AdjClose"],
        "Volume": (
            int(today_row["Volume"]) if not pd.isna(today_row["Volume"]) else "N/A"
        ),
        "AvgVol10": (
            int(today_row["AvgVol10"]) if not pd.isna(today_row["AvgVol10"]) else "N/A"
        ),
        "RelVol10": today_row["RelVol10"],
        "MA20": today_row["MA20"],
        "MA50": today_row["MA50"],
        "ATR14": today_row["ATR14"],
        "ATRfrac": today_row["ATRfrac"],
        "Trend20": today_row["Trend20"],
        "Trend50": today_row["Trend50"],
        "DayReturnPct": today_row["DayReturnPct"],
        "SpreadPct": spread,
        "A_WINRATE": a_winrate,
        "A_LOSERATE": a_loserate,
        "A_AMBIGRATE": a_ambigrate,
        "B_WINRATE": b_winrate,
        "SampleSizeA": sample_size_a,
        "SampleSizeB": sample_size_b,
        "EarningsFlag": earnings_flag,
        # EDGE-10 dataschema tillägg
        "NewsFlag": 0,  # Placeholder - behöver news API integration  
        "SecFlag": 0,   # Placeholder - behöver SEC filings API
        "SentimentScore": 0.0,  # Placeholder - behöver sentiment API
        "SectorETF": "Unknown",  # Placeholder - behöver sektor-mapping
        "SectorStrength": 0.0,   # Placeholder - behöver sektor-performance
        "IndexBias": 0.0,        # Placeholder - behöver index-korrelation
    }

    return result


def calculate_edge_scores(results: List[dict]) -> List[dict]:
    """Beräkna EDGE-10 EdgeScore enligt specifikation:
    EdgeScore = 30% DayStrength + 30% RelVol10 + 20% Catalyst + 10% Market + 10% VolFit
    Alla komponenter rank-baserade (1-100 skala)"""
    
    if not results:
        return results
        
    df = pd.DataFrame(results)
    n_stocks = len(df)
    
    # EDGE-10 komponenter enligt specifikation
    
    # 1. DayStrength: Dagens styrka (DayReturnPct)
    df['DayStrength_rank'] = df['DayReturnPct'].rank(method='min', ascending=True, na_option='bottom')
    df['DayStrength'] = ((df['DayStrength_rank'] - 1) / max(1, n_stocks - 1)) * 100
    
    # 2. RelVol10: Relativt volym (redan beräknad)
    df['RelVol10_rank'] = df['RelVol10'].rank(method='min', ascending=True, na_option='bottom')
    df['RelVol10_score'] = ((df['RelVol10_rank'] - 1) / max(1, n_stocks - 1)) * 100
    
    # 3. Catalyst: Kombinera news/earnings/sector events
    # För nu: använd EarningsFlag + NewsFlag kombinerat
    catalyst_raw = df['EarningsFlag'].fillna(0) * 2 + df['NewsFlag'].fillna(0)
    df['Catalyst_rank'] = catalyst_raw.rank(method='min', ascending=True, na_option='bottom')
    df['Catalyst'] = ((df['Catalyst_rank'] - 1) / max(1, n_stocks - 1)) * 100
    
    # 4. Market: Marknadssentiment (SectorStrength + IndexBias)
    market_raw = df['SectorStrength'].fillna(0) + df['IndexBias'].fillna(0)
    df['Market_rank'] = market_raw.rank(method='min', ascending=True, na_option='bottom')
    df['Market'] = ((df['Market_rank'] - 1) / max(1, n_stocks - 1)) * 100
    
    # 5. VolFit: Volatilitetspassning (lägre ATRfrac = bättre, så invertera)
    df['ATRfrac_rank'] = df['ATRfrac'].rank(method='min', ascending=False, na_option='bottom')  # Invertera
    df['VolFit'] = ((df['ATRfrac_rank'] - 1) / max(1, n_stocks - 1)) * 100
    
    # Beräkna EdgeScore enligt EDGE-10 viktning
    df['EdgeScore'] = (
        0.30 * df['DayStrength'] +      # 30% DayStrength  
        0.30 * df['RelVol10_score'] +   # 30% RelVol10
        0.20 * df['Catalyst'] +         # 20% Catalyst
        0.10 * df['Market'] +           # 10% Market
        0.10 * df['VolFit']             # 10% VolFit
    )
    
    # Avrunda EdgeScore
    df['EdgeScore'] = df['EdgeScore'].round(1)
    
    # Kopiera tillbaka till results
    for i, result in enumerate(results):
        result["Score"] = df.loc[i, 'EdgeScore']
        result["DayStrength"] = df.loc[i, 'DayStrength'].round(1)
        result["Catalyst"] = df.loc[i, 'Catalyst'].round(1) 
        result["Market"] = df.loc[i, 'Market'].round(1)
        result["VolFit"] = df.loc[i, 'VolFit'].round(1)

    return results


def select_top_candidates(results: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Välj TOP-100 och TOP-10 kandidater enligt EDGE-10 spec"""
    df = pd.DataFrame(results)

    # TOP-100: sortera på EdgeScore (högst först)
    df_sorted = df.sort_values(
        ["Score"],  # EdgeScore är primär sortering för EDGE-10
        ascending=[False],
        na_position="last",
    )

    top_100 = df_sorted.head(100).to_dict("records")

    # TOP-10 urval: Välj TOP-10 via EdgeScore RANK
    top_10 = []
    
    # EDGE-10 spec: Primary selection via EdgeScore ranking
    candidates_sorted = df.sort_values(
        ["Score", "A_WINRATE", "B_WINRATE"],  # EdgeScore först, sedan A/B som tiebreaker
        ascending=[False, False, False],
        na_position="last",
    )

    # Sample size validation: flagga <30 samples
    for _, row in candidates_sorted.iterrows():
        sample_a = row.get("SampleSizeA", 0)
        sample_b = row.get("SampleSizeB", 0) 
        
        sample_warning = ""
        if sample_a < 30:
            sample_warning += f"SampleA={sample_a}<30; "
        if sample_b < 30:
            sample_warning += f"SampleB={sample_b}<30; "
            
        row_dict = row.to_dict()
        row_dict["PickReason"] = f"EdgeScore={row['Score']:.1f}" + (f" [{sample_warning.strip()}]" if sample_warning else "")
        
        top_10.append(row_dict)
        
        if len(top_10) >= 10:
            break

    return top_100, top_10


def format_output_row(row_dict: dict) -> dict:
    """Formatera en rad för CSV output enligt specifikationen"""
    formatted = {}

    # Kopiering och formatering
    formatted["Ticker"] = row_dict.get("Ticker", "N/A")
    formatted["Name"] = row_dict.get("Name", "N/A")
    formatted["Sector"] = row_dict.get("Sector", "Unknown")
    formatted["Date"] = row_dict.get("Date", "N/A")
    formatted["market_date"] = row_dict.get("market_date", "N/A")

    # Priser/MA/ATR: 2 d.p.
    formatted["Open"] = format_number(row_dict.get("Open"), 2)
    formatted["High"] = format_number(row_dict.get("High"), 2)
    formatted["Low"] = format_number(row_dict.get("Low"), 2)
    formatted["Close"] = format_number(row_dict.get("Close"), 2)
    formatted["AdjClose"] = format_number(row_dict.get("AdjClose"), 2)
    formatted["MA20"] = format_number(row_dict.get("MA20"), 2)
    formatted["MA50"] = format_number(row_dict.get("MA50"), 2)
    formatted["ATR14"] = format_number(row_dict.get("ATR14"), 2)

    # Volymer: heltal eller N/A
    formatted["Volume"] = row_dict.get("Volume", "N/A")
    formatted["AvgVol10"] = row_dict.get("AvgVol10", "N/A")

    # RelVol10, ATRfrac, Trend*: 4 d.p.
    formatted["RelVol10"] = format_number(row_dict.get("RelVol10"), 4)
    formatted["ATRfrac"] = format_number(row_dict.get("ATRfrac"), 4)
    formatted["Trend20"] = format_number(row_dict.get("Trend20"), 4)
    formatted["Trend50"] = format_number(row_dict.get("Trend50"), 4)
    formatted["DayReturnPct"] = format_number(row_dict.get("DayReturnPct"), 4)

    # SpreadPct (decimal): 4 d.p.
    formatted["SpreadPct"] = format_number(row_dict.get("SpreadPct"), 4)

    # A/B rates: 2 d.p.
    formatted["A_WINRATE"] = format_number(row_dict.get("A_WINRATE"), 2)
    formatted["A_LOSERATE"] = format_number(row_dict.get("A_LOSERATE"), 2)
    formatted["A_AMBIGRATE"] = format_number(row_dict.get("A_AMBIGRATE"), 2)
    formatted["B_WINRATE"] = format_number(row_dict.get("B_WINRATE"), 2)

    # Sample sizes
    formatted["SampleSizeA"] = row_dict.get("SampleSizeA", "N/A")
    formatted["SampleSizeB"] = row_dict.get("SampleSizeB", "N/A")

    # EDGE-10 flags och scores
    formatted["EarningsFlag"] = row_dict.get("EarningsFlag", "N/A")
    formatted["NewsFlag"] = row_dict.get("NewsFlag", "N/A") 
    formatted["SecFlag"] = row_dict.get("SecFlag", "N/A")
    formatted["SentimentScore"] = format_number(row_dict.get("SentimentScore"), 2)
    formatted["SectorETF"] = row_dict.get("SectorETF", "N/A")
    formatted["SectorStrength"] = format_number(row_dict.get("SectorStrength"), 2)
    formatted["IndexBias"] = format_number(row_dict.get("IndexBias"), 2)
    formatted["DayStrength"] = format_number(row_dict.get("DayStrength"), 1)
    formatted["Catalyst"] = format_number(row_dict.get("Catalyst"), 1)
    formatted["Market"] = format_number(row_dict.get("Market"), 1)
    formatted["VolFit"] = format_number(row_dict.get("VolFit"), 1)

    # EdgeScore: 1 d.p.
    formatted["Score"] = format_number(row_dict.get("Score"), 1)

    # PickReason för TOP-10
    if "PickReason" in row_dict:
        formatted["PickReason"] = row_dict["PickReason"]

    return formatted


def save_results(
    results: List[dict], top_100: List[dict], top_10: List[dict], outdir: str
):
    """Spara alla tre CSV-filer"""

    # Kolumnordning enligt spec
    base_columns = [
        "Ticker",
        "Name",
        "Sector",
        "Date",
        "market_date",
        "Open",
        "High",
        "Low",
        "Close",
        "AdjClose",
        "Volume",
        "AvgVol10",
        "RelVol10",
        "MA20",
        "MA50",
        "ATR14",
        "ATRfrac",
        "Trend20",
        "Trend50",
        "DayReturnPct",
        "SpreadPct",
        "A_WINRATE",
        "A_LOSERATE",
        "A_AMBIGRATE",
        "B_WINRATE",
        "SampleSizeA",
        "SampleSizeB",
        "EarningsFlag",
        "NewsFlag",
        "SecFlag", 
        "SentimentScore",
        "SectorETF",
        "SectorStrength",
        "IndexBias",
        "DayStrength",
        "Catalyst",
        "Market", 
        "VolFit",
        "Score",
    ]

    # Full universe
    full_formatted = [format_output_row(row) for row in results]
    df_full = pd.DataFrame(full_formatted, columns=base_columns)
    df_full.to_csv(os.path.join(outdir, "full_universe_features.csv"), index=False)

    # TOP-100
    top100_formatted = [format_output_row(row) for row in top_100]
    df_top100 = pd.DataFrame(top100_formatted, columns=base_columns)
    df_top100.to_csv(os.path.join(outdir, "top_100.csv"), index=False)

    # TOP-10 (with PickReason + SampleA/SampleB columns)
    top10_columns = base_columns + ["SampleA", "SampleB", "PickReason"]
    top10_formatted = []
    
    for row in top_10:
        formatted_row = format_output_row(row)
        # Add SampleA and SampleB columns
        formatted_row["SampleA"] = row.get("SampleSizeA", 0)
        formatted_row["SampleB"] = row.get("SampleSizeB", 0)
        top10_formatted.append(formatted_row)
        
    df_top10 = pd.DataFrame(top10_formatted, columns=top10_columns)
    df_top10.to_csv(os.path.join(outdir, "top_10.csv"), index=False)


def load_and_filter_capital_csv(csv_path: str, logger) -> pd.DataFrame:
    """
    Läs Capital CSV och applicera grundfilter:
    - US-aktier: is_us_stock == True
    - Handlingsbar: bid > 0 && ask > 0
    - Spread ≤ 0.3% (0.003 i decimal)
    - Prisgolv: mid = (bid+ask)/2 ≥ 2 USD
    """
    logger.info(f"Läser Capital CSV: {csv_path}")
    df = pd.read_csv(csv_path)

    logger.info(f"Total instruments i CSV: {len(df)}")
    
    # Initialize excluded tracking
    excluded_list = []

    # Filter 1: US-aktier
    df_us = df[df["is_us_stock"] == True].copy()
    non_us = df[df["is_us_stock"] != True]
    for _, row in non_us.iterrows():
        excluded_list.append({
            'epic': row.get('epic', ''),
            'name': row.get('name', ''),
            'reason': 'Not US stock',
            'filter_stage': 'US-filter'
        })
    logger.info(f"Efter US-aktie filter: {len(df_us)}")

    # Filter 2: DUBBEL ETF-filtering (enligt EDGE-10 spec failsafe)
    # LEVEL A: Keyword-baserad filtering
    def is_etf_or_leveraged_keywords(row):
        """LEVEL A: Kontrollera ETF/leveraged via keywords och blocked tickers"""
        epic = str(row.get("epic", "")).upper()
        name = str(row.get("name", "")).upper()
        
        # ETF patterns
        etf_patterns = ["ETF", "FUND", "TRUST", "INDEX", "SPDR", "ISHARES", "VANGUARD", "INVESCO"]
        # Leveraged patterns  
        leveraged_patterns = ["ULTRA", "2X", "3X", "DIREXION", "PROSHARES"]
        # Specific blocked tickers
        blocked_tickers = ["QQQ", "SPY", "IVV", "VTI", "TQQQ", "SQQQ", "QLD", "QID", "XLF", "XLE", "XLI", "XLK"]
        
        # Check name patterns
        for pattern in etf_patterns + leveraged_patterns:
            if pattern in name:
                return True, f"ETF/Leveraged pattern: {pattern}"
                
        # Check blocked tickers
        if epic in blocked_tickers:
            return True, f"Blocked ticker: {epic}"
            
        return False, None
    
    # LEVEL B: Yahoo Finance quoteType validation
    def is_yahoo_etf(row):
        """LEVEL B: Yahoo Finance quoteType=='ETF' validation"""
        try:
            epic = str(row.get("epic", ""))
            yahoo_symbol = epic  # For now, assume direct mapping
            
            # Fetch Yahoo data
            import yfinance as yf
            ticker = yf.Ticker(yahoo_symbol)
            info = ticker.info
            
            # Check quoteType
            quote_type = info.get("quoteType", "").upper()
            if quote_type == "ETF":
                return True, f"Yahoo quoteType: {quote_type}"
                
        except Exception as e:
            logger.warning(f"Yahoo validation failed for {epic}: {e}")
            
        return False, None
    
    # Apply LEVEL A filtering first
    level_a_excluded = []
    level_a_keep = []
    
    for _, row in df_us.iterrows():
        is_excluded, reason = is_etf_or_leveraged_keywords(row)
        if is_excluded:
            excluded_list.append({
                'epic': row.get('epic', ''),
                'name': row.get('name', ''),
                'reason': reason,
                'filter_stage': 'ETF-LEVEL-A'
            })
            level_a_excluded.append(row)
        else:
            level_a_keep.append(row)
    
    df_level_a = pd.DataFrame(level_a_keep)
    logger.info(f"Efter LEVEL A ETF filter: {len(df_level_a)} (exkluderade {len(level_a_excluded)} via keywords)")
    
    # Apply LEVEL B filtering (sample validation on subset for performance)
    if len(df_level_a) > 100:
        # Sample 50 random instruments for Yahoo validation
        sample_df = df_level_a.sample(n=50, random_state=42)
        yahoo_etfs = []
        
        for _, row in sample_df.iterrows():
            is_etf, reason = is_yahoo_etf(row)
            if is_etf:
                excluded_list.append({
                    'epic': row.get('epic', ''),
                    'name': row.get('name', ''),
                    'reason': reason,
                    'filter_stage': 'ETF-LEVEL-B'
                })
                yahoo_etfs.append(row['epic'])
        
        if len(yahoo_etfs) > 0:
            logger.warning(f"🚨 LEVEL B detected {len(yahoo_etfs)} Yahoo ETFs in sample: {yahoo_etfs}")
            # Remove any detected ETFs from full dataset
            df_stocks_only = df_level_a[~df_level_a['epic'].isin(yahoo_etfs)].copy()
        else:
            df_stocks_only = df_level_a.copy()
            logger.info("✅ LEVEL B validation: No Yahoo ETFs detected in sample")
    else:
        # Small dataset - validate all
        level_b_keep = []
        for _, row in df_level_a.iterrows():
            is_etf, reason = is_yahoo_etf(row)
            if is_etf:
                excluded_list.append({
                    'epic': row.get('epic', ''),
                    'name': row.get('name', ''),
                    'reason': reason,
                    'filter_stage': 'ETF-LEVEL-B'
                })
            else:
                level_b_keep.append(row)
        df_stocks_only = pd.DataFrame(level_b_keep)
    
    total_excluded = len(df_us) - len(df_stocks_only)
    logger.info(f"🛡️ DUBBEL ETF-filter komplett: {len(df_stocks_only)} stocks (exkluderade {total_excluded} ETF:er totalt)")

    # Filter 3: Handlingsbar (bid > 0 och ask > 0)
    not_tradeable = df_stocks_only[(df_stocks_only["bid"] <= 0) | (df_stocks_only["ask"] <= 0)]
    for _, row in not_tradeable.iterrows():
        excluded_list.append({
            'epic': row.get('epic', ''),
            'name': row.get('name', ''),
            'reason': f"Not tradeable - bid:{row.get('bid',0)} ask:{row.get('ask',0)}",
            'filter_stage': 'Tradeable-filter'
        })
    df_tradeable = df_stocks_only[(df_stocks_only["bid"] > 0) & (df_stocks_only["ask"] > 0)].copy()
    logger.info(f"Efter tradeable filter: {len(df_tradeable)}")

    # Filter 4: Spread ≤ 0.3% (0.003 i decimal)
    # Normalisera spread_pct först
    df_tradeable["spread_pct_norm"] = df_tradeable["spread_pct"].apply(
        normalize_spread_pct
    )
    high_spread = df_tradeable[df_tradeable["spread_pct_norm"] > 0.003]
    for _, row in high_spread.iterrows():
        excluded_list.append({
            'epic': row.get('epic', ''),
            'name': row.get('name', ''),
            'reason': f"High spread: {row.get('spread_pct_norm',0):.4f} > 0.003",
            'filter_stage': 'Spread-filter'
        })
    df_spread = df_tradeable[df_tradeable["spread_pct_norm"] <= 0.003].copy()
    logger.info(f"Efter spread ≤ 0.3% filter: {len(df_spread)}")

    # Filter 5: Prisgolv ≥ $2 USD
    df_spread["mid_price"] = (df_spread["bid"] + df_spread["ask"]) / 2
    low_price = df_spread[df_spread["mid_price"] < 2.0]
    for _, row in low_price.iterrows():
        excluded_list.append({
            'epic': row.get('epic', ''),
            'name': row.get('name', ''),
            'reason': f"Price too low: ${row.get('mid_price',0):.2f} < $2.00",
            'filter_stage': 'Price-filter'
        })
    df_final = df_spread[df_spread["mid_price"] >= 2.0].copy()
    logger.info(f"Efter prisgolv ≥ $2 filter: {len(df_final)} (ENDAST US-AKTIER, INGA ETF:er)")

    # Save excluded.csv 
    if excluded_list:
        excluded_df = pd.DataFrame(excluded_list)
        excluded_path = csv_path.replace('.csv', '_excluded.csv')
        excluded_df.to_csv(excluded_path, index=False)
        logger.info(f"💾 Saved {len(excluded_list)} excluded instruments to {excluded_path}")

    # Lägg till Yahoo symbol
    df_final["symbol_yahoo"] = df_final["epic"].apply(map_capital_symbol_to_yahoo)

    return df_final


def main():
    logger = setup_logging()
    args = parse_arguments()

    logger.info(f"Starting Universe Run...")
    logger.info(f"CSV: {args.csv}")
    logger.info(f"Date: {args.date}")
    logger.info(f"Output: {args.outdir}")

    # Skapa output directory
    create_output_dir(args.outdir)

    # Beräkna market_date
    market_date = get_market_date(args.date)
    logger.info(f"Market date: {market_date}")

    # Läs och filtrera Capital CSV
    capital_df = load_and_filter_capital_csv(args.csv, logger)

    if len(capital_df) == 0:
        logger.error("Inga instrument kvar efter filtrering!")
        return

    logger.info(f"Slutlig kandidatlista: {len(capital_df)} instruments")

    # Processa alla tickers
    results = []

    for idx, row in capital_df.iterrows():
        ticker_info = {
            "symbol_yahoo": row["symbol_yahoo"],
            "spread_pct_norm": row["spread_pct_norm"],
            "name": row.get("name", ""),
            "sector": row.get("sector", ""),
        }

        result = process_ticker(ticker_info, market_date, args.date, logger)
        if result:
            results.append(result)

        # Progress logging var 50:e ticker
        if (len(results) + 1) % 50 == 0:
            logger.info(f"Processed {len(results)} tickers...")

    logger.info(f"Successfully processed {len(results)} tickers")

    if len(results) == 0:
        logger.error("Inga lyckade ticker-processningar!")
        return

    # Beräkna scores
    results = calculate_edge_scores(results)

    # Välj TOP-100 och TOP-10
    top_100, top_10 = select_top_candidates(results)

    # Spara alla filer
    save_results(results, top_100, top_10, args.outdir)

    logger.info(
        f"Saved {len(results)} total, {len(top_100)} TOP-100, {len(top_10)} TOP-10"
    )
    logger.info("Universe Run completed!")


if __name__ == "__main__":
    main()

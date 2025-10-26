"""
Hämtar alla tillgängliga instrument från Capital.com API utan filtrering.
Sparar till CSV för analys och skanningsändamål.
"""

import os
import sys
import argparse
import requests
import pandas as pd
from typing import Dict, List, Any
from dotenv import load_dotenv

# Importera session-hantering från rank_top10.py
from rank_top10 import CapitalSession, load_env, login_capital, session_headers


def fetch_all_markets(session: CapitalSession) -> List[Dict[str, Any]]:
    """
    Hämtar alla tillgängliga marknader från Capital.com API
    GET /api/v1/markets - returnerar alla instrument utan filtrering
    """
    url = f"{session.base_url}/api/v1/markets"
    headers = session_headers(session)

    all_markets = []

    try:
        print("Hämtar alla instrument från Capital.com...")
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            raise RuntimeError(f"API-fel {response.status_code}: {response.text}")

        data = response.json()
        markets = data.get("markets", [])

        print(f"Hittade {len(markets)} instrument totalt")

        # DEBUG: Kolla första market-objektet
        if markets:
            first_market = markets[0]
            print(f"\nDEBUG - Första market struktur:")
            print(f"Keys: {list(first_market.keys())}")

        for i, market in enumerate(markets):
            # NY STRUKTUR - alla fält är på toppnivån
            epic = market.get("epic", "")
            name = market.get("instrumentName", "")
            instrument_type = market.get("instrumentType", "")
            market_id = market.get("instrumentId", "")

            # Spread-data finns direkt
            bid = 0.0
            ask = 0.0
            spread_pct = 0.0

            try:
                bid_data = market.get("bid")
                ask_data = market.get(
                    "offer"
                )  # Capital använder "offer" istället för "ask"

                if bid_data is not None and ask_data is not None:
                    bid = float(bid_data)
                    ask = float(ask_data)

                    if bid > 0 and ask > bid:
                        mid = (bid + ask) / 2.0
                        spread_pct = ((ask - bid) / mid) * 100

                # DEBUG för första 3 instrument
                if i < 3:
                    print(
                        f"DEBUG {epic}: bid={bid}, ask={ask}, spread={spread_pct:.4f}%"
                    )

            except (ValueError, TypeError) as e:
                if i < 3:
                    print(f"DEBUG {epic}: Conversion error - {e}")
                pass

            # Market status
            market_status = market.get("marketStatus", "")

            # Andra fält
            lot_size = market.get("lotSize", 0)
            percentage_change = market.get("percentageChange", 0.0)

            instrument_data = {
                "epic": epic,
                "name": name,
                "market_id": market_id,
                "type": instrument_type,
                "category": "",  # Måste härledas från type
                "sector": "",  # Finns inte i denna API
                "country": "",  # Finns inte i denna API
                "base_currency": "",  # Finns inte direkt
                "market_status": market_status,
                "bid": bid,
                "ask": ask,
                "spread_pct": spread_pct,
                "min_deal_size": lot_size,
                "max_deal_size": 0,  # Finns inte i denna API
                "open_time": "",
                "close_time": "",
                "percentage_change": percentage_change,
            }

            all_markets.append(instrument_data)

    except Exception as e:
        print(f"Fel vid hämtning av marknader: {str(e)}", file=sys.stderr)
        raise

    return all_markets


def filter_and_categorize(markets: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Konverterar till DataFrame och lägg till extra kategoriseringskolumner
    """
    df = pd.DataFrame(markets)

    if df.empty:
        return df

    # NY: Basera asset_class på instrumentType istället
    def determine_asset_class(row):
        instrument_type = str(row.get("type", "")).lower()
        epic = str(row.get("epic", "")).lower()

        if "currencies" in instrument_type or any(
            curr in epic for curr in ["eur", "usd", "gbp", "jpy", "chf"]
        ):
            return "forex"
        elif "crypto" in instrument_type or any(
            crypto in epic for crypto in ["btc", "eth", "ada", "doge"]
        ):
            return "crypto"
        elif "commodities" in instrument_type or any(
            comm in epic for comm in ["gold", "oil", "silver"]
        ):
            return "commodity"
        elif "indices" in instrument_type or any(
            idx in epic for idx in ["dax", "ftse", "sp500", "nasdaq"]
        ):
            return "index"
        elif "shares" in instrument_type or "equities" in instrument_type:
            return "stock"
        else:
            return "other"

    df["asset_class"] = df.apply(determine_asset_class, axis=1)

    # Förbättrad tradeable check
    df["is_tradeable"] = (
        (df["market_status"] == "TRADEABLE") & (df["bid"] > 0) & (df["ask"] > 0)
    )

    # Spread quality (nu borde fungera!)
    def spread_quality(spread_pct):
        if pd.isna(spread_pct):
            return "no_data"
        elif spread_pct <= 0:
            return "no_spread"
        elif spread_pct <= 0.1:
            return "excellent"
        elif spread_pct <= 0.3:
            return "good"
        elif spread_pct <= 0.5:
            return "fair"
        elif spread_pct <= 1.0:
            return "wide"
        else:
            return "very_wide"

    df["spread_quality"] = df["spread_pct"].apply(spread_quality)

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Hämta alla instrument från Capital.com"
    )
    parser.add_argument(
        "--output",
        default="data/scan/all_instruments_capital.csv",
        help="Output CSV-fil (default: data/scan/all_instruments_capital.csv)",
    )
    parser.add_argument(
        "--tradeable-only",
        action="store_true",
        help="Filtrera endast tradeable instrument",
    )
    parser.add_argument(
        "--asset-class",
        choices=["stock", "forex", "crypto", "commodity", "index", "other"],
        help="Filtrera på specifik asset class",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=0,
        help="Minimum deal size (default: 0, ingen filtrering)",
    )
    parser.add_argument(
        "--max-spread",
        type=float,
        default=None,
        help="Maximum spread i procent (t.ex. 0.5 för 0.5%)",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Visa statistik över hämtade instrument"
    )

    args = parser.parse_args()

    # Skapa output-mapp om den inte finns
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    try:
        # Logga in till Capital.com
        session = login_capital()
        print(f"✓ Inloggad på {session.base_url}")

        # Hämta alla marknader
        markets = fetch_all_markets(session)

        if not markets:
            print("Inga instrument hittades", file=sys.stderr)
            sys.exit(1)

        # Konvertera till DataFrame med extra kategorisering
        df = filter_and_categorize(markets)

        print(f"✓ Bearbetade {len(df)} instrument")

        # Applicera filter om specificerade
        original_count = len(df)

        if args.tradeable_only:
            df = df[df["is_tradeable"] == True]
            print(f"  → Filtrerade till {len(df)} tradeable instrument")

        if args.asset_class:
            df = df[df["asset_class"] == args.asset_class]
            print(f"  → Filtrerade till {len(df)} {args.asset_class} instrument")

        if args.min_volume > 0:
            df = df[df["min_deal_size"] >= args.min_volume]
            print(
                f"  → Filtrerade till {len(df)} instrument med min deal size >= {args.min_volume}"
            )

        if args.max_spread is not None:
            df = df[(df["spread_pct"] <= args.max_spread) | (df["spread_pct"].isna())]
            print(
                f"  → Filtrerade till {len(df)} instrument med spread <= {args.max_spread}%"
            )

        # Sortera efter asset_class, sedan spread
        df = df.sort_values(["asset_class", "spread_pct", "epic"])

        # Lägg till timestamp
        df.insert(0, "timestamp", pd.Timestamp.utcnow().isoformat())

        # Spara till CSV
        df.to_csv(args.output, index=False)
        print(f"✓ Sparade {len(df)} instrument till {args.output}")

        # Visa statistik om begärd
        if args.stats:
            print("\n=== STATISTIK ===")
            print(f"Totalt hämtade: {original_count}")
            print(f"Efter filtrering: {len(df)}")

            print("\nFördelning per asset class:")
            asset_counts = df["asset_class"].value_counts()
            for asset_class, count in asset_counts.items():
                print(f"  {asset_class}: {count}")

            print("\nMarket status fördelning:")
            status_counts = df["market_status"].value_counts()
            for status, count in status_counts.items():
                print(f"  {status}: {count}")

            print("\nSpread quality fördelning:")
            spread_counts = df["spread_quality"].value_counts()
            for quality, count in spread_counts.items():
                print(f"  {quality}: {count}")

            # Visa topp 10 lägsta spreads
            tradeable = df[df["is_tradeable"] == True]
            if not tradeable.empty:
                print("\nTopp 10 lägsta spreads (tradeable):")
                top_spreads = tradeable.nsmallest(10, "spread_pct")
                for _, row in top_spreads.iterrows():
                    print(
                        f"  {row['epic']:<15} {row['name']:<30} {row['spread_pct']:>6.3f}% ({row['asset_class']})"
                    )

        # Visa sample av resultatet
        print(f"\n=== SAMPLE (första 5 rader) ===")
        display_cols = [
            "epic",
            "name",
            "asset_class",
            "market_status",
            "spread_pct",
            "is_tradeable",
        ]
        sample = df[display_cols].head()
        print(sample.to_string(index=False))

    except Exception as e:
        print(f"Fel: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

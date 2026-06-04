"""
Broad U.S.-listed universe loader for trading_system.

This module loads free Nasdaq Trader symbol directory files, normalizes symbols
for yfinance, and filters obvious non-common-stock issues before expensive
market-data calls. It does not fetch price history or call AI services.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
CACHE_PATH = "data/us_listed_universe.csv"
UNIVERSE_COLUMNS = [
    "Symbol",
    "Company",
    "Exchange",
    "Source",
    "IsETF",
    "TestIssue",
    "RawSecurityName",
]

EXCHANGE_MAP = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

EXCLUDE_NAME_TERMS = [
    "warrant",
    " wt",
    "unit",
    "right",
    "preferred",
    "preference",
    "depositary shares",
    "notes",
    "bond",
    "debenture",
    "fund",
    "etf",
    "etn",
    " trust",
    "closed end",
    "acquisition corp. unit",
    "spac unit",
]


def _fetch_symbol_directory(url: str) -> pd.DataFrame:
    """Fetch a pipe-delimited Nasdaq Trader symbol directory file."""
    response = requests.get(
        url,
        headers={"User-Agent": "trading_system/1.0"},
        timeout=30,
    )
    response.raise_for_status()

    # Footer rows such as "File Creation Time" are malformed table rows.
    lines = [
        line
        for line in response.text.splitlines()
        if line and not line.lower().startswith("file creation time")
    ]
    return pd.read_csv(StringIO("\n".join(lines)), sep="|", dtype=str)


def _flag_is_yes(value: Any) -> bool:
    """Interpret Y/N style flags."""
    return str(value).strip().upper() == "Y"


def clean_symbol_for_yfinance(symbol: Any) -> Optional[str]:
    """
    Normalize a listing symbol for yfinance.

    Returns None for symbols that are obviously malformed or likely unsupported.
    """
    if symbol is None:
        return None

    cleaned = str(symbol).strip().upper()
    if not cleaned or " " in cleaned:
        return None

    cleaned = cleaned.replace(".", "-")

    # Keep this conservative: obvious non-common suffix punctuation is removed
    # by name filters later, while normal share classes like BRK-B remain.
    unsupported_chars = {"^", "/", "="}
    if any(char in cleaned for char in unsupported_chars):
        return None

    return cleaned


def is_likely_common_stock(row: Any) -> bool:
    """
    Filter out obvious warrants, units, preferreds, funds, notes, and similar.
    """
    if hasattr(row, "get"):
        raw_name = row.get("RawSecurityName") or row.get("Company") or row.get("Security Name")
    else:
        raw_name = None

    name = str(raw_name or "").strip().lower()
    if not name:
        return False

    padded_name = f" {name} "
    for term in EXCLUDE_NAME_TERMS:
        if term.strip() in {"wt", "trust"}:
            if f" {term.strip()} " in padded_name:
                return False
        elif term in name:
            return False

    return True


def _normalize_frame(
    df: pd.DataFrame,
    symbol_column: str,
    company_column: str,
    exchange: Any,
    source: str,
    exchange_column: Optional[str] = None,
) -> pd.DataFrame:
    """Normalize a raw Nasdaq Trader frame into project universe columns."""
    if df is None or df.empty:
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    output = pd.DataFrame()
    output["Symbol"] = df[symbol_column].apply(clean_symbol_for_yfinance)
    output["Company"] = df[company_column].astype(str).str.strip()
    output["RawSecurityName"] = output["Company"]
    if exchange_column and exchange_column in df.columns:
        raw_exchange = df[exchange_column].astype(str).str.strip()
        output["Exchange"] = raw_exchange.map(EXCHANGE_MAP).fillna(raw_exchange)
    else:
        output["Exchange"] = exchange

    output["Source"] = source
    etf_flags = df["ETF"] if "ETF" in df.columns else pd.Series("N", index=df.index)
    test_flags = (
        df["Test Issue"]
        if "Test Issue" in df.columns
        else pd.Series("N", index=df.index)
    )
    output["IsETF"] = etf_flags.apply(_flag_is_yes)
    output["TestIssue"] = test_flags.apply(_flag_is_yes)
    if "NextShares" in df.columns:
        output["NextShares"] = df["NextShares"].apply(_flag_is_yes)
    else:
        output["NextShares"] = False

    output = output.dropna(subset=["Symbol"])
    output = output[output["Symbol"].astype(str).str.len() > 0]
    output = output[~output["TestIssue"]]
    output = output[~output["IsETF"]]
    output = output[~output["NextShares"]]

    return output.loc[:, UNIVERSE_COLUMNS].reset_index(drop=True)


def load_nasdaq_listed() -> pd.DataFrame:
    """
    Load and normalize Nasdaq-listed securities.
    """
    try:
        raw = _fetch_symbol_directory(NASDAQ_LISTED_URL)
    except Exception as exc:
        print(f"Failed to load Nasdaq-listed universe: {exc}", flush=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    required = {"Symbol", "Security Name"}
    if not required.issubset(raw.columns):
        print("Failed to load Nasdaq-listed universe: required columns missing.", flush=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    return _normalize_frame(
        raw,
        symbol_column="Symbol",
        company_column="Security Name",
        exchange="NASDAQ",
        source="nasdaqlisted",
    )


def load_other_listed() -> pd.DataFrame:
    """
    Load and normalize non-Nasdaq exchange-listed securities.
    """
    try:
        raw = _fetch_symbol_directory(OTHER_LISTED_URL)
    except Exception as exc:
        print(f"Failed to load other-listed universe: {exc}", flush=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    required = {"ACT Symbol", "Security Name", "Exchange"}
    if not required.issubset(raw.columns):
        print("Failed to load other-listed universe: required columns missing.", flush=True)
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    return _normalize_frame(
        raw,
        symbol_column="ACT Symbol",
        company_column="Security Name",
        exchange=None,
        source="otherlisted",
        exchange_column="Exchange",
    )


def load_us_listed_universe(
    include_etfs: bool = False, common_stock_only: bool = True
) -> pd.DataFrame:
    """
    Load, combine, and clean broad U.S.-listed equities.
    """
    nasdaq = load_nasdaq_listed()
    other = load_other_listed()
    combined_raw_count = len(nasdaq) + len(other)

    combined = pd.concat([nasdaq, other], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    if not include_etfs:
        combined = combined[~combined["IsETF"]]
    if common_stock_only:
        combined = combined[combined.apply(is_likely_common_stock, axis=1)]

    combined = (
        combined.drop_duplicates(subset=["Symbol"])
        .sort_values("Symbol")
        .reset_index(drop=True)
    )

    combined = combined.loc[:, UNIVERSE_COLUMNS]
    combined.attrs["nasdaq_listed_count"] = len(nasdaq)
    combined.attrs["other_listed_count"] = len(other)
    combined.attrs["combined_raw_count"] = combined_raw_count
    combined.attrs["cleaned_count"] = len(combined)

    print(f"Nasdaq listed count: {len(nasdaq)}", flush=True)
    print(f"Other listed count: {len(other)}", flush=True)
    print(f"Combined raw count: {combined_raw_count}", flush=True)
    print(f"Cleaned universe count: {len(combined)}", flush=True)

    return combined


def save_universe_cache(df: pd.DataFrame, path: str = CACHE_PATH) -> str:
    """Save a universe DataFrame cache and return the filepath."""
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return str(cache_path)


def load_universe_cache(path: str = CACHE_PATH) -> pd.DataFrame:
    """Load a cached universe DataFrame."""
    cache_path = Path(path)
    if not cache_path.exists():
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)

    return pd.read_csv(cache_path, dtype={"Symbol": str})


if __name__ == "__main__":
    universe = load_us_listed_universe()
    print("\nFirst 20 rows:")
    print(universe.head(20))

    symbols_to_check = ["TE", "PLTR", "SMCI", "F", "AAPL"]
    print("\nSymbol presence checks:")
    for ticker in symbols_to_check:
        found = ticker in set(universe["Symbol"])
        print(f"{ticker}: {'FOUND' if found else 'not found'}")

    print(f"\nFinal universe count: {len(universe)}")

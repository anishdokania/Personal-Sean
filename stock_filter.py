"""
Stock filtering engine for the trading_system scanner.

This module loads normalized symbol rows. The primary hard universe gate in
main.py is the first strategic tradability filter.
"""

from __future__ import annotations

from io import StringIO
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from universe import load_us_listed_universe


SP500_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UNIVERSE_COLUMNS = ["Symbol", "Security", "GICS Sector"]
CANDIDATE_COLUMNS = [
    "Symbol",
    "Company",
    "Sector",
    "Exchange",
]

CandidateValue = str


def _empty_universe() -> pd.DataFrame:
    """Return an empty universe with the expected columns."""
    return pd.DataFrame(columns=UNIVERSE_COLUMNS)


def _empty_candidates() -> pd.DataFrame:
    """Return an empty candidate set with the expected columns."""
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


def _normalize_symbol(symbol: str) -> str:
    """Convert Wikipedia symbols into yfinance-compatible symbols."""
    return str(symbol).strip().replace(".", "-")


def _clean_text_field(value: Any) -> str:
    """Return a clean string for optional universe metadata fields."""
    if value is None or pd.isna(value):
        return ""

    return str(value).strip()


def load_sp500_universe() -> pd.DataFrame:
    """
    Load S&P 500 constituents from Wikipedia.

    Returns a DataFrame with Symbol, Security, and GICS Sector columns. If the
    web table cannot be loaded, an empty DataFrame is returned and the scanner
    can exit cleanly.
    """
    try:
        # Wikipedia may reject pandas' default URL opener, so fetch the page
        # with a regular user agent and still parse the table via read_html.
        response = requests.get(
            SP500_WIKIPEDIA_URL,
            headers={"User-Agent": "trading_system/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
    except Exception as exc:
        print(f"Failed to load S&P 500 universe: {exc}", flush=True)
        return _empty_universe()

    for table in tables:
        if all(column in table.columns for column in UNIVERSE_COLUMNS):
            universe = table.loc[:, UNIVERSE_COLUMNS].copy()
            universe["Symbol"] = universe["Symbol"].apply(_normalize_symbol)
            universe["Security"] = universe["Security"].astype(str).str.strip()
            universe["GICS Sector"] = universe["GICS Sector"].astype(str).str.strip()
            return universe.dropna(subset=UNIVERSE_COLUMNS).reset_index(drop=True)

    print("Failed to load S&P 500 universe: required columns were not found.", flush=True)
    return _empty_universe()


def _scan_symbol_rows(
    stocks_to_scan: pd.DataFrame,
    universe_mode: str,
) -> tuple[List[Dict[str, CandidateValue]], Dict[str, str]]:
    """Load normalized symbol rows for the primary hard gate."""
    candidates: List[Dict[str, CandidateValue]] = []
    failures: Dict[str, str] = {}
    total_count = len(stocks_to_scan)

    for idx, stock in stocks_to_scan.iterrows():
        symbol = str(stock.get("Symbol", "")).strip()
        if not symbol:
            continue

        company = (
            _clean_text_field(stock.get("Security"))
            or _clean_text_field(stock.get("Company"))
            or _clean_text_field(stock.get("RawSecurityName"))
        )
        sector = _clean_text_field(stock.get("GICS Sector")) or _clean_text_field(
            stock.get("Sector")
        )
        exchange = _clean_text_field(stock.get("Exchange"))

        if universe_mode == "us_listed":
            print(f"Scanning {idx + 1}/{total_count} {symbol}...", flush=True)
        else:
            print(f"Scanning {symbol}...", flush=True)

        try:
            candidates.append(
                {
                    "Symbol": symbol,
                    "Company": company,
                    "Sector": sector,
                    "Exchange": exchange,
                }
            )
        except Exception as exc:
            failures[symbol] = str(exc)
            print(f"Skipping {symbol}: {exc}", flush=True)

    return candidates, failures


def scan_candidates(
    universe_mode: str = "sp500",
    max_universe_size: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load stock symbols for the scanner.

    universe_mode:
    - sp500: S&P 500 symbol rows.
    - us_listed: broad Nasdaq Trader symbol-directory universe scan.

    This only returns available symbol rows. The primary hard universe gate is
    the first strategic tradability filter.
    """
    normalized_mode = str(universe_mode or "sp500").strip().lower()
    if max_universe_size is not None and max_universe_size <= 0:
        raise ValueError("max_universe_size must be positive when provided.")

    if normalized_mode not in {"sp500", "us_listed"}:
        raise ValueError("universe_mode must be 'sp500' or 'us_listed'.")

    if normalized_mode == "us_listed":
        universe = load_us_listed_universe()
        if universe.empty:
            return _empty_candidates()

        raw_universe_count = len(universe)
        stocks_to_scan = universe.copy()
        if max_universe_size is not None:
            stocks_to_scan = stocks_to_scan.head(max_universe_size).reset_index(drop=True)
        else:
            stocks_to_scan = stocks_to_scan.reset_index(drop=True)

        print(
            f"US-listed universe loaded: {raw_universe_count} symbols; "
            f"scanning {len(stocks_to_scan)}.",
            flush=True,
        )

        candidates, failures = _scan_symbol_rows(
            stocks_to_scan,
            normalized_mode,
        )
        results = pd.DataFrame(candidates, columns=CANDIDATE_COLUMNS)

        results.attrs["failures"] = failures
        results.attrs["universe_mode"] = normalized_mode
        results.attrs["raw_universe_count"] = raw_universe_count
        results.attrs["scanned_universe_count"] = len(stocks_to_scan)
        return results

    universe = load_sp500_universe()
    if universe.empty:
        return _empty_candidates()

    stocks_to_scan = universe.copy()
    stocks_to_scan["Exchange"] = ""
    if max_universe_size is not None:
        stocks_to_scan = stocks_to_scan.head(max_universe_size).reset_index(drop=True)

    if stocks_to_scan.empty:
        print("No S&P 500 stocks matched the selected universe settings.", flush=True)
        return _empty_candidates()

    candidates, failures = _scan_symbol_rows(
        stocks_to_scan.reset_index(drop=True),
        normalized_mode,
    )

    results = pd.DataFrame(candidates, columns=CANDIDATE_COLUMNS)

    results.attrs["failures"] = failures
    results.attrs["universe_mode"] = normalized_mode
    results.attrs["raw_universe_count"] = len(universe)
    results.attrs["scanned_universe_count"] = len(stocks_to_scan)
    return results


if __name__ == "__main__":
    candidate_results = scan_candidates()

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 140)

    print(f"\nCandidate count: {len(candidate_results)}")
    print(candidate_results.head(20))

    if candidate_results.attrs.get("failures"):
        print("\nSkipped tickers / errors:")
        print(candidate_results.attrs["failures"])

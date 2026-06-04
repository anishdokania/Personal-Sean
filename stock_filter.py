"""
Stock filtering engine for the trading_system scanner.

Module 3 finds candidate stocks from leading sectors using simple,
blueprint-inspired filters. Deeper technical analysis belongs in Module 4.
"""

from __future__ import annotations

from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Union

import pandas as pd
import requests

from data_fetcher import fetch_stock_data, validate_ohlcv
from sector_scanner import get_top_sectors
from universe import load_us_listed_universe


SP500_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UNIVERSE_COLUMNS = ["Symbol", "Security", "GICS Sector"]
MIN_PRICE = 3
MIN_AVG_VOLUME = 500_000
MIN_DOLLAR_VOLUME = 20_000_000
CANDIDATE_COLUMNS = [
    "Symbol",
    "Company",
    "Sector",
    "Exchange",
    "Close",
    "AvgVolume20",
    "DollarVolume20",
    "SMA20",
    "AboveSMA20",
]

# Module 2 uses clean trading-sector names. Wikipedia uses official GICS names
# for a couple of sectors, so this map keeps the modules compatible.
SECTOR_NAME_ALIASES = {
    "Technology": "Information Technology",
    "Healthcare": "Health Care",
}
BasicMetricValue = Union[float, bool]
CandidateValue = Union[float, bool, str]


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


def _normalize_top_sector_names(top_sectors: Iterable[str]) -> List[str]:
    """Translate Module 2 sector names into Wikipedia GICS sector names."""
    normalized_sectors: List[str] = []

    for sector in top_sectors:
        sector_name = str(sector).strip()
        if not sector_name:
            continue

        normalized_sectors.append(SECTOR_NAME_ALIASES.get(sector_name, sector_name))

    return normalized_sectors


def filter_by_top_sectors(
    universe_df: pd.DataFrame, top_sectors: Iterable[str]
) -> pd.DataFrame:
    """
    Keep only S&P 500 stocks that belong to the selected leading sectors.
    """
    if universe_df.empty:
        return _empty_universe()

    missing_columns = [column for column in UNIVERSE_COLUMNS if column not in universe_df.columns]
    if missing_columns:
        raise ValueError(
            f"Universe DataFrame is missing required columns: {', '.join(missing_columns)}"
        )

    normalized_sectors = _normalize_top_sector_names(top_sectors)
    if not normalized_sectors:
        return _empty_universe()

    filtered = universe_df[universe_df["GICS Sector"].isin(normalized_sectors)].copy()
    return filtered.reset_index(drop=True)


def _calculate_basic_metrics(df: pd.DataFrame) -> Dict[str, BasicMetricValue]:
    """Calculate the Module 3 price, volume, and SMA metrics."""
    validate_ohlcv(df)

    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    volumes = pd.to_numeric(df["Volume"], errors="coerce").dropna()

    if len(closes) < 20 or len(volumes) < 20:
        raise ValueError("Not enough clean history to calculate 20-day filters.")

    latest_close = float(closes.iloc[-1])
    sma20 = float(closes.tail(20).mean())
    avg_volume20 = float(volumes.tail(20).mean())
    dollar_volume20 = latest_close * avg_volume20

    return {
        "Close": latest_close,
        "AvgVolume20": avg_volume20,
        "DollarVolume20": dollar_volume20,
        "SMA20": sma20,
        "AboveSMA20": bool(latest_close > sma20),
    }


def passes_basic_filters(df: pd.DataFrame) -> bool:
    """
    Apply basic blueprint-inspired candidate filters.

    Rules:
    - Latest close must be above $3.
    - 20-day average volume must be above 500,000 shares.
    - Latest close must be above the 20-day simple moving average.
    """
    try:
        metrics = _calculate_basic_metrics(df)
    except Exception:
        return False

    return bool(
        metrics["Close"] > MIN_PRICE
        and metrics["AvgVolume20"] > MIN_AVG_VOLUME
        and metrics["AboveSMA20"]
    )


def passes_broad_universe_filters(df: pd.DataFrame) -> bool:
    """
    Apply broad-universe tradability filters.

    Adds a dollar-volume floor so low-priced or thinly traded symbols are not
    expensive downstream candidates.
    """
    try:
        metrics = _calculate_basic_metrics(df)
    except Exception:
        return False

    return bool(
        metrics["Close"] > MIN_PRICE
        and metrics["AvgVolume20"] > MIN_AVG_VOLUME
        and metrics["DollarVolume20"] > MIN_DOLLAR_VOLUME
        and metrics["AboveSMA20"]
    )


def _limit_stocks_per_sector(
    stocks_df: pd.DataFrame, max_stocks_per_sector: Optional[int]
) -> pd.DataFrame:
    """Optionally cap how many symbols are scanned from each selected sector."""
    if max_stocks_per_sector is None:
        return stocks_df

    if max_stocks_per_sector <= 0:
        raise ValueError("max_stocks_per_sector must be positive when provided.")

    return (
        stocks_df.groupby("GICS Sector", group_keys=False)
        .head(max_stocks_per_sector)
        .reset_index(drop=True)
    )


def _scan_symbol_rows(
    stocks_to_scan: pd.DataFrame,
    universe_mode: str,
    apply_basic_filters: bool = False,
) -> tuple[List[Dict[str, CandidateValue]], Dict[str, str]]:
    """Load normalized symbol rows, optionally applying legacy light filters."""
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
            if not apply_basic_filters:
                candidates.append(
                    {
                        "Symbol": symbol,
                        "Company": company,
                        "Sector": sector,
                        "Exchange": exchange,
                    }
                )
                continue

            df = fetch_stock_data(symbol, period="3mo", interval="1d")

            if universe_mode == "us_listed":
                if not passes_broad_universe_filters(df):
                    continue
            elif not passes_basic_filters(df):
                continue

            metrics = _calculate_basic_metrics(df)
            candidates.append(
                {
                    "Symbol": symbol,
                    "Company": company,
                    "Sector": sector,
                    "Exchange": exchange,
                    "Close": metrics["Close"],
                    "AvgVolume20": metrics["AvgVolume20"],
                    "DollarVolume20": metrics["DollarVolume20"],
                    "SMA20": metrics["SMA20"],
                    "AboveSMA20": metrics["AboveSMA20"],
                }
            )
        except Exception as exc:
            failures[symbol] = str(exc)
            print(f"Skipping {symbol}: {exc}", flush=True)

    return candidates, failures


def scan_candidates(
    top_n_sectors: int = 5,
    max_stocks_per_sector: Optional[int] = None,
    universe_mode: str = "sp500",
    max_universe_size: Optional[int] = None,
    apply_basic_filters: bool = False,
    apply_sector_filter: bool = False,
) -> pd.DataFrame:
    """
    Load stock symbols for the scanner.

    universe_mode:
    - sp500: S&P 500 symbol rows.
    - us_listed: broad Nasdaq Trader symbol-directory universe scan.

    By default this only returns available symbol rows so the primary hard
    universe gate is the first strategic filter. The old light price/volume/SMA
    filters remain available only for explicit legacy calls.
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
            apply_basic_filters=apply_basic_filters,
        )
        results = pd.DataFrame(candidates, columns=CANDIDATE_COLUMNS)
        if apply_basic_filters and not results.empty:
            results = results.sort_values(
                ["DollarVolume20", "AvgVolume20"], ascending=[False, False]
            ).reset_index(drop=True)

        results.attrs["failures"] = failures
        results.attrs["universe_mode"] = normalized_mode
        results.attrs["raw_universe_count"] = raw_universe_count
        results.attrs["scanned_universe_count"] = len(stocks_to_scan)
        return results

    universe = load_sp500_universe()
    if universe.empty:
        return _empty_candidates()

    top_sector_names: List[str] = []
    if apply_sector_filter:
        top_sector_ranking = get_top_sectors(top_n_sectors)
        if top_sector_ranking.empty:
            print("No top sectors available. Candidate scan stopped.", flush=True)
            return _empty_candidates()

        top_sector_names = top_sector_ranking["Sector"].tolist()
        stocks_to_scan = filter_by_top_sectors(universe, top_sector_names)
    else:
        stocks_to_scan = universe.copy()

    stocks_to_scan = _limit_stocks_per_sector(stocks_to_scan, max_stocks_per_sector)
    stocks_to_scan["Exchange"] = ""
    if max_universe_size is not None:
        stocks_to_scan = stocks_to_scan.head(max_universe_size).reset_index(drop=True)

    if stocks_to_scan.empty:
        print("No S&P 500 stocks matched the selected universe settings.", flush=True)
        return _empty_candidates()

    candidates, failures = _scan_symbol_rows(
        stocks_to_scan.reset_index(drop=True),
        normalized_mode,
        apply_basic_filters=apply_basic_filters,
    )

    results = pd.DataFrame(candidates, columns=CANDIDATE_COLUMNS)
    if apply_basic_filters and not results.empty:
        results = results.sort_values("AvgVolume20", ascending=False).reset_index(drop=True)

    results.attrs["failures"] = failures
    results.attrs["top_sectors"] = top_sector_names
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

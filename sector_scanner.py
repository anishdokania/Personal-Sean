"""
Sector ranking engine for the trading_system scanner.

Module 2 implements the blueprint idea: strong stocks are best found inside
strong sectors. Sector ETFs are used as liquid proxies for each major sector.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from data_fetcher import fetch_stock_data, validate_ohlcv


SECTOR_ETFS: Dict[str, str] = {
    "Technology": "XLK",
    "Energy": "XLE",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Financials": "XLF",
    "Real Estate": "XLRE",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Healthcare": "XLV",
    "Consumer Staples": "XLP",
}

LOOKBACKS = {
    "1W": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
}
LOOKBACK_WEIGHTS = {
    "1W": 0.20,
    "1M": 0.30,
    "3M": 0.30,
    "6M": 0.10,
    "1Y": 0.10,
}
RETURN_COLUMNS = [f"{label}_Return" for label in LOOKBACKS]
OUTPUT_COLUMNS = [
    "Sector",
    "ETF",
    *RETURN_COLUMNS,
    "Score",
    "SectorRank",
]


def calculate_performance(df: pd.DataFrame, days: int) -> float:
    """
    Calculate percentage return over a given number of trading days.

    Returns NaN when there is not enough history to calculate the requested
    lookback. This lets callers skip incomplete symbols without crashing.
    """
    if days <= 0:
        raise ValueError("days must be a positive integer.")

    validate_ohlcv(df)

    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()

    # A 5-day return compares the latest close with the close 5 sessions ago.
    if len(closes) <= days:
        return float("nan")

    latest_close = closes.iloc[-1]
    historical_close = closes.iloc[-days - 1]

    if pd.isna(latest_close) or pd.isna(historical_close) or historical_close == 0:
        return float("nan")

    return float(((latest_close / historical_close) - 1) * 100)


def _weighted_sector_score(row: Dict[str, float]) -> Optional[float]:
    """Calculate weighted sector score, normalizing when lookbacks are missing."""
    weighted_score = 0.0
    available_weight = 0.0

    for label, weight in LOOKBACK_WEIGHTS.items():
        value = row.get(f"{label}_Return")
        if value is None or pd.isna(value):
            continue

        weighted_score += float(value) * weight
        available_weight += weight

    if available_weight <= 0:
        return None

    return weighted_score / available_weight


def rank_sectors() -> pd.DataFrame:
    """
    Fetch sector ETF data, calculate returns, and rank sectors by combined score.

    Returns:
        DataFrame with Sector, ETF, timeframe returns, weighted Score, and
        SectorRank, sorted from strongest to weakest combined sector
        strength.
    """
    sector_rows: List[Dict[str, float | str]] = []
    errors: Dict[str, str] = {}

    for sector, etf in SECTOR_ETFS.items():
        try:
            df = fetch_stock_data(etf, period="18mo", interval="1d")

            row: Dict[str, float | str] = {
                "Sector": sector,
                "ETF": etf,
            }
            for label, days in LOOKBACKS.items():
                row[f"{label}_Return"] = calculate_performance(df, days)

            score = _weighted_sector_score(row)  # type: ignore[arg-type]
            if score is None:
                errors[etf] = f"{sector}: insufficient clean history for ranking."
                continue

            row["Score"] = score
            sector_rows.append(row)
        except Exception as exc:
            errors[etf] = f"{sector}: {exc}"

    if not sector_rows:
        ranking = pd.DataFrame(columns=OUTPUT_COLUMNS)
        ranking.attrs["errors"] = errors
        return ranking

    ranking = pd.DataFrame(sector_rows)
    ranking = ranking.sort_values(
        by=["Score", "1M_Return", "3M_Return", "1W_Return"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    ranking["SectorRank"] = ranking.index + 1
    ranking = ranking.loc[:, OUTPUT_COLUMNS]

    ranking.attrs["errors"] = errors
    return ranking


def get_top_sectors(n: int = 5) -> pd.DataFrame:
    """Return the top N sectors from the current sector ranking."""
    if n <= 0:
        raise ValueError("n must be a positive integer.")

    return rank_sectors().head(n).reset_index(drop=True)


if __name__ == "__main__":
    ranked_sectors = rank_sectors()

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)

    print("Full sector ranking:")
    print(ranked_sectors)

    if ranked_sectors.attrs.get("errors"):
        print("\nSkipped ETFs / errors:")
        print(ranked_sectors.attrs["errors"])

    print("\nTop 5 sectors:")
    print(ranked_sectors.head(5))

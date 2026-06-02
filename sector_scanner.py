"""
Sector ranking engine for the trading_system scanner.

Module 2 implements the blueprint idea: strong stocks are best found inside
strong sectors. Sector ETFs are used as liquid proxies for each major sector.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from data_fetcher import fetch_stock_data, validate_ohlcv


SECTOR_ETFS: Dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

RETURN_COLUMNS = ["1W_Return", "1M_Return", "3M_Return"]
RANK_COLUMNS = {
    "1W_Return": "Rank_1W",
    "1M_Return": "Rank_1M",
    "3M_Return": "Rank_3M",
}
OUTPUT_COLUMNS = [
    "Sector",
    "ETF",
    *RETURN_COLUMNS,
    "Rank_1W",
    "Rank_1M",
    "Rank_3M",
    "Score",
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


def _assign_rank_scores(ranking: pd.DataFrame, return_column: str) -> pd.Series:
    """Assign 11-to-1 style rank scores for one return timeframe."""
    max_score = len(SECTOR_ETFS)
    sorted_returns = ranking[return_column].sort_values(ascending=False)

    rank_scores = {
        index: max_score - rank_position
        for rank_position, index in enumerate(sorted_returns.index)
    }

    return ranking.index.to_series().map(rank_scores).astype(int)


def _assign_display_ranks(ranking: pd.DataFrame, return_column: str) -> pd.Series:
    """Assign transparent ranks where the strongest return is rank 1."""
    sorted_returns = ranking[return_column].sort_values(ascending=False)

    display_ranks = {
        index: rank_position + 1
        for rank_position, index in enumerate(sorted_returns.index)
    }

    return ranking.index.to_series().map(display_ranks).astype(int)


def rank_sectors() -> pd.DataFrame:
    """
    Fetch sector ETF data, calculate returns, and rank sectors by combined score.

    Returns:
        DataFrame with Sector, ETF, timeframe returns, transparent timeframe
        ranks, and Score, sorted from strongest to weakest combined sector
        strength.
    """
    sector_rows: List[Dict[str, float | str]] = []
    errors: Dict[str, str] = {}

    for sector, etf in SECTOR_ETFS.items():
        try:
            df = fetch_stock_data(etf, period="6mo", interval="1d")

            one_week_return = calculate_performance(df, 5)
            one_month_return = calculate_performance(df, 21)
            three_month_return = calculate_performance(df, 63)

            returns = [one_week_return, one_month_return, three_month_return]
            if any(pd.isna(value) for value in returns):
                errors[etf] = f"{sector}: insufficient clean history for ranking."
                continue

            sector_rows.append(
                {
                    "Sector": sector,
                    "ETF": etf,
                    "1W_Return": one_week_return,
                    "1M_Return": one_month_return,
                    "3M_Return": three_month_return,
                }
            )
        except Exception as exc:
            errors[etf] = f"{sector}: {exc}"

    if not sector_rows:
        ranking = pd.DataFrame(columns=OUTPUT_COLUMNS)
        ranking.attrs["errors"] = errors
        return ranking

    ranking = pd.DataFrame(sector_rows)
    score_columns = []

    for return_column in RETURN_COLUMNS:
        score_column = f"{return_column}_Rank_Score"
        rank_column = RANK_COLUMNS[return_column]

        # Display rank: best return is 1, second best is 2, and so on.
        ranking[rank_column] = _assign_display_ranks(ranking, return_column)

        # Score contribution is intentionally unchanged: best sector receives
        # the highest points for this timeframe.
        ranking[score_column] = _assign_rank_scores(ranking, return_column)
        score_columns.append(score_column)

    ranking["Score"] = ranking[score_columns].sum(axis=1)
    ranking = ranking.loc[:, OUTPUT_COLUMNS]
    ranking = ranking.sort_values(
        by=["Score", "1M_Return", "3M_Return", "1W_Return"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)

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

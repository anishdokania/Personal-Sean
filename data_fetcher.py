"""
Data fetching utilities for the trading_system scanner.

This module intentionally stays independent so future scanner modules can import
clean OHLCV data without needing to know anything about yfinance quirks.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

import pandas as pd
import yfinance as yf


REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
PREFERRED_COLUMN_ORDER = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _normalize_ticker(ticker: str) -> str:
    """Return a clean ticker symbol or raise a clear error."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("Ticker must be a non-empty string.")

    return ticker.strip().upper()


def _flatten_yfinance_columns(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Convert yfinance output into predictable single-level OHLCV columns.

    Depending on yfinance version and parameters, a single-ticker download may
    still return MultiIndex columns such as ("Close", "AAPL"). This helper
    strips the ticker level while preserving standard price field names.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df.copy()

    cleaned = df.copy()
    ticker_upper = ticker.upper()

    for level in range(cleaned.columns.nlevels):
        level_values = {
            str(value).upper()
            for value in cleaned.columns.get_level_values(level)
            if value is not None
        }

        if ticker_upper in level_values:
            cleaned.columns = cleaned.columns.droplevel(level)
            break

    if isinstance(cleaned.columns, pd.MultiIndex) and cleaned.columns.nlevels == 1:
        cleaned.columns = cleaned.columns.get_level_values(0)

    if isinstance(cleaned.columns, pd.MultiIndex):
        # Last-resort flattening keeps known yfinance field names if present.
        flattened_columns = []
        known_columns = set(PREFERRED_COLUMN_ORDER)

        for column in cleaned.columns:
            matching_names = [part for part in column if str(part) in known_columns]
            flattened_columns.append(str(matching_names[0] if matching_names else column[-1]))

        cleaned.columns = flattened_columns

    return cleaned


def _clean_ohlcv_data(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Keep expected columns, coerce numeric values, and remove incomplete rows."""
    cleaned = _flatten_yfinance_columns(df, ticker)
    cleaned = cleaned.rename(columns={str(column): str(column).strip() for column in cleaned.columns})

    missing_columns = [column for column in REQUIRED_OHLCV_COLUMNS if column not in cleaned.columns]
    if missing_columns:
        raise ValueError(
            f"{ticker}: missing required OHLCV columns: {', '.join(missing_columns)}"
        )

    output_columns = [column for column in PREFERRED_COLUMN_ORDER if column in cleaned.columns]
    cleaned = cleaned.loc[:, output_columns].copy()

    for column in output_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned = cleaned.dropna(subset=REQUIRED_OHLCV_COLUMNS)

    if cleaned.empty:
        raise ValueError(f"{ticker}: no usable OHLCV rows after cleaning missing values.")

    cleaned.columns.name = None
    validate_ohlcv(cleaned)
    return cleaned


def validate_ohlcv(df: pd.DataFrame) -> bool:
    """
    Validate that a DataFrame has usable OHLCV data.

    Returns True when valid. Raises ValueError with a clear message otherwise.
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("OHLCV data must be a pandas DataFrame.")

    if df.empty:
        raise ValueError("OHLCV DataFrame is empty.")

    missing_columns = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(
            f"OHLCV DataFrame is missing required columns: {', '.join(missing_columns)}"
        )

    if not pd.api.types.is_numeric_dtype(df["Volume"]):
        raise ValueError("OHLCV DataFrame Volume column must be numeric.")

    return True


def fetch_stock_data(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """
    Fetch clean OHLCV data for a single ticker.

    Parameters:
        ticker: Stock ticker symbol, such as "AAPL".
        period: yfinance period string, such as "6mo", "1y", or "5d".
        interval: yfinance interval string, such as "1d", "1h", or "5m".

    Returns:
        A cleaned pandas DataFrame with Open, High, Low, Close, optional
        Adj Close, and Volume columns.
    """
    symbol = _normalize_ticker(ticker)

    try:
        raw_data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        raise RuntimeError(f"{symbol}: yfinance download failed: {exc}") from exc

    if raw_data is None or raw_data.empty:
        raise ValueError(f"{symbol}: no data returned by yfinance.")

    return _clean_ohlcv_data(raw_data, symbol)


def fetch_multiple_stocks(
    tickers: Iterable[str], period: str = "6mo", interval: str = "1d"
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """
    Fetch clean OHLCV data for multiple tickers.

    One failed ticker will not stop the rest of the batch. Successful results
    are returned in data, and failures are returned in errors.
    """
    data: Dict[str, pd.DataFrame] = {}
    errors: Dict[str, str] = {}

    for ticker in tickers:
        try:
            symbol = _normalize_ticker(ticker)
            data[symbol] = fetch_stock_data(symbol, period=period, interval=interval)
        except Exception as exc:
            error_key = ticker if isinstance(ticker, str) and ticker.strip() else str(ticker)
            errors[error_key] = str(exc)

    return data, errors


if __name__ == "__main__":
    data = fetch_stock_data("AAPL")
    print(data.tail())
    print("Single ticker fetch passed")

    tickers = ["AAPL", "MSFT", "NVDA"]
    results, errors = fetch_multiple_stocks(tickers)
    print(results.keys())
    print(errors)
    print("Multiple ticker fetch passed")

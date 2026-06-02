"""
Static chart image generation for trading_system visual review.

This module uses local OHLCV data only. It does not use TradingView, browser
automation, or external charting services.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

MPL_CACHE_DIR = Path("/private/tmp/trading_system_mpl_cache")
MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE_DIR))

try:
    import mplfinance as mpf
except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent.
    raise ImportError(
        "mplfinance is required for chart generation. Install requirements.txt first."
    ) from exc


REQUIRED_CHART_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _clean_symbol(symbol: str) -> str:
    """Return a filesystem-friendly uppercase symbol."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string.")

    return symbol.strip().upper().replace("/", "-")


def _prepare_chart_data(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Return recent OHLCV data with EMA overlays ready for mplfinance."""
    if lookback <= 0:
        raise ValueError("lookback must be a positive integer.")
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("df must be a non-empty pandas DataFrame.")

    missing_columns = [column for column in REQUIRED_CHART_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"DataFrame is missing required columns: {', '.join(missing_columns)}")

    chart_df = df.copy()
    for column in REQUIRED_CHART_COLUMNS:
        chart_df[column] = pd.to_numeric(chart_df[column], errors="coerce")

    chart_df = chart_df.dropna(subset=REQUIRED_CHART_COLUMNS).tail(lookback)
    if chart_df.empty:
        raise ValueError("No clean OHLCV rows available for chart generation.")

    if not isinstance(chart_df.index, pd.DatetimeIndex):
        chart_df.index = pd.to_datetime(chart_df.index, errors="coerce")
        chart_df = chart_df[chart_df.index.notna()]

    if chart_df.empty:
        raise ValueError("Chart data must have a usable date-like index.")

    for span in [8, 21, 50]:
        column = f"EMA{span}"
        if column not in chart_df.columns:
            chart_df[column] = chart_df["Close"].ewm(span=span, adjust=False).mean()
        else:
            chart_df[column] = pd.to_numeric(chart_df[column], errors="coerce")

    return chart_df


def _latest_date_stamp(chart_df: pd.DataFrame) -> str:
    """Return YYYY-MM-DD from the latest chart row."""
    latest_index: Any = chart_df.index[-1]
    if hasattr(latest_index, "date"):
        return latest_index.date().isoformat()

    return pd.Timestamp(latest_index).date().isoformat()


def generate_chart_image(
    symbol: str, df: pd.DataFrame, output_dir: str = "charts", lookback: int = 90
) -> str:
    """
    Generate a readable daily candlestick chart PNG with volume and EMA overlays.
    """
    symbol_clean = _clean_symbol(symbol)
    chart_df = _prepare_chart_data(df, lookback)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    date_stamp = _latest_date_stamp(chart_df)
    filepath = output_path / f"{symbol_clean}_{date_stamp}.png"

    ema_plots = [
        mpf.make_addplot(chart_df["EMA8"], color="#1f77b4", width=1.2),
        mpf.make_addplot(chart_df["EMA21"], color="#ff7f0e", width=1.2),
        mpf.make_addplot(chart_df["EMA50"], color="#2ca02c", width=1.2),
    ]

    style = mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle=":", y_on_right=False)
    title = f"{symbol_clean} Daily Chart - {date_stamp}"

    mpf.plot(
        chart_df,
        type="candle",
        style=style,
        addplot=ema_plots,
        volume=True,
        title=title,
        ylabel="Price",
        ylabel_lower="Volume",
        figsize=(14, 8),
        tight_layout=True,
        warn_too_much_data=lookback + 20,
        savefig={"fname": str(filepath), "dpi": 140, "bbox_inches": "tight"},
    )

    return str(filepath)


if __name__ == "__main__":
    from data_fetcher import fetch_stock_data

    stock_df = fetch_stock_data("MSFT")
    path = generate_chart_image("MSFT", stock_df)
    print(f"Chart saved: {path}")

"""Historical data access for the backtest.

Tier 1 (implemented): daily bars from yfinance, cached to disk. Yahoo gives the
full daily history for free, which is enough to backtest setup *selection* and
the daily-resolution entry/stop model.

Tier 2 (interface only): intraday bars. Yahoo cannot serve years of intraday
data (1m ~7 days, 5m ~60 days, 1h ~2yr), so a real intraday backtest needs a
paid/registered source (Polygon, Alpaca, Databento, Tiingo). `IntradayProvider`
defines the seam where that plugs in without touching the engine.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf


CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _cache_path(symbol: str, start: str, end: str) -> str:
    safe = symbol.replace("/", "_").replace(".", "-")
    return os.path.join(CACHE_DIR, f"{safe}_{start}_{end}_1d.csv")


def _normalize(df: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    """Flatten yfinance output to a clean Open/High/Low/Close/Volume frame."""
    if df is None or df.empty:
        return None

    # yfinance returns a column MultiIndex when given a single ticker in some
    # versions; collapse it to the price field name.
    if isinstance(df.columns, pd.MultiIndex):
        # level 0 is the field (Open/High/...), level 1 is the ticker
        df = df.copy()
        df.columns = [c[0] for c in df.columns]

    rename = {c: c.title() for c in df.columns}
    df = df.rename(columns=rename)

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        return None

    out = df[OHLCV_COLUMNS].copy()
    for col in OHLCV_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=OHLCV_COLUMNS)
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if not out.empty else None


class DailyDataProvider:
    """Daily OHLCV with a simple on-disk CSV cache."""

    def __init__(self, start: str, end: str, use_cache: bool = True) -> None:
        self.start = start
        self.end = end
        self.use_cache = use_cache
        os.makedirs(CACHE_DIR, exist_ok=True)

    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        path = _cache_path(symbol, self.start, self.end)
        if self.use_cache and os.path.exists(path):
            try:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                norm = _normalize(df, symbol)
                if norm is not None:
                    return norm
            except Exception:
                pass

        try:
            raw = yf.download(
                symbol,
                start=self.start,
                end=self.end,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            print(f"  ! download failed for {symbol}: {exc}", flush=True)
            return None

        norm = _normalize(raw, symbol)
        if norm is None:
            return None

        if self.use_cache:
            try:
                norm.to_csv(path)
            except Exception:
                pass
        return norm

    def get_many(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        for i, sym in enumerate(symbols, 1):
            df = self.get(sym)
            status = f"{len(df)} bars" if df is not None else "no data"
            print(f"  [{i}/{len(symbols)}] {sym}: {status}", flush=True)
            if df is not None and len(df) > 0:
                out[sym] = df
        return out


class IntradayProvider:
    """Interface placeholder for Tier-2 intraday backtesting.

    Implement `get(symbol, day)` against Polygon/Alpaca/etc. to return a single
    session's lower-timeframe bars. The engine can then replace the daily
    entry/stop approximation with real intrabar fills.
    """

    def get(self, symbol: str, day) -> Optional[pd.DataFrame]:  # pragma: no cover
        raise NotImplementedError(
            "Intraday backtesting needs a paid/registered data source. "
            "Wire Polygon/Alpaca/Databento here."
        )

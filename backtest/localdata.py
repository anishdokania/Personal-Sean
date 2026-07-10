"""Offline data access: reads the committed marketdata snapshot.

The dev environment cannot reach market-data hosts, so `backtest.fetch_data`
runs on a GitHub Actions runner and commits gzipped CSVs under
backtest/marketdata/. This module serves them with the same interface as
`DailyDataProvider`, so every backtest tool can run fully offline and every
run is reproducible against a pinned snapshot.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import pandas as pd

MARKETDATA_DIR = os.path.join(os.path.dirname(__file__), "marketdata")
DAILY_DIR = os.path.join(MARKETDATA_DIR, "daily")
HOURLY_DIR = os.path.join(MARKETDATA_DIR, "hourly")

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class SnapshotMissingError(RuntimeError):
    pass


def _require_snapshot(path: str) -> None:
    if not os.path.isdir(path):
        raise SnapshotMissingError(
            f"No market data snapshot at {path}. Run the 'Backtest Market Data "
            "Snapshot' GitHub Actions workflow on this branch (or `python -m "
            "backtest.fetch_data` on a machine with market-data access), then "
            "pull the committed snapshot."
        )


class LocalDataProvider:
    """Daily OHLCV from the committed snapshot; API-compatible with
    DailyDataProvider.get / get_many."""

    def __init__(self, start: str, end: str, use_cache: bool = True) -> None:
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        _require_snapshot(DAILY_DIR)

    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        path = os.path.join(DAILY_DIR, f"{symbol.upper()}.csv.gz")
        if not os.path.exists(path):
            return None
        df = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
        for c in OHLCV:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
        df = df.loc[(df.index >= self.start) & (df.index <= self.end)]
        return df if not df.empty else None

    def get_many(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        missing: List[str] = []
        for sym in symbols:
            df = self.get(sym)
            if df is not None and len(df) > 0:
                out[sym] = df
            else:
                missing.append(sym)
        if missing:
            print(f"  (no local daily data for: {', '.join(missing)})", flush=True)
        return out


class HourlyStore:
    """Hourly OHLCV sessions from the snapshot, for intrabar ambiguity
    resolution. Bars are indexed in America/New_York time; a session is all
    bars whose ET date matches the requested day."""

    def __init__(self) -> None:
        self._frames: Dict[str, Optional[pd.DataFrame]] = {}
        self.available = os.path.isdir(HOURLY_DIR)

    def _load(self, symbol: str) -> Optional[pd.DataFrame]:
        sym = symbol.upper()
        if sym in self._frames:
            return self._frames[sym]
        path = os.path.join(HOURLY_DIR, f"{sym}.csv.gz")
        if not self.available or not os.path.exists(path):
            self._frames[sym] = None
            return None
        df = pd.read_csv(path)
        idx = pd.to_datetime(df["Datetime"], utc=True).dt.tz_convert("America/New_York")
        df = df.set_index(idx).drop(columns=["Datetime"])
        for c in OHLCV:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
        df["_date"] = df.index.date
        self._frames[sym] = df
        return df

    def session(self, symbol: str, day: pd.Timestamp) -> Optional[pd.DataFrame]:
        df = self._load(symbol)
        if df is None:
            return None
        bars = df[df["_date"] == pd.Timestamp(day).date()]
        # A real regular session has 7 hourly bars (09:30..15:30). Require most
        # of the session to be present so a partial download can't fake a
        # resolution.
        if len(bars) < 5:
            return None
        return bars

    def coverage_start(self, symbol: str) -> Optional[pd.Timestamp]:
        df = self._load(symbol)
        if df is None or df.empty:
            return None
        return pd.Timestamp(df.index[0].date())

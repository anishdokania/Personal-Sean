"""Bulk market-data fetcher for the backtest — designed to run on a GitHub
Actions runner (which has open internet), not inside a sandboxed dev session.

Writes a committed, reproducible data snapshot under backtest/marketdata/:

    marketdata/daily/{SYM}.csv.gz    daily OHLCV, 2021-01-01 -> today (split+div adjusted)
    marketdata/hourly/{SYM}.csv.gz   hourly OHLCV, last ~730 sessions (Yahoo's 1h limit)
    marketdata/manifest.json         per-symbol row counts / date ranges / errors

Daily bars drive signal generation. Hourly bars exist for one purpose: resolving
intrabar ambiguity (stop-vs-target ordering inside a daily bar) so the backtest
does not have to guess. Yahoo serves ~2 years of 1h data, which defines the
"ground truth window"; older trades fall back to pessimistic/optimistic bounds.

Run:  python -m backtest.fetch_data  (from the repo root)
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from .universe_movers import MOVERS_UNIVERSE

DAILY_START = "2021-01-01"
HOURLY_LOOKBACK_DAYS = 729  # Yahoo rejects 1h requests beyond 730 days

MARKETDATA_DIR = os.path.join(os.path.dirname(__file__), "marketdata")
DAILY_DIR = os.path.join(MARKETDATA_DIR, "daily")
HOURLY_DIR = os.path.join(MARKETDATA_DIR, "hourly")

OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# Market benchmarks (regime slicing) + the live scanner's 11 sector ETFs
# (point-in-time sector-alignment gate).
BENCHMARK_ETFS = ["SPY", "QQQ"]
SECTOR_ETFS = ["XLK", "XLE", "XLC", "XLI", "XLY", "XLF", "XLRE", "XLB", "XLU", "XLV", "XLP"]


def full_universe() -> List[str]:
    seen = set()
    out = []
    for s in list(MOVERS_UNIVERSE) + BENCHMARK_ETFS + SECTOR_ETFS:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _flatten(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns={c: str(c).title() for c in df.columns})
    if any(c not in df.columns for c in OHLCV):
        return None
    out = df[OHLCV].copy()
    for c in OHLCV:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if not out.empty else None


def _yf_download(symbol: str, **kwargs) -> Optional[pd.DataFrame]:
    import yfinance as yf

    for attempt in range(3):
        try:
            raw = yf.download(symbol, progress=False, threads=False,
                              auto_adjust=True, **kwargs)
            out = _flatten(raw)
            if out is not None:
                return out
        except Exception as exc:  # noqa: BLE001 — log and retry
            print(f"    yf attempt {attempt + 1} failed for {symbol}: {exc}", flush=True)
        time.sleep(2 * (attempt + 1))
    return None


def _stooq_daily(symbol: str) -> Optional[pd.DataFrame]:
    """Free full-history daily fallback (no key). Stooq US tickers are lowercase
    with a .us suffix; class shares use dashes the same way yfinance does."""
    import requests

    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200 or not resp.text or resp.text.startswith("No data"):
            return None
        df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"], index_col="Date")
        df = df.rename(columns={c: c.title() for c in df.columns})
        out = _flatten(df)
        if out is None:
            return None
        return out.loc[out.index >= pd.Timestamp(DAILY_START)]
    except Exception as exc:  # noqa: BLE001
        print(f"    stooq failed for {symbol}: {exc}", flush=True)
        return None


def _write_gz(df: pd.DataFrame, path: str, index_label: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        df.to_csv(fh, index_label=index_label, float_format="%.6f")


def fetch_daily(symbol: str) -> Optional[pd.DataFrame]:
    end = (date.today() + timedelta(days=1)).isoformat()
    df = _yf_download(symbol, start=DAILY_START, end=end, interval="1d")
    source = "yfinance"
    if df is None:
        df = _stooq_daily(symbol)
        source = "stooq"
    if df is None:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    df.attrs["source"] = source
    return df


def fetch_hourly(symbol: str) -> Optional[pd.DataFrame]:
    start = (date.today() - timedelta(days=HOURLY_LOOKBACK_DAYS)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    df = _yf_download(symbol, start=start, end=end, interval="1h")
    if df is None:
        return None
    idx = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    df.index = idx
    df.index.name = "Datetime"
    return df


def main() -> None:
    symbols = full_universe()
    manifest = {"generated_utc": pd.Timestamp.utcnow().isoformat(),
                "daily_start": DAILY_START, "symbols": {}}
    print(f"Fetching {len(symbols)} symbols (daily since {DAILY_START} + ~2y hourly)", flush=True)

    for n, sym in enumerate(symbols, 1):
        entry: dict = {}
        daily = fetch_daily(sym)
        if daily is not None:
            _write_gz(daily, os.path.join(DAILY_DIR, f"{sym}.csv.gz"), "Date")
            entry["daily_rows"] = len(daily)
            entry["daily_range"] = [str(daily.index[0].date()), str(daily.index[-1].date())]
            entry["daily_source"] = daily.attrs.get("source", "?")
        else:
            entry["daily_error"] = "no data from yfinance or stooq"

        hourly = fetch_hourly(sym)
        if hourly is not None:
            _write_gz(hourly, os.path.join(HOURLY_DIR, f"{sym}.csv.gz"), "Datetime")
            entry["hourly_rows"] = len(hourly)
            entry["hourly_range"] = [str(hourly.index[0]), str(hourly.index[-1])]
        else:
            entry["hourly_error"] = "no 1h data"

        manifest["symbols"][sym] = entry
        print(f"  [{n}/{len(symbols)}] {sym}: "
              f"daily={entry.get('daily_rows', 'FAIL')} hourly={entry.get('hourly_rows', 'FAIL')}",
              flush=True)
        time.sleep(0.4)  # be polite to Yahoo; the runner has plenty of time

    os.makedirs(MARKETDATA_DIR, exist_ok=True)
    with open(os.path.join(MARKETDATA_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    ok_d = sum(1 for e in manifest["symbols"].values() if "daily_rows" in e)
    ok_h = sum(1 for e in manifest["symbols"].values() if "hourly_rows" in e)
    print(f"\nDone. daily ok: {ok_d}/{len(symbols)}, hourly ok: {ok_h}/{len(symbols)}", flush=True)


if __name__ == "__main__":
    main()

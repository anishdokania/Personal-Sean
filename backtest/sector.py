"""Point-in-time sector leadership for the backtest.

The live scanner ranks 11 sector ETFs by a weighted multi-timeframe momentum
score (``sector_scanner.rank_sectors``) and then scores each stock's alignment
with ``main.score_sector_alignment``. Both are computed *as of today* in live.

For a backtest we must reproduce that ranking **as of each historical bar**,
using only price data up to that bar (no look-ahead). This module:

  * loads daily history for every sector ETF over the backtest window,
  * ranks the sectors as of any given date using the exact live weights, and
  * maps each universe symbol to its sector ETF proxy.

The scoring formula itself is reused from ``main.score_sector_alignment`` so the
backtest and the live gate stay in lock-step -- we only supply the point-in-time
inputs it reads (sector_rank, sector_score, relative_strength_1m/3m).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from sector_scanner import LOOKBACKS, LOOKBACK_WEIGHTS, SECTOR_ETFS

from .data import DailyDataProvider


# Static symbol -> sector ETF map for the liquid backtest universe. Live derives
# this from yfinance sector metadata (``map_symbol_to_sector_etf``); for a fixed,
# well-known universe a static map is point-in-time safe (sector membership is
# stable) and avoids a per-bar metadata lookup. Symbols absent here fall back to
# a neutral alignment score, exactly like unknown sectors do live.
SYMBOL_TO_ETF: Dict[str, str] = {
    # Technology (XLK)
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "MU": "XLK", "QCOM": "XLK", "INTC": "XLK", "TXN": "XLK", "AMAT": "XLK",
    "LRCX": "XLK", "MRVL": "XLK", "ON": "XLK", "SMCI": "XLK", "ARM": "XLK",
    "CRM": "XLK", "ADBE": "XLK", "ORCL": "XLK", "NOW": "XLK", "SNOW": "XLK",
    "PLTR": "XLK", "PANW": "XLK", "DDOG": "XLK",
    # Communication Services (XLC)
    "META": "XLC", "GOOGL": "XLC", "NFLX": "XLC", "DIS": "XLC", "ROKU": "XLC",
    # Consumer Discretionary (XLY)
    "AMZN": "XLY", "TSLA": "XLY", "SHOP": "XLY", "ABNB": "XLY", "HD": "XLY",
    "NKE": "XLY", "SBUX": "XLY", "MCD": "XLY", "TGT": "XLY", "LULU": "XLY",
    "CMG": "XLY", "RIVN": "XLY", "DKNG": "XLY", "CVNA": "XLY",
    # Consumer Staples (XLP)
    "COST": "XLP", "WMT": "XLP",
    # Financials (XLF)
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "WFC": "XLF",
    "C": "XLF", "SCHW": "XLF", "AXP": "XLF", "COF": "XLF", "V": "XLF",
    "COIN": "XLF", "MARA": "XLF", "SQ": "XLF", "PYPL": "XLF", "AFRM": "XLF",
    # Healthcare (XLV)
    "UNH": "XLV", "LLY": "XLV", "JNJ": "XLV", "MRK": "XLV", "PFE": "XLV",
    "ABBV": "XLV", "TMO": "XLV", "ISRG": "XLV", "VRTX": "XLV", "GILD": "XLV",
    # Energy (XLE)
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE",
    # Industrials (XLI)
    "CAT": "XLI", "DE": "XLI", "BA": "XLI", "GE": "XLI", "HON": "XLI",
    "UPS": "XLI", "UBER": "XLI",
}

# ETF -> sector display name (inverse of SECTOR_ETFS), for readable diagnostics.
_ETF_TO_SECTOR: Dict[str, str] = {etf: name for name, etf in SECTOR_ETFS.items()}


def _pct_over_bars(closes: pd.Series, bars: int) -> Optional[float]:
    """Percent return of the last close vs `bars` sessions earlier.

    Matches ``sector_scanner.calculate_performance`` / ``_pct_change_over_bars``:
    ``(latest / prior - 1) * 100``. Returns None without enough history.
    """
    if len(closes) <= bars:
        return None
    latest = closes.iloc[-1]
    prior = closes.iloc[-bars - 1]
    if not np.isfinite(latest) or not np.isfinite(prior) or prior == 0:
        return None
    return float((latest / prior - 1.0) * 100.0)


def _weighted_score(returns: Dict[str, Optional[float]]) -> Optional[float]:
    """Weighted, weight-normalized multi-timeframe score (live formula)."""
    weighted = 0.0
    available = 0.0
    for label, weight in LOOKBACK_WEIGHTS.items():
        val = returns.get(label)
        if val is None or not np.isfinite(val):
            continue
        weighted += val * weight
        available += weight
    if available <= 0:
        return None
    return weighted / available


class SectorRanker:
    """Ranks sector ETFs as of any date, with per-date memoization.

    One instance is shared across the whole backtest so each calendar date's
    ranking is computed at most once regardless of how many symbols query it.
    """

    def __init__(self, provider: DailyDataProvider) -> None:
        self._closes: Dict[str, pd.Series] = {}
        etfs = sorted(set(SECTOR_ETFS.values()))
        frames = provider.get_many(etfs)
        for etf, df in frames.items():
            self._closes[etf] = pd.to_numeric(df["Close"], errors="coerce").dropna()
        self._cache: Dict[pd.Timestamp, Dict[str, Dict[str, Optional[float]]]] = {}

    @property
    def loaded_etfs(self) -> List[str]:
        return sorted(self._closes.keys())

    def _rank_as_of(self, date: pd.Timestamp) -> Dict[str, Dict[str, Optional[float]]]:
        """ETF -> {rank, score, perf_1m, perf_3m} using only bars <= date."""
        if date in self._cache:
            return self._cache[date]

        rows: List[Dict[str, object]] = []
        for etf, closes in self._closes.items():
            window = closes.loc[:date]
            returns = {
                label: _pct_over_bars(window, bars)
                for label, bars in LOOKBACKS.items()
            }
            score = _weighted_score(returns)
            if score is None:
                continue
            rows.append(
                {
                    "etf": etf,
                    "score": score,
                    "1M": returns.get("1M"),
                    "3M": returns.get("3M"),
                    "1W": returns.get("1W"),
                }
            )

        # Same sort as sector_scanner.rank_sectors: Score, then 1M, 3M, 1W desc.
        def sort_key(r: Dict[str, object]):
            def nz(v):
                return v if isinstance(v, (int, float)) and np.isfinite(v) else -1e9
            return (nz(r["score"]), nz(r["1M"]), nz(r["3M"]), nz(r["1W"]))

        rows.sort(key=sort_key, reverse=True)

        out: Dict[str, Dict[str, Optional[float]]] = {}
        for rank, r in enumerate(rows, 1):
            out[str(r["etf"])] = {
                "rank": float(rank),
                "score": float(r["score"]),  # type: ignore[arg-type]
                "perf_1m": r["1M"],
                "perf_3m": r["3M"],
            }
        self._cache[date] = out
        return out

    def alignment_inputs(
        self,
        symbol: str,
        date: pd.Timestamp,
        stock_perf_1m: Optional[float],
        stock_perf_3m: Optional[float],
    ) -> Dict[str, Optional[float]]:
        """Build the row fields ``main.score_sector_alignment`` reads.

        Unknown symbol/sector or an unranked ETF returns all-None, which the
        live scorer treats as a neutral 50 -- matching its behavior for
        incomplete sector metadata.
        """
        etf = SYMBOL_TO_ETF.get(symbol.upper())
        if etf is None:
            return {
                "sector_rank": None, "sector_score": None,
                "relative_strength_1m": None, "relative_strength_3m": None,
                "sector_etf": None,
            }
        ranked = self._rank_as_of(date).get(etf)
        if ranked is None:
            return {
                "sector_rank": None, "sector_score": None,
                "relative_strength_1m": None, "relative_strength_3m": None,
                "sector_etf": etf,
            }
        sec_1m = ranked["perf_1m"]
        sec_3m = ranked["perf_3m"]
        rs_1m = (
            stock_perf_1m - sec_1m
            if stock_perf_1m is not None and sec_1m is not None
            else None
        )
        rs_3m = (
            stock_perf_3m - sec_3m
            if stock_perf_3m is not None and sec_3m is not None
            else None
        )
        return {
            "sector_rank": ranked["rank"],
            "sector_score": ranked["score"],
            "relative_strength_1m": rs_1m,
            "relative_strength_3m": rs_3m,
            "sector_etf": etf,
        }

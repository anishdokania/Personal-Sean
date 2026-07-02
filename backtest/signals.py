"""Point-in-time signal generation.

Given a symbol's full daily history and an as-of bar position `i`, this slices
the data to bars [0..i] (so there is NO look-ahead) and runs the live scanner's
own scoring stack to decide whether bar `i`'s close is a qualifying setup, and
if so what the trigger (entry) and invalidation (stop) levels are.

We import the real modules from the repo root rather than reimplementing the
logic, so the backtest tests exactly what the live system would have flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from technical import analyze_stock_technicals
from today_focus import evaluate_today_focus
from focus_structure import evaluate_focus_structure
from main import score_sector_alignment

from .config import (
    BacktestConfig,
    HARD_GATE_MIN_ATR14,
    HARD_GATE_MIN_AVG_VOLUME,
    HARD_GATE_MIN_DOLLAR_VOLUME,
    HARD_GATE_MIN_PRICE,
    FOCUS_GATE_MIN_BLUEPRINT,
    FOCUS_GATE_MIN_SECTOR,
    FOCUS_GATE_MIN_STRUCTURE,
    FOCUS_GATE_MIN_TODAY,
)
from .sector import SectorRanker


@dataclass
class Signal:
    symbol: str
    date: pd.Timestamp
    close: float
    trigger_level: float       # breakout entry reference
    invalidation_level: float  # protective stop reference
    today_score: float
    structure_score: float
    blueprint_score: float
    setup_type: Optional[str]
    sector_alignment_score: Optional[float] = None
    sector_etf: Optional[str] = None


def _atr14(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 15:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    val = tr.rolling(14).mean().iloc[-1]
    return float(val) if np.isfinite(val) else None


def _passes_hard_gate(window: pd.DataFrame) -> bool:
    """Cheap pre-filter mirroring the live primary gate (minus point-in-time
    market cap, which yfinance can't serve historically -> dollar-volume proxy)."""
    close = float(window["Close"].iloc[-1])
    if close <= HARD_GATE_MIN_PRICE:
        return False
    avg_vol = float(window["Volume"].tail(20).mean())
    if not np.isfinite(avg_vol) or avg_vol <= HARD_GATE_MIN_AVG_VOLUME:
        return False
    if close * avg_vol < HARD_GATE_MIN_DOLLAR_VOLUME:
        return False
    atr = _atr14(window)
    if atr is None or atr <= HARD_GATE_MIN_ATR14:
        return False
    # "Above the moving averages" intent: close above 20-day SMA.
    sma20 = float(window["Close"].tail(20).mean())
    if close <= sma20:
        return False
    return True


def _pct_change_over_bars(closes: pd.Series, bars: int) -> Optional[float]:
    """Percent return vs `bars` sessions ago, matching main._pct_change_over_bars."""
    if len(closes) <= bars:
        return None
    latest = float(closes.iloc[-1])
    prior = float(closes.iloc[-bars - 1])
    if not np.isfinite(latest) or not np.isfinite(prior) or prior == 0:
        return None
    return (latest / prior - 1.0) * 100.0


def evaluate_bar(
    symbol: str,
    df: pd.DataFrame,
    i: int,
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
) -> Optional[Signal]:
    """Return a Signal if bar `i` qualifies as a fresh setup, else None.

    `df` is the full history; only bars [0..i] are used.
    """
    if i + 1 < config.min_bars_for_signal:
        return None

    window = df.iloc[: i + 1]
    if not _passes_hard_gate(window):
        return None

    try:
        technicals = analyze_stock_technicals(symbol, window)
        today = evaluate_today_focus(symbol, technicals)
        structure = evaluate_focus_structure(symbol, window, technicals)
    except Exception:
        return None

    today_score = float(today.get("today_focus_score") or 0)
    structure_score = float(structure.get("focus_structure_score") or 0)
    blueprint_score = float(structure.get("blueprint_fit_score") or 0)

    if (
        today_score < FOCUS_GATE_MIN_TODAY
        or structure_score < FOCUS_GATE_MIN_STRUCTURE
        or blueprint_score < FOCUS_GATE_MIN_BLUEPRINT
    ):
        return None

    # Sector-alignment gate: mirror the live focus gate, which rejects
    # sector_alignment_score < 45 (main.py:991). Point-in-time sector ranking
    # comes from `sector_ranker`; when absent (e.g. single-symbol debugging),
    # skip this layer rather than fail closed.
    sector_alignment_score: Optional[float] = None
    sector_etf: Optional[str] = None
    if sector_ranker is not None:
        closes = window["Close"]
        inputs = sector_ranker.alignment_inputs(
            symbol,
            window.index[-1],
            _pct_change_over_bars(closes, 21),
            _pct_change_over_bars(closes, 63),
        )
        sector_etf = inputs.get("sector_etf")  # type: ignore[assignment]
        sector_alignment_score = score_sector_alignment(inputs)
        if sector_alignment_score < FOCUS_GATE_MIN_SECTOR:
            return None

    diagnostics = structure.get("diagnostics") or {}
    ti = diagnostics.get("trigger_invalidation") or {}
    trigger = ti.get("trigger_level")
    invalidation = ti.get("invalidation_level")
    close = float(window["Close"].iloc[-1])

    if trigger is None or invalidation is None:
        return None
    trigger = float(trigger)
    invalidation = float(invalidation)
    if not (trigger > close > invalidation):
        return None

    # Reject setups whose stop sits too far away (risk per share too large).
    if (close - invalidation) / close > config.max_risk_pct_of_price:
        return None

    return Signal(
        symbol=symbol,
        date=window.index[-1],
        close=close,
        trigger_level=trigger,
        invalidation_level=invalidation,
        today_score=today_score,
        structure_score=structure_score,
        blueprint_score=blueprint_score,
        setup_type=structure.get("blueprint_setup_type"),
        sector_alignment_score=sector_alignment_score,
        sector_etf=sector_etf,
    )

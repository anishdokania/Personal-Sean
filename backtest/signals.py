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
from .adr import adr_pct, adr_abs
from .entry_models import detect_entry


@dataclass
class Signal:
    symbol: str
    date: pd.Timestamp
    close: float
    trigger_level: float       # entry: next-day LIMIT at the reclaimed level
    invalidation_level: float  # stop: just under the undercut low
    today_score: float
    structure_score: float
    blueprint_score: float
    setup_type: Optional[str]
    target_level: Optional[float] = None       # nearest swing high above entry
    sector_alignment_score: Optional[float] = None
    sector_etf: Optional[str] = None


def nearest_swing_high_above(
    window: pd.DataFrame, level: float, k: int, lookback: int
) -> Optional[float]:
    """Nearest confirmed swing-high (pivot high) sitting above `level`.

    A pivot high at bar j is a local max of High over [j-k, j+k]. The nearest
    overhead one is the target (HOD/PHOD/prior swing high style). Returns None
    if there is no clean overhead pivot.
    """
    highs = window["High"].to_numpy()
    m = len(highs)
    if m < 2 * k + 2:
        return None
    start = max(k, m - lookback)
    candidates = []
    for j in range(start, m - k):  # need k confirming bars to the right
        seg = highs[j - k:j + k + 1]
        if highs[j] == seg.max() and highs[j] > level:
            candidates.append(float(highs[j]))
    if not candidates:
        return None
    return min(candidates)  # closest overhead pivot


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


def _ema_stop(window: pd.DataFrame, close: float, config: BacktestConfig) -> Optional[float]:
    """8 EMA-anchored tight stop.

    Anchored just below the 8 EMA and kept tight: the stop uses the recent swing
    low, but clamped into a band around the 8 EMA so it is never deeper than
    `stop_max_adr_below_ema` ADRs below it (and always at least a hair below it).
    Returns None if it can't produce a valid stop below `close`.
    """
    ema8 = float(window["Close"].ewm(span=config.stop_ema_span, adjust=False).mean().iloc[-1])
    adr = adr_abs(window, config.adr_lookback)
    if adr is None:
        return None

    swing_low = float(window["Low"].tail(config.stop_swing_lookback).min())
    tight = ema8 - config.stop_buffer_adr * adr        # tightest: just below 8 EMA
    deepest = ema8 - config.stop_max_adr_below_ema * adr  # deepest allowed below 8 EMA

    # Use the pattern's swing low, clamped into [deepest, tight].
    stop = min(max(swing_low, deepest), tight)
    # Must sit strictly below price with a small ADR buffer.
    stop = min(stop, close - 0.05 * adr)
    if not np.isfinite(stop) or stop <= 0 or stop >= close:
        return None
    return float(stop)


def evaluate_bar(
    symbol: str,
    df: pd.DataFrame,
    i: int,
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
    funnel: Optional[dict] = None,
) -> Optional[Signal]:
    """Return a Signal if bar `i` qualifies as a fresh setup, else None.

    `df` is the full history; only bars [0..i] are used. If `funnel` (a dict of
    stage -> count) is passed, it records where each evaluated bar dropped out,
    for gate drop-off diagnostics.
    """
    def drop(stage: str) -> None:
        if funnel is not None:
            funnel[stage] = funnel.get(stage, 0) + 1

    if i + 1 < config.min_bars_for_signal:
        return None  # not counted: insufficient history, not a real candidate

    drop("evaluated")
    window = df.iloc[: i + 1]
    if not _passes_hard_gate(window):
        drop("fail_hard_gate")
        return None
    drop("pass_hard_gate")

    # ADR% gate: the style only trades high-ADR movers (tight stop + short hold
    # only pays when the stock actually ranges). Point-in-time, no look-ahead.
    adrp = adr_pct(window, config.adr_lookback)
    if adrp is None or adrp < config.min_adr_pct:
        drop("fail_adr")
        return None

    close = float(window["Close"].iloc[-1])
    target_level: Optional[float] = None

    if config.use_native_entry:
        # Sniper UnR entry: next-day LIMIT at the reclaimed level, tiny stop under
        # the undercut low, target = nearest swing high above.
        entry = detect_entry(window, config)
        if entry is None:
            drop("fail_no_entry")
            return None
        adr = adr_abs(window, config.adr_lookback)
        if adr is None:
            drop("fail_no_entry")
            return None

        entry_level = entry.reclaim_level
        stop_level = entry.undercut_low - config.snipe_stop_buffer_adr * adr
        setup_type = entry.entry_type
        today_score = structure_score = blueprint_score = 0.0

        risk = entry_level - stop_level
        if risk <= 0 or not (entry_level < close):
            drop("fail_bad_levels")
            return None

        target_level = nearest_swing_high_above(
            window, entry_level, config.swing_pivot_k, config.swing_lookback
        )
        if target_level is None or (target_level - entry_level) < config.min_rr * risk:
            drop("fail_no_target")
            return None

        trigger = entry_level          # limit entry level
        invalidation = stop_level      # protective stop
    else:
        # Legacy mega-cap blueprint scoring / breakout path.
        try:
            technicals = analyze_stock_technicals(symbol, window)
            today = evaluate_today_focus(symbol, technicals)
            structure = evaluate_focus_structure(symbol, window, technicals)
        except Exception:
            drop("fail_scoring_error")
            return None

        today_score = float(today.get("today_focus_score") or 0)
        structure_score = float(structure.get("focus_structure_score") or 0)
        blueprint_score = float(structure.get("blueprint_fit_score") or 0)

        if today_score < FOCUS_GATE_MIN_TODAY:
            drop("fail_today")
            return None
        if structure_score < FOCUS_GATE_MIN_STRUCTURE:
            drop("fail_structure")
            return None
        if blueprint_score < FOCUS_GATE_MIN_BLUEPRINT:
            drop("fail_blueprint")
            return None

        diagnostics = structure.get("diagnostics") or {}
        ti = diagnostics.get("trigger_invalidation") or {}
        t = ti.get("trigger_level")
        if t is None:
            drop("fail_no_levels")
            return None
        trigger = float(t)
        invalidation = _ema_stop(window, close, config) if config.use_ema_stop else ti.get("invalidation_level")
        setup_type = structure.get("blueprint_setup_type")
        if invalidation is None:
            drop("fail_no_levels")
            return None
        invalidation = float(invalidation)
        if not (trigger > close > invalidation):
            drop("fail_bad_levels")
            return None
        if (close - invalidation) / close > config.max_risk_pct_of_price:
            drop("fail_risk_too_wide")
            return None

    # Sector-alignment gate (both modes): reject sector_alignment_score < 45,
    # mirroring the live focus gate (main.py:991). Point-in-time ranking from
    # `sector_ranker`; when absent (single-symbol debugging) skip rather than
    # fail closed.
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
            drop("fail_sector")
            return None

    drop("signal")
    return Signal(
        symbol=symbol,
        date=window.index[-1],
        close=close,
        trigger_level=trigger,
        invalidation_level=invalidation,
        today_score=today_score,
        structure_score=structure_score,
        blueprint_score=blueprint_score,
        setup_type=setup_type,
        target_level=target_level,
        sector_alignment_score=sector_alignment_score,
        sector_etf=sector_etf,
    )

"""Point-in-time signal generation.

Given a symbol's full daily history and an as-of bar position `i`, this uses
only bars [0..i] (NO look-ahead) to decide whether bar `i`'s close is a
qualifying setup, and if so what the entry (trigger) and stop (invalidation)
levels are.

Indicators are precomputed once per symbol (`precompute`) instead of being
recomputed over a growing window at every bar. This is mathematically identical
for causal indicators (EMA/rolling means read at position `i` use only bars
<= i) and turns the signal pass from O(n^2) into O(n).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

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
    trigger_level: float       # entry: next-day LIMIT at the reclaimed level
    invalidation_level: float  # stop: just under the undercut low
    today_score: float
    structure_score: float
    blueprint_score: float
    setup_type: Optional[str]
    target_level: Optional[float] = None       # nearest swing high above entry
    sector_alignment_score: Optional[float] = None
    sector_etf: Optional[str] = None
    # Slicing metadata (point-in-time at the signal bar)
    adr_pct: Optional[float] = None            # ADR% — how fast the name moves
    rr_planned: Optional[float] = None         # (target - entry) / (entry - stop)
    undercut_low: Optional[float] = None
    chase_adr: Optional[float] = None          # (close - reclaim level) in ADRs


@dataclass
class Precomputed:
    """Causal indicator series for one symbol, aligned to the daily index."""
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    ema8: np.ndarray
    ema21: np.ndarray
    ema50: np.ndarray
    sma20: np.ndarray
    avg_vol20: np.ndarray
    atr14: np.ndarray
    adr_abs: np.ndarray
    adr_pct: np.ndarray


def precompute(df: pd.DataFrame, config: BacktestConfig) -> Precomputed:
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    n = config.adr_lookback
    low_nz = low.replace(0, np.nan)
    return Precomputed(
        opens=df["Open"].to_numpy(float),
        highs=high.to_numpy(float),
        lows=low.to_numpy(float),
        closes=close.to_numpy(float),
        volumes=df["Volume"].to_numpy(float),
        ema8=close.ewm(span=8, adjust=False).mean().to_numpy(float),
        ema21=close.ewm(span=21, adjust=False).mean().to_numpy(float),
        ema50=close.ewm(span=50, adjust=False).mean().to_numpy(float),
        sma20=close.rolling(20).mean().to_numpy(float),
        avg_vol20=df["Volume"].rolling(20).mean().to_numpy(float),
        atr14=tr.rolling(14).mean().to_numpy(float),
        adr_abs=(high - low).rolling(n).mean().to_numpy(float),
        adr_pct=(((high / low_nz).rolling(n).mean()) - 1.0).to_numpy(float) * 100.0,
    )


def nearest_swing_high_above(
    highs: np.ndarray, i: int, level: float, k: int, lookback: int
) -> Optional[float]:
    """Nearest confirmed swing-high (pivot high) above `level`, using bars
    [0..i] only. A pivot at bar j (j <= i - k) is a local max of High over
    [j-k, j+k]; confirmation bars are all <= i so this stays point-in-time."""
    m = i + 1
    if m < 2 * k + 2:
        return None
    start = max(k, m - lookback)
    candidates = []
    for j in range(start, m - k):
        seg = highs[j - k: j + k + 1]
        if highs[j] == seg.max() and highs[j] > level:
            candidates.append(float(highs[j]))
    if not candidates:
        return None
    return min(candidates)


def _pct_change_over_bars(closes: pd.Series, bars: int) -> Optional[float]:
    """Percent return vs `bars` sessions ago, matching main._pct_change_over_bars."""
    if len(closes) <= bars:
        return None
    latest = float(closes.iloc[-1])
    prior = float(closes.iloc[-bars - 1])
    if not np.isfinite(latest) or not np.isfinite(prior) or prior == 0:
        return None
    return (latest / prior - 1.0) * 100.0


def _passes_hard_gate(pre: Precomputed, i: int) -> bool:
    """Live primary gate (minus point-in-time market cap, which no free source
    serves historically -> dollar-volume proxy)."""
    close = pre.closes[i]
    if not np.isfinite(close) or close <= HARD_GATE_MIN_PRICE:
        return False
    avg_vol = pre.avg_vol20[i]
    if not np.isfinite(avg_vol) or avg_vol <= HARD_GATE_MIN_AVG_VOLUME:
        return False
    if close * avg_vol < HARD_GATE_MIN_DOLLAR_VOLUME:
        return False
    atr = pre.atr14[i]
    if not np.isfinite(atr) or atr <= HARD_GATE_MIN_ATR14:
        return False
    sma20 = pre.sma20[i]
    if not np.isfinite(sma20) or close <= sma20:
        return False
    return True


def _detect_unr_entry(pre: Precomputed, i: int) -> Optional[tuple]:
    """The UnR snipe firing on bar `i`: price undercuts a reference (PDL, 8 EMA,
    21 EMA) and reclaims it (closes back above) inside a constructive regime.
    Returns (entry_type, reclaim_level, undercut_low) or None."""
    if i < 54:
        return None
    o, h, l, c = pre.opens[i], pre.highs[i], pre.lows[i], pre.closes[i]
    ema8, ema21, ema50 = pre.ema8[i], pre.ema21[i], pre.ema50[i]
    ema21_prev = pre.ema21[i - 5]
    pdl = pre.lows[i - 1]

    # Constructive long regime: above the 50 EMA with a rising intermediate trend.
    if not (c > ema50 and ema21 >= ema21_prev):
        return None
    # The reclaim bar should close in the upper part of its range (buyers won).
    if not (h > l and (c - l) / (h - l) >= 0.4):
        return None

    # Prefer PDL, then the 8 EMA, then the 21 EMA. The chosen reference must sit
    # below the close so the next-day limit is a real pullback.
    for name, ref in (("pdl", pdl), ("ema8", ema8), ("ema21", ema21)):
        if np.isfinite(ref) and l < ref < c:
            return (f"unr_{name}", float(ref), float(l))
    return None


def evaluate_bar(
    symbol: str,
    df: pd.DataFrame,
    i: int,
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
    funnel: Optional[dict] = None,
    pre: Optional[Precomputed] = None,
) -> Optional[Signal]:
    """Return a Signal if bar `i` qualifies as a fresh setup, else None.

    If `funnel` (a dict of stage -> count) is passed, it records where each
    evaluated bar dropped out, for gate drop-off diagnostics.
    """
    def drop(stage: str) -> None:
        if funnel is not None:
            funnel[stage] = funnel.get(stage, 0) + 1

    if i + 1 < config.min_bars_for_signal:
        return None  # not counted: insufficient history, not a real candidate

    if pre is None:
        pre = precompute(df, config)

    drop("evaluated")
    if not _passes_hard_gate(pre, i):
        drop("fail_hard_gate")
        return None
    drop("pass_hard_gate")

    # ADR% gate: the style only trades high-ADR movers (tight stop + short hold
    # only pays when the stock actually ranges). Point-in-time, no look-ahead.
    adrp = pre.adr_pct[i]
    if not np.isfinite(adrp) or adrp < config.min_adr_pct:
        drop("fail_adr")
        return None

    close = float(pre.closes[i])
    target_level: Optional[float] = None
    rr_planned: Optional[float] = None
    undercut_low: Optional[float] = None

    if config.use_native_entry:
        # Sniper UnR entry: next-day LIMIT at the reclaimed level, tiny stop under
        # the undercut low, target = nearest swing high above.
        entry = _detect_unr_entry(pre, i)
        adr = pre.adr_abs[i]
        if entry is None or not np.isfinite(adr) or adr <= 0:
            drop("fail_no_entry")
            return None
        setup_type, entry_level, undercut_low = entry
        stop_level = undercut_low - config.snipe_stop_buffer_adr * float(adr)
        today_score = structure_score = blueprint_score = 0.0

        risk = entry_level - stop_level
        if risk <= 0 or not (entry_level < close):
            drop("fail_bad_levels")
            return None

        target_level = nearest_swing_high_above(
            pre.highs, i, entry_level, config.swing_pivot_k, config.swing_lookback
        )
        if target_level is None or (target_level - entry_level) < config.min_rr * risk:
            drop("fail_no_target")
            return None
        rr_planned = (target_level - entry_level) / risk

        trigger = entry_level          # limit entry level
        invalidation = stop_level      # protective stop
    else:
        # Legacy mega-cap blueprint scoring / breakout path. Imports are lazy so
        # the fast native path doesn't need the live scanner's dependencies.
        from technical import analyze_stock_technicals
        from today_focus import evaluate_today_focus
        from focus_structure import evaluate_focus_structure

        window = df.iloc[: i + 1]
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
        invalidation = _ema_stop(pre, i, close, config) if config.use_ema_stop else ti.get("invalidation_level")
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
        from main import score_sector_alignment  # lazy: pulls live-scanner deps

        closes = df["Close"].iloc[: i + 1]
        inputs = sector_ranker.alignment_inputs(
            symbol,
            df.index[i],
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
        date=df.index[i],
        close=close,
        trigger_level=float(trigger),
        invalidation_level=float(invalidation),
        today_score=today_score,
        structure_score=structure_score,
        blueprint_score=blueprint_score,
        setup_type=setup_type,
        target_level=target_level,
        sector_alignment_score=sector_alignment_score,
        sector_etf=sector_etf,
        adr_pct=float(adrp) if np.isfinite(adrp) else None,
        rr_planned=rr_planned,
        undercut_low=undercut_low,
        chase_adr=(
            (close - float(trigger)) / float(pre.adr_abs[i])
            if config.use_native_entry and np.isfinite(pre.adr_abs[i]) and pre.adr_abs[i] > 0
            else None
        ),
    )


def _ema_stop(pre: Precomputed, i: int, close: float, config: BacktestConfig) -> Optional[float]:
    """8 EMA-anchored tight stop for the legacy path: recent swing low clamped
    into a band just below the 8 EMA."""
    ema8 = pre.ema8[i]
    adr = pre.adr_abs[i]
    if not np.isfinite(adr) or adr <= 0 or not np.isfinite(ema8):
        return None

    lo = max(0, i - config.stop_swing_lookback + 1)
    swing_low = float(np.min(pre.lows[lo: i + 1]))
    tight = ema8 - config.stop_buffer_adr * adr
    deepest = ema8 - config.stop_max_adr_below_ema * adr

    stop = min(max(swing_low, deepest), tight)
    stop = min(stop, close - 0.05 * adr)
    if not np.isfinite(stop) or stop <= 0 or stop >= close:
        return None
    return float(stop)

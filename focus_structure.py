"""
Focus-list structure evaluation for trading_system.

This module scores whether a chart has the Sean-style focus-list shape:
impulse, controlled digestion, EMA hold/reclaim, compression, nearby trigger
or retest path, nearby invalidation, and limited extension. It is deterministic
decision support only and does not call AI services or place trades.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
STRUCTURE_TYPES = {
    "trendline_compression",
    "high_tight_flag",
    "breakout_retest",
    "ema_reclaim_base",
    "extended_no_base",
    "sloppy_chop",
    "no_clear_structure",
}
BLUEPRINT_SETUP_TYPES = {
    "bullish_power_gap_base",
    "accumulation_base_lows",
    "big_base_highs_breakout",
    "compression_breakout_retest",
    "watchlist_structure",
    "no_blueprint_setup",
}


def _as_float(value: Any) -> Optional[float]:
    """Return a finite float when possible, otherwise None."""
    if isinstance(value, bool):
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numeric_value):
        return None

    return numeric_value


def _format_date(value: Any) -> Optional[str]:
    """Format index values as stable date strings."""
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def _pct_distance(level: Optional[float], current_price: Optional[float]) -> Optional[float]:
    """Return absolute percentage distance from current price to a level."""
    if level is None or current_price is None or current_price <= 0:
        return None
    return abs((level / current_price) - 1) * 100


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return clean OHLCV data with EMAs and helper columns."""
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame.")
    if df.empty:
        raise ValueError("Input OHLCV DataFrame is empty.")

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required OHLCV columns: {', '.join(missing)}")

    prepared = df.copy()
    for column in REQUIRED_OHLCV_COLUMNS:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    prepared = prepared.dropna(subset=REQUIRED_OHLCV_COLUMNS).sort_index()
    if prepared.empty:
        raise ValueError("Input OHLCV DataFrame has no clean numeric rows.")

    prepared["EMA8"] = prepared["Close"].ewm(span=8, adjust=False).mean()
    prepared["EMA21"] = prepared["Close"].ewm(span=21, adjust=False).mean()
    prepared["EMA50"] = prepared["Close"].ewm(span=50, adjust=False).mean()
    prepared["AvgVolume20"] = prepared["Volume"].rolling(window=20, min_periods=5).mean()
    prepared["Range"] = prepared["High"] - prepared["Low"]
    prepared["Body"] = (prepared["Close"] - prepared["Open"]).abs()
    previous_close = prepared["Close"].shift(1)
    prepared["TrueRange"] = pd.concat(
        [
            prepared["High"] - prepared["Low"],
            (prepared["High"] - previous_close).abs(),
            (prepared["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return prepared


def _level_list(levels: Any, current_price: float, side: str) -> List[float]:
    """Return numeric support/resistance levels on one side of current price."""
    matching_levels: List[float] = []

    for level in levels or []:
        numeric_level = _as_float(level)
        if numeric_level is None:
            continue
        if side == "below" and numeric_level < current_price:
            matching_levels.append(numeric_level)
        elif side == "above" and numeric_level > current_price:
            matching_levels.append(numeric_level)

    return sorted(matching_levels, reverse=(side == "below"))


def _zones_below(zones: Any, current_price: float) -> List[Dict[str, Any]]:
    """Return demand/supply zones fully below current price."""
    matching_zones: List[Dict[str, Any]] = []

    for zone in zones or []:
        if not isinstance(zone, dict):
            continue
        low = _as_float(zone.get("low"))
        high = _as_float(zone.get("high"))
        if low is None or high is None:
            continue
        if high < current_price:
            matching_zones.append({"date": zone.get("date"), "low": low, "high": high})

    return sorted(matching_zones, key=lambda item: item["high"], reverse=True)


def _clamp_score(score: float) -> int:
    """Clamp structure score to a 0-100 integer."""
    return int(round(max(0, min(100, score))))


def detect_recent_impulse(df: pd.DataFrame, lookback: int = 30) -> Dict[str, Any]:
    """Detect a meaningful recent impulse move."""
    analyzed = _prepare_df(df)
    recent = analyzed.tail(lookback)

    best_return = None
    best_start = None
    best_end = None
    best_volume_expansion = False

    for window in range(3, 11):
        if len(recent) < window:
            continue
        for end_pos in range(window - 1, len(recent)):
            start_pos = end_pos - window + 1
            start_close = _as_float(recent["Close"].iloc[start_pos])
            end_close = _as_float(recent["Close"].iloc[end_pos])
            if start_close is None or end_close is None or start_close <= 0:
                continue

            impulse_return = ((end_close / start_close) - 1) * 100
            if best_return is None or impulse_return > best_return:
                window_slice = recent.iloc[start_pos : end_pos + 1]
                prior_volume = analyzed.loc[: window_slice.index[0]].tail(20)["Volume"].mean()
                window_volume = window_slice["Volume"].mean()
                best_return = impulse_return
                best_start = window_slice.index[0]
                best_end = window_slice.index[-1]
                best_volume_expansion = (
                    pd.notna(prior_volume)
                    and prior_volume > 0
                    and pd.notna(window_volume)
                    and window_volume > prior_volume * 1.2
                )

    candle_impulse = None
    for date, row in recent.iterrows():
        candle_range = _as_float(row.get("Range"))
        avg_volume = _as_float(row.get("AvgVolume20"))
        if candle_range is None or candle_range <= 0 or avg_volume is None or avg_volume <= 0:
            continue

        body_to_range = float(row["Body"]) / candle_range
        relative_volume = float(row["Volume"]) / avg_volume
        close_location = (float(row["Close"]) - float(row["Low"])) / candle_range
        if (
            row["Close"] > row["Open"]
            and body_to_range >= 0.6
            and close_location >= 0.65
            and relative_volume >= 1.8
        ):
            candle_impulse = {
                "date": date,
                "relative_volume": relative_volume,
                "body_to_range": body_to_range,
            }

    bullish_impulse = best_return is not None and best_return >= 8
    if candle_impulse and (best_return is None or best_return < 8):
        best_return = 0.0 if best_return is None else best_return
        best_start = candle_impulse["date"]
        best_end = candle_impulse["date"]
        best_volume_expansion = True
        bullish_impulse = True

    if bullish_impulse:
        strong = (
            (best_return is not None and best_return >= 15)
            or (
                candle_impulse is not None
                and candle_impulse["relative_volume"] >= 2.2
                and candle_impulse["body_to_range"] >= 0.7
            )
        )
        return {
            "found": True,
            "direction": "bullish",
            "start_date": _format_date(best_start),
            "end_date": _format_date(best_end),
            "impulse_return_pct": best_return,
            "volume_expansion": bool(best_volume_expansion or candle_impulse),
            "quality": "strong" if strong else "acceptable",
        }

    worst_return = None
    worst_start = None
    worst_end = None
    for window in range(3, 11):
        if len(recent) < window:
            continue
        for end_pos in range(window - 1, len(recent)):
            start_pos = end_pos - window + 1
            start_close = _as_float(recent["Close"].iloc[start_pos])
            end_close = _as_float(recent["Close"].iloc[end_pos])
            if start_close is None or end_close is None or start_close <= 0:
                continue
            impulse_return = ((end_close / start_close) - 1) * 100
            if worst_return is None or impulse_return < worst_return:
                worst_return = impulse_return
                worst_start = recent.index[start_pos]
                worst_end = recent.index[end_pos]

    if worst_return is not None and worst_return <= -8:
        return {
            "found": True,
            "direction": "bearish",
            "start_date": _format_date(worst_start),
            "end_date": _format_date(worst_end),
            "impulse_return_pct": worst_return,
            "volume_expansion": False,
            "quality": "weak",
        }

    return {
        "found": False,
        "direction": "none",
        "start_date": None,
        "end_date": None,
        "impulse_return_pct": best_return,
        "volume_expansion": False,
        "quality": "none",
    }


def detect_controlled_digestion(
    df: pd.DataFrame, impulse_info: Dict[str, Any], lookback: int = 15
) -> Dict[str, Any]:
    """Evaluate whether price is digesting a prior impulse in controlled fashion."""
    analyzed = _prepare_df(df)
    if not isinstance(impulse_info, dict) or impulse_info.get("direction") != "bullish":
        return {
            "found": False,
            "pullback_depth_pct": None,
            "holding_ema21": False,
            "volume_quieted": False,
            "bearish_damage": False,
            "quality": "none",
        }

    impulse_end = pd.to_datetime(impulse_info.get("end_date"), errors="coerce")
    if pd.notna(impulse_end):
        digestion = analyzed[analyzed.index >= impulse_end].tail(lookback)
    else:
        digestion = analyzed.tail(lookback)

    if len(digestion) < 3:
        digestion = analyzed.tail(lookback)

    current = digestion.iloc[-1]
    current_price = float(current["Close"])
    recent_high = float(digestion["High"].max())
    pullback_depth = ((recent_high / current_price) - 1) * 100 if current_price > 0 else None
    ema21 = _as_float(current.get("EMA21"))
    holding_ema21 = ema21 is not None and current_price >= ema21 * 0.98

    impulse_volume = None
    if pd.notna(impulse_end):
        impulse_window = analyzed[analyzed.index <= impulse_end].tail(10)
        impulse_volume = _as_float(impulse_window["Volume"].mean())
    digestion_volume = _as_float(digestion.tail(min(10, len(digestion)))["Volume"].mean())
    volume_quieted = (
        impulse_volume is not None
        and digestion_volume is not None
        and digestion_volume < impulse_volume * 0.85
    )

    bearish_candles = digestion[digestion["Close"] < digestion["Open"]].copy()
    bearish_damage = False
    if not bearish_candles.empty:
        bearish_candles["BodyToRange"] = bearish_candles["Body"] / bearish_candles["Range"].replace(0, pd.NA)
        large_bearish = bearish_candles[
            (bearish_candles["BodyToRange"] >= 0.6)
            & (bearish_candles["Close"] < bearish_candles["EMA21"])
        ]
        bearish_damage = len(large_bearish) >= 2

    found = (
        pullback_depth is not None
        and pullback_depth <= 20
        and holding_ema21
        and not bearish_damage
    )

    if not found:
        quality = "failed" if bearish_damage or (pullback_depth is not None and pullback_depth > 20) else "none"
    elif pullback_depth <= 6 and volume_quieted:
        quality = "tight"
    elif pullback_depth <= 15:
        quality = "acceptable"
    else:
        quality = "loose"

    return {
        "found": found,
        "pullback_depth_pct": pullback_depth,
        "holding_ema21": holding_ema21,
        "volume_quieted": volume_quieted,
        "bearish_damage": bearish_damage,
        "quality": quality,
    }


def detect_compression(df: pd.DataFrame, lookback: int = 12) -> Dict[str, Any]:
    """Detect tightening price action using simple range and pivot proxies."""
    analyzed = _prepare_df(df)
    if len(analyzed) < lookback + 10:
        recent = analyzed.tail(min(len(analyzed), lookback))
        previous = analyzed.iloc[:0]
    else:
        recent = analyzed.tail(lookback)
        previous = analyzed.iloc[-(lookback + 10) : -lookback]

    last_five = analyzed.tail(5)
    prior_ten = analyzed.iloc[-15:-5] if len(analyzed) >= 15 else previous
    last_avg_range = _as_float(last_five["TrueRange"].mean())
    prior_avg_range = _as_float(prior_ten["TrueRange"].mean()) if not prior_ten.empty else None

    range_contraction_pct = None
    if last_avg_range is not None and prior_avg_range is not None and prior_avg_range > 0:
        range_contraction_pct = (1 - (last_avg_range / prior_avg_range)) * 100

    recent_highs = recent["High"].tail(6)
    recent_lows = recent["Low"].tail(6)
    lower_highs = bool(len(recent_highs) >= 3 and recent_highs.iloc[-1] <= recent_highs.iloc[0] * 1.01)
    higher_lows = bool(len(recent_lows) >= 3 and recent_lows.iloc[-1] >= recent_lows.min() * 1.01)

    close_volatility_recent = _as_float(recent["Close"].pct_change().tail(5).std())
    close_volatility_prior = _as_float(analyzed["Close"].pct_change().iloc[-15:-5].std()) if len(analyzed) >= 15 else None
    closes_quieter = (
        close_volatility_recent is not None
        and close_volatility_prior is not None
        and close_volatility_recent < close_volatility_prior * 0.9
    )

    found = (
        range_contraction_pct is not None
        and range_contraction_pct >= 15
        and (lower_highs or higher_lows or closes_quieter)
    )

    if found and range_contraction_pct >= 35 and lower_highs and higher_lows:
        quality = "tight"
    elif found and range_contraction_pct >= 15:
        quality = "acceptable"
    elif range_contraction_pct is not None and range_contraction_pct > 0:
        quality = "loose"
    else:
        quality = "none"

    return {
        "found": found,
        "range_contraction_pct": range_contraction_pct,
        "lower_highs": lower_highs,
        "higher_lows": higher_lows,
        "quality": quality,
    }


def detect_volume_dryup(
    df: pd.DataFrame, lookback: int = 10, prior_window: int = 20
) -> Dict[str, Any]:
    """Detect lower volume during consolidation versus prior participation."""
    analyzed = _prepare_df(df)
    recent = analyzed.tail(lookback)
    prior = analyzed.iloc[-(lookback + prior_window) : -lookback]

    recent_avg_volume = _as_float(recent.tail(min(10, len(recent)))["Volume"].mean())
    prior_avg_volume = _as_float(prior["Volume"].mean()) if not prior.empty else None

    dryup_pct = None
    if recent_avg_volume is not None and prior_avg_volume is not None and prior_avg_volume > 0:
        dryup_pct = (1 - (recent_avg_volume / prior_avg_volume)) * 100

    red_days = recent[recent["Close"] < recent["Open"]]
    red_volume_expansion = False
    if not red_days.empty and prior_avg_volume is not None and prior_avg_volume > 0:
        red_volume_expansion = bool(red_days["Volume"].mean() > prior_avg_volume * 1.2)

    found = dryup_pct is not None and dryup_pct >= 15
    if found and dryup_pct >= 30:
        quality = "strong"
    elif found:
        quality = "acceptable"
    elif dryup_pct is not None and dryup_pct > 0:
        quality = "weak"
    else:
        quality = "none"

    return {
        "found": found,
        "recent_avg_volume": recent_avg_volume,
        "prior_avg_volume": prior_avg_volume,
        "dryup_pct": dryup_pct,
        "red_volume_expansion": red_volume_expansion,
        "quality": quality,
    }


def evaluate_ema_structure(df: pd.DataFrame, technicals: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate EMA stack quality and extension risk."""
    analyzed = _prepare_df(df)
    latest = analyzed.iloc[-1]

    current_price = _as_float(latest.get("Close"))
    ema8 = _as_float(latest.get("EMA8"))
    ema21 = _as_float(latest.get("EMA21"))
    ema50 = _as_float(latest.get("EMA50"))

    regime = ""
    if isinstance(technicals, dict):
        ema_regime = technicals.get("ema_regime")
        if isinstance(ema_regime, dict):
            regime = str(ema_regime.get("regime", "")).strip().lower()

    if not regime and None not in {current_price, ema8, ema21, ema50}:
        if current_price > ema8 > ema21 > ema50:
            regime = "strong_bullish"
        elif current_price > ema21 and current_price > ema50:
            regime = "bullish"
        elif current_price < ema8 < ema21 < ema50:
            regime = "strong_bearish"
        elif current_price < ema21 and current_price < ema50:
            regime = "bearish"
        else:
            regime = "mixed"

    pct_above_ema8 = ((current_price / ema8) - 1) * 100 if current_price and ema8 and ema8 > 0 else None
    pct_above_ema21 = ((current_price / ema21) - 1) * 100 if current_price and ema21 and ema21 > 0 else None

    if (pct_above_ema8 is not None and pct_above_ema8 > 8) or (
        pct_above_ema21 is not None and pct_above_ema21 > 12
    ):
        extension_risk = "high"
    elif (pct_above_ema8 is not None and pct_above_ema8 > 5) or (
        pct_above_ema21 is not None and pct_above_ema21 > 8
    ):
        extension_risk = "medium"
    else:
        extension_risk = "low"

    holding = bool(
        current_price is not None
        and ema21 is not None
        and current_price >= ema21 * 0.98
        and regime not in {"bearish", "strong_bearish"}
    )

    if regime == "strong_bullish" and extension_risk == "low":
        quality = "strong"
    elif regime in {"strong_bullish", "bullish"} and extension_risk in {"low", "medium"}:
        quality = "acceptable"
    elif regime == "mixed":
        quality = "mixed"
    else:
        quality = "weak"

    return {
        "holding": holding,
        "regime": regime or "mixed",
        "pct_above_ema8": pct_above_ema8,
        "pct_above_ema21": pct_above_ema21,
        "extension_risk": extension_risk,
        "quality": quality,
    }


def detect_trigger_and_invalidation(
    df: pd.DataFrame, technicals: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Find nearby trigger and invalidation references."""
    analyzed = _prepare_df(df)
    latest = analyzed.iloc[-1]
    current_price = _as_float(latest.get("Close"))
    if current_price is None or current_price <= 0:
        return {
            "trigger_level": None,
            "trigger_distance_pct": None,
            "trigger_nearby": False,
            "invalidation_level": None,
            "invalidation_distance_pct": None,
            "invalidation_nearby": False,
        }

    support_levels: List[float] = []
    resistance_levels: List[float] = []
    demand_zones: List[Dict[str, Any]] = []
    if isinstance(technicals, dict):
        support_resistance = technicals.get("support_resistance")
        if isinstance(support_resistance, dict):
            support_levels = _level_list(support_resistance.get("support_levels"), current_price, "below")
            resistance_levels = _level_list(
                support_resistance.get("resistance_levels"), current_price, "above"
            )

        supply_demand = technicals.get("supply_demand_zones")
        if isinstance(supply_demand, dict):
            demand_zones = _zones_below(supply_demand.get("demand_zones"), current_price)

    recent_high = _as_float(analyzed.tail(12)["High"].max())
    compression_high = _as_float(analyzed.tail(6)["High"].max())
    trigger_candidates = resistance_levels[:]
    for level in [compression_high, recent_high]:
        if level is not None and level > current_price:
            trigger_candidates.append(level)
    trigger_level = min(trigger_candidates) if trigger_candidates else None
    trigger_distance = _pct_distance(trigger_level, current_price)

    invalidation_candidates = support_levels[:]
    ema21 = _as_float(latest.get("EMA21"))
    if ema21 is not None and ema21 < current_price:
        invalidation_candidates.append(ema21)
    if demand_zones:
        invalidation_candidates.append(demand_zones[0]["low"])

    invalidation_level = max(
        [level for level in invalidation_candidates if level is not None and level < current_price],
        default=None,
    )
    invalidation_distance = _pct_distance(invalidation_level, current_price)

    return {
        "trigger_level": trigger_level,
        "trigger_distance_pct": trigger_distance,
        "trigger_nearby": trigger_distance is not None and trigger_distance <= 5,
        "invalidation_level": invalidation_level,
        "invalidation_distance_pct": invalidation_distance,
        "invalidation_nearby": invalidation_distance is not None and invalidation_distance <= 6,
    }


def detect_power_gap_proxy(df: pd.DataFrame, lookback: int = 30) -> Dict[str, Any]:
    """
    Detect a bullish gap-up impulse that can proxy a PEG-style catalyst.

    The blueprint's PEG setup is specifically post-earnings. This project uses
    free OHLCV data only, so this detects the chart behavior and marks the
    earnings catalyst as unverified instead of assuming it.
    """
    analyzed = _prepare_df(df)
    recent = analyzed.tail(lookback).copy()
    if len(recent) < 2:
        return {
            "found": False,
            "date": None,
            "gap_pct": None,
            "relative_volume": None,
            "body_to_range": None,
            "earnings_verified": False,
        }

    for idx in range(len(recent) - 1, 0, -1):
        row = recent.iloc[idx]
        previous = recent.iloc[idx - 1]
        previous_close = _as_float(previous.get("Close"))
        open_price = _as_float(row.get("Open"))
        close_price = _as_float(row.get("Close"))
        candle_range = _as_float(row.get("Range"))
        avg_volume = _as_float(row.get("AvgVolume20"))
        if (
            previous_close is None
            or previous_close <= 0
            or open_price is None
            or close_price is None
            or candle_range is None
            or candle_range <= 0
            or avg_volume is None
            or avg_volume <= 0
        ):
            continue

        gap_pct = ((open_price / previous_close) - 1) * 100
        relative_volume = float(row["Volume"]) / avg_volume
        body_to_range = float(row["Body"]) / candle_range
        if (
            gap_pct >= 3
            and close_price > open_price
            and relative_volume >= 1.5
            and body_to_range >= 0.45
        ):
            return {
                "found": True,
                "date": _format_date(recent.index[idx]),
                "gap_pct": gap_pct,
                "relative_volume": relative_volume,
                "body_to_range": body_to_range,
                "earnings_verified": False,
            }

    return {
        "found": False,
        "date": None,
        "gap_pct": None,
        "relative_volume": None,
        "body_to_range": None,
        "earnings_verified": False,
    }


def classify_blueprint_setup(
    df: pd.DataFrame,
    technicals: Optional[Dict[str, Any]],
    impulse: Dict[str, Any],
    digestion: Dict[str, Any],
    compression: Dict[str, Any],
    volume_dryup: Dict[str, Any],
    ema_structure: Dict[str, Any],
    trigger_invalidation: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify the chart against the daily setup families in the blueprint."""
    analyzed = _prepare_df(df)
    latest = analyzed.iloc[-1]
    current_price = _as_float(latest.get("Close"))
    if current_price is None or current_price <= 0:
        return {
            "setup_type": "no_blueprint_setup",
            "setup_score": 0,
            "setup_match": False,
            "fit_score": 0,
            "fit_pass": False,
            "fit_fail_reasons": ["missing_current_price"],
            "evidence": [],
            "warnings": ["Current price is unavailable."],
            "diagnostics": {},
        }

    recent_120 = analyzed.tail(min(len(analyzed), 120))
    recent_60 = analyzed.tail(min(len(analyzed), 60))
    recent_20 = analyzed.tail(min(len(analyzed), 20))
    long_high = _as_float(recent_120["High"].max())
    long_low = _as_float(recent_120["Low"].min())
    base_high = _as_float(recent_60["High"].max())
    base_low = _as_float(recent_60["Low"].min())
    short_high = _as_float(recent_20["High"].max())
    short_low = _as_float(recent_20["Low"].min())

    range_position = None
    if long_high is not None and long_low is not None and long_high > long_low:
        range_position = (current_price - long_low) / (long_high - long_low)

    base_range_pct = None
    if base_high is not None and base_low is not None and current_price > 0:
        base_range_pct = ((base_high / base_low) - 1) * 100 if base_low > 0 else None

    short_range_pct = None
    if short_high is not None and short_low is not None and current_price > 0:
        short_range_pct = ((short_high / short_low) - 1) * 100 if short_low > 0 else None

    volume_bias = "neutral"
    obv_trend = "flat"
    if isinstance(technicals, dict):
        accumulation_distribution = technicals.get("accumulation_distribution")
        if isinstance(accumulation_distribution, dict):
            volume_bias = str(
                accumulation_distribution.get("volume_bias") or "neutral"
            ).strip().lower()
            obv_trend = str(
                accumulation_distribution.get("obv_trend") or "flat"
            ).strip().lower()

    bullish_volume = bool(volume_bias == "accumulation" or obv_trend == "rising")
    impulse_present = bool(impulse.get("found") and impulse.get("direction") == "bullish")
    controlled_digestion = bool(digestion.get("found"))
    compression_present = bool(compression.get("found"))
    volume_quiet = bool(volume_dryup.get("found") or digestion.get("volume_quieted"))
    holding_ema = bool(ema_structure.get("holding"))
    extension_risk = str(ema_structure.get("extension_risk") or "medium").strip().lower()
    trigger_nearby = bool(trigger_invalidation.get("trigger_nearby"))
    invalidation_nearby = bool(trigger_invalidation.get("invalidation_nearby"))
    near_highs = bool(long_high is not None and current_price >= long_high * 0.90)
    near_lows_or_mid = bool(range_position is not None and range_position <= 0.55)
    tight_recent_base = bool(
        (short_range_pct is not None and short_range_pct <= 18)
        or compression_present
    )
    named_setup_has_base = bool(compression_present or tight_recent_base)
    clean_extension_context = bool(extension_risk != "high" or invalidation_nearby)
    red_volume_expansion = bool(volume_dryup.get("red_volume_expansion"))
    extension_warnings = (
        ["Extension risk is high; Blueprint setup is capped until a cleaner base or retest forms."]
        if extension_risk == "high"
        else []
    )
    power_gap = detect_power_gap_proxy(analyzed)

    candidates: List[Dict[str, Any]] = []

    def add_candidate(
        setup_type: str,
        score: float,
        evidence: List[str],
        warnings: Optional[List[str]] = None,
    ) -> None:
        candidates.append(
            {
                "setup_type": setup_type,
                "setup_score": _clamp_score(score),
                "evidence": evidence,
                "warnings": warnings or [],
            }
        )

    peg_score = 0.0
    peg_evidence: List[str] = []
    peg_warnings = ["Earnings catalyst is not verified from OHLCV data."]
    if power_gap.get("found"):
        peg_score += 35
        peg_evidence.append(
            f"Bullish gap-up proxy on {power_gap.get('date')} with elevated volume."
        )
    if controlled_digestion:
        peg_score += 20
        peg_evidence.append("Controlled digestion after the gap/impulse is present.")
    if compression_present or tight_recent_base:
        peg_score += 15
        peg_evidence.append("A tight consolidation/base is present after the impulse.")
    if bullish_volume or volume_quiet:
        peg_score += 15
        peg_evidence.append("Volume behavior supports the base.")
    if holding_ema:
        peg_score += 10
        peg_evidence.append("Price is holding the 8/21/50 EMA structure.")
    if trigger_nearby or invalidation_nearby:
        peg_score += 10
        peg_evidence.append("A trigger or invalidation reference is nearby.")
    if peg_score >= 55 and named_setup_has_base and clean_extension_context:
        add_candidate(
            "bullish_power_gap_base",
            peg_score,
            peg_evidence,
            peg_warnings + extension_warnings,
        )

    accumulation_score = 0.0
    accumulation_evidence: List[str] = []
    if near_lows_or_mid:
        accumulation_score += 25
        accumulation_evidence.append("Price is basing in the lower/middle part of its recent range.")
    if base_range_pct is not None and base_range_pct <= 35:
        accumulation_score += 15
        accumulation_evidence.append("The larger base is contained rather than loose.")
    if bullish_volume:
        accumulation_score += 25
        accumulation_evidence.append("Volume/OBV behavior leans accumulation.")
    if compression_present or volume_quiet:
        accumulation_score += 15
        accumulation_evidence.append("Compression or volume dry-up is present in the base.")
    if trigger_nearby or invalidation_nearby:
        accumulation_score += 15
        accumulation_evidence.append("A reclaim/retest or invalidation path is nearby.")
    if holding_ema:
        accumulation_score += 10
        accumulation_evidence.append("EMA structure is reclaiming or holding.")
    if (
        accumulation_score >= 55
        and (named_setup_has_base or (base_range_pct is not None and base_range_pct <= 35 and bullish_volume))
        and clean_extension_context
    ):
        add_candidate(
            "accumulation_base_lows",
            accumulation_score,
            accumulation_evidence,
            extension_warnings,
        )

    high_base_score = 0.0
    high_base_evidence: List[str] = []
    if near_highs:
        high_base_score += 25
        high_base_evidence.append("Price is consolidating near recent highs.")
    if impulse_present:
        high_base_score += 15
        high_base_evidence.append("A prior bullish impulse is present.")
    if controlled_digestion:
        high_base_score += 15
        high_base_evidence.append("The pullback after impulse is controlled.")
    if compression_present or tight_recent_base:
        high_base_score += 20
        high_base_evidence.append("The recent range is compressing or flagging.")
    if volume_quiet or bullish_volume:
        high_base_score += 15
        high_base_evidence.append("Volume supports continuation rather than distribution.")
    if holding_ema:
        high_base_score += 10
        high_base_evidence.append("Price is holding the EMA structure.")
    if trigger_nearby:
        high_base_score += 10
        high_base_evidence.append("A breakout trigger is nearby.")
    if high_base_score >= 55 and named_setup_has_base and clean_extension_context:
        add_candidate(
            "big_base_highs_breakout",
            high_base_score,
            high_base_evidence,
            extension_warnings,
        )

    compression_score = 0.0
    compression_evidence: List[str] = []
    if compression_present:
        compression_score += 30
        compression_evidence.append("Compression is detected.")
    if controlled_digestion:
        compression_score += 20
        compression_evidence.append("Digestion after impulse is controlled.")
    if volume_quiet:
        compression_score += 15
        compression_evidence.append("Volume is quieter during the setup.")
    if trigger_nearby:
        compression_score += 15
        compression_evidence.append("A breakout trigger is nearby.")
    if invalidation_nearby:
        compression_score += 15
        compression_evidence.append("A nearby invalidation/retest level exists.")
    if holding_ema:
        compression_score += 10
        compression_evidence.append("EMA structure supports the setup.")
    if compression_score >= 55 and clean_extension_context:
        add_candidate(
            "compression_breakout_retest",
            compression_score,
            compression_evidence,
            extension_warnings,
        )

    if not candidates:
        watch_score = 0.0
        watch_evidence: List[str] = []
        if impulse_present:
            watch_score += 20
            watch_evidence.append("Bullish impulse is present.")
        if holding_ema:
            watch_score += 20
            watch_evidence.append("EMA structure is constructive.")
        if trigger_nearby or invalidation_nearby:
            watch_score += 15
            watch_evidence.append("A trade reference level exists.")
        if bullish_volume:
            watch_score += 15
            watch_evidence.append("Volume is not hostile.")
        if extension_risk == "high":
            watch_score = min(watch_score, 45 if named_setup_has_base else 35)
            watch_evidence.append("High extension caps this as a lower-priority watch until a cleaner base forms.")
        if watch_score >= 45:
            add_candidate("watchlist_structure", watch_score, watch_evidence, extension_warnings)

    if not candidates:
        fit = evaluate_blueprint_fit(
            setup_type="no_blueprint_setup",
            setup_score=0,
            impulse_present=impulse_present,
            controlled_digestion=controlled_digestion,
            compression_present=compression_present,
            tight_recent_base=tight_recent_base,
            volume_quiet=volume_quiet,
            bullish_volume=bullish_volume,
            red_volume_expansion=red_volume_expansion,
            holding_ema=holding_ema,
            trigger_nearby=trigger_nearby,
            invalidation_nearby=invalidation_nearby,
            extension_risk=extension_risk,
            near_highs=near_highs,
            near_lows_or_mid=near_lows_or_mid,
        )
        return {
            "setup_type": "no_blueprint_setup",
            "setup_score": 0,
            "setup_match": False,
            "fit_score": fit["fit_score"],
            "fit_pass": fit["fit_pass"],
            "fit_fail_reasons": fit["fit_fail_reasons"],
            "evidence": [],
            "warnings": ["No named Blueprint daily setup was detected."] + extension_warnings,
            "diagnostics": {
                "range_position": range_position,
                "base_range_pct": base_range_pct,
                "short_range_pct": short_range_pct,
                "extension_risk": extension_risk,
                "power_gap": power_gap,
                "blueprint_fit": fit,
            },
        }

    best = sorted(candidates, key=lambda item: item["setup_score"], reverse=True)[0]
    setup_type = str(best["setup_type"])
    setup_score = int(best["setup_score"])
    fit = evaluate_blueprint_fit(
        setup_type=setup_type,
        setup_score=setup_score,
        impulse_present=impulse_present,
        controlled_digestion=controlled_digestion,
        compression_present=compression_present,
        tight_recent_base=tight_recent_base,
        volume_quiet=volume_quiet,
        bullish_volume=bullish_volume,
        red_volume_expansion=red_volume_expansion,
        holding_ema=holding_ema,
        trigger_nearby=trigger_nearby,
        invalidation_nearby=invalidation_nearby,
        extension_risk=extension_risk,
        near_highs=near_highs,
        near_lows_or_mid=near_lows_or_mid,
    )
    return {
        "setup_type": setup_type,
        "setup_score": setup_score,
        "setup_match": setup_type != "watchlist_structure" and setup_score >= 60,
        "fit_score": fit["fit_score"],
        "fit_pass": fit["fit_pass"],
        "fit_fail_reasons": fit["fit_fail_reasons"],
        "evidence": best["evidence"][:5],
        "warnings": best["warnings"][:3],
        "diagnostics": {
            "range_position": range_position,
            "base_range_pct": base_range_pct,
            "short_range_pct": short_range_pct,
            "near_highs": near_highs,
            "near_lows_or_mid": near_lows_or_mid,
            "tight_recent_base": tight_recent_base,
            "extension_risk": extension_risk,
            "volume_bias": volume_bias,
            "obv_trend": obv_trend,
            "power_gap": power_gap,
            "red_volume_expansion": red_volume_expansion,
            "blueprint_fit": fit,
            "all_candidates": candidates,
        },
    }


def evaluate_blueprint_fit(
    *,
    setup_type: str,
    setup_score: int,
    impulse_present: bool,
    controlled_digestion: bool,
    compression_present: bool,
    tight_recent_base: bool,
    volume_quiet: bool,
    bullish_volume: bool,
    red_volume_expansion: bool,
    holding_ema: bool,
    trigger_nearby: bool,
    invalidation_nearby: bool,
    extension_risk: str,
    near_highs: bool,
    near_lows_or_mid: bool,
) -> Dict[str, Any]:
    """
    Score how closely the chart matches the blueprint examples.

    The named setup score can be constructive while still missing a practical
    focus-list ingredient. This fit layer makes those misses explicit.
    """
    setup_type = str(setup_type or "no_blueprint_setup")
    extension_risk = str(extension_risk or "medium").strip().lower()
    fail_reasons: List[str] = []
    score = 0.0

    named_setup = setup_type not in {"no_blueprint_setup", "watchlist_structure"}
    if named_setup:
        score += 15
        if setup_score >= 70:
            score += 10
        elif setup_score >= 60:
            score += 5
    else:
        fail_reasons.append("missing_named_blueprint_setup")

    has_base_location = bool(near_highs or near_lows_or_mid)
    if impulse_present:
        score += 10
    elif setup_type != "accumulation_base_lows":
        fail_reasons.append("missing_recent_bullish_impulse")

    if has_base_location:
        score += 5
    else:
        fail_reasons.append("not_near_highs_or_base_lows")

    if controlled_digestion:
        score += 15
    elif setup_type in {
        "bullish_power_gap_base",
        "big_base_highs_breakout",
        "compression_breakout_retest",
    }:
        fail_reasons.append("missing_controlled_digestion")

    if compression_present:
        score += 15
    elif tight_recent_base:
        score += 8
    else:
        fail_reasons.append("missing_compression_or_tight_base")

    if bullish_volume:
        score += 15
    if volume_quiet:
        score += 10
    if not bullish_volume and not volume_quiet:
        fail_reasons.append("missing_bullish_volume_or_dryup")
    if red_volume_expansion:
        score -= 15
        fail_reasons.append("red_volume_expansion")

    if holding_ema:
        score += 10
    else:
        fail_reasons.append("not_holding_ema_structure")

    if trigger_nearby:
        score += 10
    if invalidation_nearby:
        score += 10
    if not trigger_nearby and not invalidation_nearby:
        fail_reasons.append("missing_trigger_or_retest_reference")
    if not invalidation_nearby:
        fail_reasons.append("missing_nearby_invalidation")

    if extension_risk == "high":
        if controlled_digestion and (compression_present or tight_recent_base):
            score -= 5
        else:
            score -= 20
            fail_reasons.append("high_extension_without_clean_base")
    elif extension_risk == "medium":
        score -= 5

    hard_fail_reasons = {
        "missing_named_blueprint_setup",
        "missing_compression_or_tight_base",
        "missing_bullish_volume_or_dryup",
        "red_volume_expansion",
        "not_holding_ema_structure",
        "missing_trigger_or_retest_reference",
        "high_extension_without_clean_base",
    }
    hard_fail_present = any(reason in hard_fail_reasons for reason in fail_reasons)
    fit_score = _clamp_score(score)
    if hard_fail_present:
        fit_score = min(fit_score, 64)
    fit_pass = bool(
        fit_score >= 65
        and not hard_fail_present
    )

    return {
        "fit_score": fit_score,
        "fit_pass": fit_pass,
        "fit_fail_reasons": fail_reasons,
    }


def classify_structure_type(
    score_before_caps: int,
    impulse: Dict[str, Any],
    digestion: Dict[str, Any],
    compression: Dict[str, Any],
    ema_structure: Dict[str, Any],
    trigger_invalidation: Dict[str, Any],
    sloppy_structure: bool,
) -> Dict[str, str]:
    """Classify structure from the same evidence used to build the score."""
    impulse_present = bool(impulse.get("found") and impulse.get("direction") == "bullish")
    controlled_digestion = bool(digestion.get("found"))
    compression_present = bool(compression.get("found"))
    trigger_nearby = bool(trigger_invalidation.get("trigger_nearby"))
    invalidation_nearby = bool(trigger_invalidation.get("invalidation_nearby"))
    holding_ema_structure = bool(ema_structure.get("holding"))
    extension_risk = str(ema_structure.get("extension_risk", "medium")).strip().lower()
    ema_regime = str(ema_structure.get("regime", "mixed")).strip().lower()

    pullback_depth_pct = _as_float(digestion.get("pullback_depth_pct"))
    range_contraction_pct = _as_float(compression.get("range_contraction_pct"))
    lower_highs = bool(compression.get("lower_highs"))
    higher_lows = bool(compression.get("higher_lows"))
    price_near_highs = pullback_depth_pct is not None and pullback_depth_pct <= 8
    modest_pullback = pullback_depth_pct is not None and pullback_depth_pct <= 12
    has_tightening_proxy = bool(
        compression_present
        or lower_highs
        or (
            range_contraction_pct is not None
            and range_contraction_pct > 0
        )
    )
    reclaim_or_base_evidence = bool(
        holding_ema_structure
        and (controlled_digestion or invalidation_nearby)
        and extension_risk != "high"
    )

    if extension_risk == "high" and not controlled_digestion:
        return {
            "structure_type": "extended_no_base",
            "classification_reason": (
                "High extension risk without controlled digestion classified as "
                "extended_no_base."
            ),
        }

    if ema_regime in {"bearish", "strong_bearish"} and not reclaim_or_base_evidence:
        structure_type = "sloppy_chop" if sloppy_structure else "no_clear_structure"
        return {
            "structure_type": structure_type,
            "classification_reason": (
                f"EMA regime is {ema_regime} with no reclaim/base evidence; "
                f"classified as {structure_type}."
            ),
        }

    if sloppy_structure:
        return {
            "structure_type": "sloppy_chop",
            "classification_reason": (
                "Digestion/compression evidence is loose or choppy; classified as "
                "sloppy_chop."
            ),
        }

    if (
        score_before_caps >= 80
        and impulse_present
        and controlled_digestion
        and compression_present
        and (trigger_nearby or invalidation_nearby)
    ):
        if trigger_nearby and (
            lower_highs
            or range_contraction_pct is None
            or range_contraction_pct >= 10
        ):
            structure_type = "trendline_compression"
        elif price_near_highs:
            structure_type = "high_tight_flag"
        elif invalidation_nearby:
            structure_type = "breakout_retest"
        else:
            structure_type = "ema_reclaim_base"
        return {
            "structure_type": structure_type,
            "classification_reason": (
                "High score with impulse, controlled digestion, compression, and "
                f"nearby {'trigger' if trigger_nearby else 'invalidation'} classified "
                f"as {structure_type}."
            ),
        }

    if (
        score_before_caps >= 75
        and impulse_present
        and controlled_digestion
        and trigger_nearby
        and holding_ema_structure
    ):
        if has_tightening_proxy:
            structure_type = "trendline_compression"
        elif price_near_highs or modest_pullback:
            structure_type = "high_tight_flag"
        elif invalidation_nearby:
            structure_type = "breakout_retest"
        else:
            structure_type = "ema_reclaim_base"
        return {
            "structure_type": structure_type,
            "classification_reason": (
                "Strong impulse/digestion setup with nearby trigger and EMA hold "
                f"classified as {structure_type}."
            ),
        }

    if (
        impulse_present
        and controlled_digestion
        and holding_ema_structure
        and (trigger_nearby or invalidation_nearby)
        and extension_risk in {"low", "medium"}
    ):
        if has_tightening_proxy and trigger_nearby:
            structure_type = "trendline_compression"
        elif price_near_highs or modest_pullback:
            structure_type = "high_tight_flag"
        elif invalidation_nearby:
            structure_type = "breakout_retest"
        else:
            structure_type = "ema_reclaim_base"
        return {
            "structure_type": structure_type,
            "classification_reason": (
                "Impulse, controlled digestion, EMA hold, and nearby risk/trigger "
                f"path classified as {structure_type} despite imperfect compression."
            ),
        }

    if (
        impulse_present
        and extension_risk == "high"
        and not compression_present
    ):
        return {
            "structure_type": "extended_no_base",
            "classification_reason": (
                "Bullish impulse is present, but high extension and no compression "
                "make it extended_no_base."
            ),
        }

    if (
        invalidation_nearby
        and holding_ema_structure
        and extension_risk != "high"
    ):
        return {
            "structure_type": "breakout_retest",
            "classification_reason": (
                "Price is holding EMA structure near a clear invalidation/retest "
                "area; classified as breakout_retest."
            ),
        }

    if (
        ema_regime in {"bullish", "mixed"}
        and holding_ema_structure
        and invalidation_nearby
    ):
        return {
            "structure_type": "ema_reclaim_base",
            "classification_reason": (
                "EMA reclaim/base evidence is present, but impulse/compression "
                "evidence is still incomplete; classified as ema_reclaim_base."
            ),
        }

    return {
        "structure_type": "no_clear_structure",
        "classification_reason": (
            "Evidence did not satisfy a valid focus-structure pattern; classified "
            "as no_clear_structure."
        ),
    }


def apply_structure_score_caps(
    score: float,
    structure_type: str,
    digestion: Dict[str, Any],
    compression: Dict[str, Any],
    ema_structure: Dict[str, Any],
    trigger_invalidation: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply score caps that keep disqualifying labels below gate thresholds."""
    capped_score = score
    capped_structure_type = structure_type
    cap_reasons: List[str] = []
    extension_risk = str(ema_structure.get("extension_risk", "medium")).strip().lower()
    ema_regime = str(ema_structure.get("regime", "mixed")).strip().lower()

    def cap_at(limit: int, reason: str) -> None:
        nonlocal capped_score
        if capped_score > limit:
            capped_score = limit
            cap_reasons.append(reason)

    if extension_risk == "high" and not digestion.get("found"):
        capped_structure_type = "extended_no_base"
        cap_at(59, "high_extension_without_controlled_digestion")

    if (
        not compression.get("found")
        and not trigger_invalidation.get("trigger_nearby")
        and not digestion.get("found")
    ):
        cap_at(49, "no_compression_trigger_or_digestion")

    if (
        not trigger_invalidation.get("invalidation_nearby")
        and not trigger_invalidation.get("trigger_nearby")
    ):
        cap_at(59, "no_nearby_trigger_or_invalidation")

    if (
        ema_regime in {"bearish", "strong_bearish"}
        and capped_structure_type != "ema_reclaim_base"
    ):
        cap_at(39, f"{ema_regime}_ema_regime_without_reclaim")

    if capped_structure_type == "extended_no_base":
        cap_at(59, "extended_no_base_score_cap")
    elif capped_structure_type == "sloppy_chop":
        cap_at(49, "sloppy_chop_score_cap")
    elif capped_structure_type == "no_clear_structure":
        cap_at(64, "no_clear_structure_score_cap")

    return {
        "score": capped_score,
        "structure_type": capped_structure_type,
        "cap_reasons": cap_reasons,
    }


def evaluate_focus_structure(
    symbol: str, df: pd.DataFrame, technicals: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Score Sean-style focus-list structure quality."""
    symbol_clean = str(symbol).strip().upper()
    impulse = detect_recent_impulse(df)
    digestion = detect_controlled_digestion(df, impulse)
    compression = detect_compression(df)
    volume_dryup = detect_volume_dryup(df)
    ema_structure = evaluate_ema_structure(df, technicals)
    trigger_invalidation = detect_trigger_and_invalidation(df, technicals)
    blueprint_setup = classify_blueprint_setup(
        df,
        technicals,
        impulse,
        digestion,
        compression,
        volume_dryup,
        ema_structure,
        trigger_invalidation,
    )

    score = 0.0
    reasons: List[str] = []
    warnings: List[str] = []
    disqualifiers: List[str] = []

    if impulse.get("found") and impulse.get("direction") == "bullish":
        score += 15
        reasons.append("Recent bullish impulse is present.")
        if impulse.get("quality") == "strong":
            score += 10
            reasons.append("Impulse quality is strong.")
    elif impulse.get("direction") == "bearish":
        disqualifiers.append("Recent bearish impulse works against long focus structure.")
    else:
        warnings.append("No clear recent bullish impulse detected.")

    if digestion.get("found"):
        score += 20
        reasons.append("Controlled digestion after impulse is present.")
        if digestion.get("quality") in {"tight", "acceptable"}:
            score += 5
            reasons.append(f"Digestion quality is {digestion.get('quality')}.")
    else:
        score -= 20
        warnings.append("No controlled digestion after impulse was detected.")

    if compression.get("found"):
        score += 20
        reasons.append("Price compression is present.")
        if compression.get("quality") == "tight":
            score += 5
            reasons.append("Compression quality is tight.")
    else:
        score -= 15
        warnings.append("No clear compression/base detected.")

    if volume_dryup.get("found"):
        score += 15
        reasons.append("Volume has dried up during consolidation.")
    elif volume_dryup.get("quality") == "weak":
        warnings.append("Volume is only slightly quieter during the recent structure.")
    else:
        warnings.append("Volume dry-up was not detected.")

    if volume_dryup.get("red_volume_expansion"):
        score -= 15
        warnings.append("Red-candle volume is expanding during the pullback.")

    if ema_structure.get("quality") in {"strong", "acceptable"}:
        score += 15
        reasons.append(f"EMA structure is {ema_structure.get('quality')}.")
    elif ema_structure.get("quality") == "mixed":
        warnings.append("EMA structure is mixed.")
    else:
        warnings.append("EMA structure is weak.")

    extension_risk = ema_structure.get("extension_risk", "medium")
    if extension_risk == "high":
        score -= 15
        warnings.append("Extension risk is high.")
    elif extension_risk == "medium":
        score -= 5
        warnings.append("Extension risk is medium.")

    if trigger_invalidation.get("trigger_nearby"):
        score += 10
        reasons.append("A nearby trigger level exists.")
    if trigger_invalidation.get("invalidation_nearby"):
        score += 10
        reasons.append("A nearby invalidation level exists.")

    if not trigger_invalidation.get("trigger_nearby") and not digestion.get("found"):
        score -= 20
        warnings.append("No nearby trigger or retest path is available.")
    if not trigger_invalidation.get("invalidation_nearby"):
        score -= 10
        warnings.append("Invalidation is too far for clean same-day risk.")

    sloppy_structure = (
        ema_structure.get("regime") == "mixed"
        and not impulse.get("found")
        and not compression.get("found")
    ) or (
        not compression.get("found")
        and digestion.get("quality") in {"failed", "loose"}
    )
    if sloppy_structure:
        score -= 20
        warnings.append("Structure appears sloppy or choppy.")

    score_before_caps = _clamp_score(score)
    classification = classify_structure_type(
        score_before_caps,
        impulse,
        digestion,
        compression,
        ema_structure,
        trigger_invalidation,
        sloppy_structure,
    )
    structure_type = classification["structure_type"]
    classification_reason = classification["classification_reason"]

    caps = apply_structure_score_caps(
        score,
        structure_type,
        digestion,
        compression,
        ema_structure,
        trigger_invalidation,
    )
    structure_type = caps["structure_type"]
    cap_reasons = caps["cap_reasons"]
    focus_structure_score = _clamp_score(caps["score"])
    if cap_reasons:
        classification_reason = (
            f"{classification_reason} Score capped for: {', '.join(cap_reasons)}."
        )

    if structure_type in {"extended_no_base", "sloppy_chop", "no_clear_structure"}:
        disqualifiers.append(f"Structure type is {structure_type}.")

    if focus_structure_score >= 75:
        verdict = "Structure is clean enough to support focus-list review."
    elif focus_structure_score >= 65:
        verdict = "Structure is constructive but still needs confirmation."
    elif structure_type == "extended_no_base":
        verdict = "Strong move is present, but digestion/compression is insufficient; avoid chasing."
    elif structure_type == "sloppy_chop":
        verdict = "Structure is too choppy for a clean focus-list setup."
    else:
        verdict = "No clean Sean-style focus-list structure is present yet."

    diagnostics = {
        "impulse": impulse,
        "controlled_digestion": digestion,
        "compression": compression,
        "volume_dryup": volume_dryup,
        "ema_structure": ema_structure,
        "trigger_invalidation": trigger_invalidation,
        "blueprint_setup": blueprint_setup,
        "sloppy_structure": sloppy_structure,
        "score_before_caps": score_before_caps,
        "score_after_caps": focus_structure_score,
        "score_cap_reasons": cap_reasons,
        "classification_reason": classification_reason,
    }

    return {
        "symbol": symbol_clean,
        "focus_structure_score": focus_structure_score,
        "score_before_caps": score_before_caps,
        "score_after_caps": focus_structure_score,
        "structure_type": structure_type,
        "classification_reason": classification_reason,
        "blueprint_setup_type": blueprint_setup.get("setup_type"),
        "blueprint_setup_score": blueprint_setup.get("setup_score"),
        "blueprint_setup_match": blueprint_setup.get("setup_match"),
        "blueprint_fit_score": blueprint_setup.get("fit_score"),
        "blueprint_fit_pass": blueprint_setup.get("fit_pass"),
        "blueprint_fit_fail_reasons": blueprint_setup.get("fit_fail_reasons"),
        "blueprint_setup_evidence": blueprint_setup.get("evidence"),
        "blueprint_setup_warnings": blueprint_setup.get("warnings"),
        "impulse_present": bool(impulse.get("found") and impulse.get("direction") == "bullish"),
        "controlled_digestion": bool(digestion.get("found")),
        "compression_present": bool(compression.get("found")),
        "volume_dryup": bool(volume_dryup.get("found")),
        "holding_ema_structure": bool(ema_structure.get("holding")),
        "trigger_nearby": bool(trigger_invalidation.get("trigger_nearby")),
        "invalidation_nearby": bool(trigger_invalidation.get("invalidation_nearby")),
        "extension_risk": extension_risk,
        "structure_verdict": verdict,
        "reasons": reasons,
        "warnings": warnings,
        "disqualifiers": disqualifiers,
        "diagnostics": diagnostics,
    }


def batch_evaluate_focus_structure(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Evaluate focus structure for a batch of precomputed items."""
    results: List[Dict[str, Any]] = []

    for item in items or []:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("Symbol")
        df = item.get("df")
        if not isinstance(df, pd.DataFrame):
            df = item.get("ohlcv")
        technicals = item.get("technicals")
        if not symbol or not isinstance(df, pd.DataFrame):
            continue
        result = evaluate_focus_structure(str(symbol), df, technicals)
        if "candidate" in item:
            result["candidate"] = item["candidate"]
        results.append(result)

    return results


if __name__ == "__main__":
    from data_fetcher import fetch_stock_data
    from technical import analyze_stock_technicals

    symbols = ["PLTR", "MSFT", "HPQ", "F", "ORCL", "EMR"]
    outputs = []

    for ticker in symbols:
        try:
            stock_data = fetch_stock_data(ticker, period="6mo", interval="1d")
            technical_data = analyze_stock_technicals(ticker, stock_data)
            outputs.append(evaluate_focus_structure(ticker, stock_data, technical_data))
        except Exception as exc:
            outputs.append(
                {
                    "symbol": ticker,
                    "focus_structure_score": 0,
                    "score_before_caps": 0,
                    "score_after_caps": 0,
                    "structure_type": "no_clear_structure",
                    "classification_reason": f"Evaluation failed before classification: {exc}",
                    "impulse_present": False,
                    "controlled_digestion": False,
                    "compression_present": False,
                    "volume_dryup": False,
                    "holding_ema_structure": False,
                    "trigger_nearby": False,
                    "invalidation_nearby": False,
                    "extension_risk": "high",
                    "structure_verdict": f"Evaluation failed: {exc}",
                    "reasons": [],
                    "warnings": [],
                    "disqualifiers": [str(exc)],
                    "diagnostics": {},
                }
            )

    print(json.dumps(outputs, indent=2, default=str))

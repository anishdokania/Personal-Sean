"""
Same-day focus-list evaluation for trading_system.

This module converts Module 4 technical dictionaries into strict same-day
actionability judgments. It is decision support only and does not call AI
services, fetch universe data, place orders, or auto-trade.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


ACTIONABILITY_VALUES = {
    "ready_today",
    "breakout_only",
    "pullback_only",
    "needs_more_time",
    "avoid",
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


def _pct_distance(reference: Optional[float], current_price: Optional[float]) -> Optional[float]:
    """Return absolute percent distance between reference and current price."""
    if reference is None or current_price is None or current_price <= 0:
        return None

    return abs((reference / current_price) - 1) * 100


def _level_token(level: Optional[float]) -> Optional[str]:
    """Format numeric levels for human-readable report text."""
    if level is None:
        return None

    return f"{level:.2f}"


def _levels_on_side(levels: Any, current_price: float, side: str) -> List[float]:
    """Return numeric levels below or above current price."""
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


def _zones_on_side(zones: Any, current_price: float, side: str) -> List[Dict[str, Any]]:
    """Return zones fully below or fully above current price."""
    matching_zones: List[Dict[str, Any]] = []

    for zone in zones or []:
        if not isinstance(zone, dict):
            continue

        low = _as_float(zone.get("low"))
        high = _as_float(zone.get("high"))
        if low is None or high is None:
            continue

        normalized_zone = {
            "date": zone.get("date"),
            "low": low,
            "high": high,
        }

        if side == "below" and high < current_price:
            matching_zones.append(normalized_zone)
        elif side == "above" and low > current_price:
            matching_zones.append(normalized_zone)

    if side == "below":
        return sorted(matching_zones, key=lambda item: item["high"], reverse=True)

    return sorted(matching_zones, key=lambda item: item["low"])


def _trading_days_since(start_date: Any, end_date: Optional[date] = None) -> Optional[int]:
    """Approximate business days elapsed since start_date."""
    if not start_date:
        return None

    parsed = pd.to_datetime(start_date, errors="coerce")
    if pd.isna(parsed):
        return None

    end = pd.Timestamp(end_date or date.today())
    parsed = parsed.normalize()
    end = end.normalize()
    if parsed > end:
        return 0

    return max(len(pd.bdate_range(parsed, end)) - 1, 0)


def _clamp_score(score: float) -> int:
    """Clamp same-day focus score into a 0-100 integer range."""
    return int(round(max(0, min(100, score))))


def _empty_diagnostics() -> Dict[str, Any]:
    """Return the default diagnostic payload for actionability debugging."""
    return {
        "pct_above_ema8": None,
        "pct_above_ema21": None,
        "nearest_support_distance_pct": None,
        "nearest_resistance_distance_pct": None,
        "nearest_demand_distance_pct": None,
        "nearest_supply_distance_pct": None,
        "ignition_age_bars": None,
        "pct_from_ignition_close": None,
        "has_nearby_trigger": False,
        "has_nearby_retest_area": False,
        "has_reasonable_invalidation": False,
        "is_extended": False,
        "is_severely_extended": False,
    }


def _base_focus_result(symbol: str) -> Dict[str, Any]:
    """Return the default same-day focus result shape."""
    return {
        "symbol": str(symbol).strip().upper(),
        "today_focus_score": 0,
        "actionability": "avoid",
        "trigger_level": None,
        "invalidation_level": None,
        "do_not_chase_above": None,
        "preferred_entry_style": "no_trade",
        "same_day_thesis": "",
        "why_today": [],
        "warnings": [],
        "disqualifiers": [],
        "diagnostics": _empty_diagnostics(),
    }


def evaluate_today_focus(symbol: str, technicals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate whether a stock deserves same-day focus-list attention.
    """
    result = _base_focus_result(symbol)
    if not isinstance(technicals, dict):
        result["same_day_thesis"] = "No technical data was provided."
        result["disqualifiers"].append("Missing technical analysis.")
        return result

    score = 0.0
    why_today: List[str] = []
    warnings: List[str] = []
    disqualifiers: List[str] = []

    ema_regime = technicals.get("ema_regime")
    if not isinstance(ema_regime, dict):
        ema_regime = {}

    current_price = _as_float(ema_regime.get("close"))
    ema8 = _as_float(ema_regime.get("ema8"))
    ema21 = _as_float(ema_regime.get("ema21"))
    regime = str(ema_regime.get("regime", "")).strip().lower()

    score += {
        "strong_bullish": 25,
        "bullish": 15,
        "mixed": -5,
        "bearish": -20,
        "strong_bearish": -30,
    }.get(regime, 0)

    if regime == "strong_bullish":
        why_today.append("Strong bullish EMA context supports focus-list attention.")
    elif regime == "bullish":
        why_today.append("Bullish EMA context is constructive.")
    elif regime == "mixed":
        warnings.append("EMA regime is mixed, so same-day actionability needs extra confirmation.")
    elif regime in {"bearish", "strong_bearish"}:
        disqualifiers.append("Bearish EMA regime is not suitable for a long focus-list setup.")

    ignition_candle = technicals.get("ignition_candle")
    ignition_direction = ""
    ignition_age_bars = None
    ignition_close = None
    pct_from_ignition = None
    if isinstance(ignition_candle, dict) and ignition_candle.get("found"):
        ignition_direction = str(ignition_candle.get("direction", "")).strip().lower()
        ignition_close = _as_float(ignition_candle.get("close"))

        if ignition_direction == "bullish":
            score += 20
            why_today.append("Bullish ignition candle suggests a recent impulse or catalyst.")
            ignition_age_bars = _trading_days_since(ignition_candle.get("date"))
            if ignition_age_bars is not None and ignition_age_bars <= 10:
                score += 10
                why_today.append("Bullish ignition candle is recent enough for today's focus.")
            elif ignition_age_bars is not None:
                warnings.append("Bullish ignition candle is older than 10 trading days.")
        elif ignition_direction == "bearish":
            score -= 20
            disqualifiers.append("Bearish ignition candle argues against same-day long focus.")

        if current_price is not None and ignition_close is not None and ignition_close > 0:
            pct_from_ignition = ((current_price / ignition_close) - 1) * 100
            if pct_from_ignition > 10:
                warnings.append("Current price is extended more than 10% above the ignition close.")
    else:
        warnings.append("No bullish ignition candle was detected.")

    accumulation_distribution = technicals.get("accumulation_distribution")
    if not isinstance(accumulation_distribution, dict):
        accumulation_distribution = {}

    volume_bias = str(accumulation_distribution.get("volume_bias", "")).strip().lower()
    obv_trend = str(accumulation_distribution.get("obv_trend", "")).strip().lower()

    if volume_bias == "accumulation":
        score += 20
        why_today.append("Accumulation bias supports same-day focus.")
    elif volume_bias == "distribution":
        score -= 25
        disqualifiers.append("Distribution bias sharply reduces same-day actionability.")
    elif obv_trend == "rising":
        score += 8
        why_today.append("OBV is rising despite neutral volume bias.")
    elif obv_trend == "falling":
        score -= 8
        warnings.append("OBV is falling, weakening same-day confidence.")

    if volume_bias == "distribution" and obv_trend == "falling":
        disqualifiers.append("Distribution with falling OBV is a hard avoid for long focus.")

    nearest_support = None
    nearest_resistance = None
    nearest_demand = None
    nearest_supply = None
    support_distance = None
    trigger_distance = None
    supply_distance = None
    demand_distance = None
    demand_low_distance = None
    pct_above_ema8 = None
    pct_above_ema21 = None
    ema8_below_distance = None
    ema21_below_distance = None

    support_resistance = technicals.get("support_resistance")
    if not isinstance(support_resistance, dict):
        support_resistance = {}

    supply_demand = technicals.get("supply_demand_zones")
    if not isinstance(supply_demand, dict):
        supply_demand = {}

    if current_price is None or current_price <= 0:
        disqualifiers.append("Current price is unavailable, so same-day risk cannot be framed.")
    else:
        support_below = _levels_on_side(
            support_resistance.get("support_levels"), current_price, "below"
        )
        resistance_above = _levels_on_side(
            support_resistance.get("resistance_levels"), current_price, "above"
        )

        nearest_support = support_below[0] if support_below else None
        nearest_resistance = resistance_above[0] if resistance_above else None
        support_distance = _pct_distance(nearest_support, current_price)
        trigger_distance = _pct_distance(nearest_resistance, current_price)

        if nearest_support is not None:
            result["invalidation_level"] = nearest_support
            if support_distance is not None and support_distance <= 5:
                score += 15
                why_today.append("Nearby support gives a usable same-day invalidation reference.")
            else:
                score += 5
                warnings.append("Support exists but is farther away, making same-day risk wider.")
        else:
            score -= 15
            warnings.append("No support below current price was identified for invalidation.")

        if nearest_resistance is not None:
            result["trigger_level"] = nearest_resistance
            if trigger_distance is not None and trigger_distance <= 3:
                score += 15
                why_today.append("Price is near an overhead trigger level.")
            elif trigger_distance is not None and trigger_distance <= 6:
                score += 8
                why_today.append("A breakout trigger exists but is not immediate.")
            else:
                warnings.append("Resistance trigger is too far away to be a clean same-day trigger.")
        else:
            warnings.append("No clear overhead resistance/trigger level identified.")

        demand_below = _zones_on_side(supply_demand.get("demand_zones"), current_price, "below")
        supply_above = _zones_on_side(supply_demand.get("supply_zones"), current_price, "above")
        nearest_demand = demand_below[0] if demand_below else None
        nearest_supply = supply_above[0] if supply_above else None

        if nearest_demand is not None:
            demand_distance = _pct_distance(nearest_demand["high"], current_price)
            demand_low_distance = _pct_distance(nearest_demand["low"], current_price)
            if demand_distance is not None and demand_distance <= 8:
                score += 12
                why_today.append("Nearby demand zone can support a pullback or retest plan.")
            else:
                score += 4
                warnings.append("Demand exists but is not near enough for tight same-day risk.")

        if nearest_supply is not None:
            supply_distance = _pct_distance(nearest_supply["low"], current_price)
            if supply_distance is not None and supply_distance <= 3:
                score -= 15
                warnings.append("Active overhead supply is within 3% of current price.")
            elif supply_distance is not None and supply_distance <= 8:
                score -= 8
                warnings.append("Overhead supply is nearby and may limit same-day path.")

        if ema8 is not None and ema8 > 0:
            pct_above_ema8 = ((current_price / ema8) - 1) * 100
            if ema8 < current_price:
                ema8_below_distance = pct_above_ema8
            if 5 <= pct_above_ema8 <= 8:
                score -= 8
                warnings.append("Price is 5-8% above EMA8; avoid chasing without a pullback.")
            elif 8 < pct_above_ema8 <= 15:
                score -= 15
                warnings.append("Price is 8-15% above EMA8 and is extended for same-day entry.")
            elif pct_above_ema8 > 15:
                score -= 25
                warnings.append("Severe extension from EMA8 creates poor same-day risk.")

        if ema21 is not None and ema21 > 0:
            pct_above_ema21 = ((current_price / ema21) - 1) * 100
            if ema21 < current_price:
                ema21_below_distance = pct_above_ema21
            if 8 < pct_above_ema21 <= 12:
                warnings.append("Price is more than 8% above EMA21; same-day risk is widening.")
            elif pct_above_ema21 > 12:
                warnings.append("Price is severely extended from EMA21, so pullback risk is elevated.")

    volume_anomalies = technicals.get("volume_anomalies")
    anomalies = volume_anomalies if isinstance(volume_anomalies, list) else []
    if not anomalies:
        score += 5
        why_today.append("No recent volume anomalies were flagged.")
    else:
        score -= 5
        anomaly_types = {
            str(anomaly.get("type", "")).strip()
            for anomaly in anomalies
            if isinstance(anomaly, dict)
        }
        if "wide_spread_low_volume" in anomaly_types:
            warnings.append("Wide-spread low-volume action may signal weak participation.")
        if "narrow_spread_high_volume" in anomaly_types:
            warnings.append("Narrow-spread high-volume action needs visual review for absorption or distribution.")

    has_trigger = result["trigger_level"] is not None
    bullish_context = regime in {"strong_bullish", "bullish"}
    has_nearby_trigger = (
        nearest_resistance is not None
        and trigger_distance is not None
        and trigger_distance <= 5
    )
    has_nearby_retest_area = (
        (support_distance is not None and support_distance <= 4)
        or (demand_distance is not None and demand_distance <= 6)
        or (ema8_below_distance is not None and 0 <= ema8_below_distance <= 4)
        or (ema21_below_distance is not None and 0 <= ema21_below_distance <= 6)
    )
    has_reasonable_invalidation = (
        (support_distance is not None and support_distance <= 6)
        or (demand_low_distance is not None and demand_low_distance <= 8)
        or (ema21_below_distance is not None and 0 <= ema21_below_distance <= 8)
    )
    is_extended = (
        (pct_above_ema8 is not None and pct_above_ema8 > 5)
        or (pct_above_ema21 is not None and pct_above_ema21 > 8)
    )
    is_severely_extended = (
        (pct_above_ema8 is not None and pct_above_ema8 > 8)
        or (pct_above_ema21 is not None and pct_above_ema21 > 12)
    )
    stale_extended_ignition = (
        ignition_direction == "bullish"
        and ignition_age_bars is not None
        and ignition_age_bars > 10
        and pct_from_ignition is not None
        and pct_from_ignition > 10
    )

    if support_distance is not None and support_distance <= 6:
        result["invalidation_level"] = nearest_support
    elif nearest_demand is not None and demand_low_distance is not None and demand_low_distance <= 8:
        result["invalidation_level"] = nearest_demand["low"]
    elif ema21 is not None and ema21_below_distance is not None and ema21_below_distance <= 8:
        result["invalidation_level"] = ema21

    if current_price is not None and current_price > 0 and not has_nearby_trigger:
        warnings.append("No nearby breakout trigger was identified within 5% of current price.")

    if not has_nearby_retest_area:
        warnings.append("No nearby pullback or retest area was identified.")

    if not has_reasonable_invalidation:
        warnings.append("No nearby invalidation level; same-day risk is too wide.")

    if nearest_supply is not None and supply_distance is not None and supply_distance <= 3 and not has_nearby_trigger:
        disqualifiers.append("Active overhead supply is too close without a breakout trigger.")

    raw_today_focus_score = _clamp_score(score)
    major_disqualifier = bool(disqualifiers)

    if (
        major_disqualifier
        or raw_today_focus_score < 45
        or regime in {"bearish", "strong_bearish"}
        or (volume_bias == "distribution" and obv_trend == "falling")
    ):
        actionability = "avoid"
    elif bullish_context and (is_extended or is_severely_extended):
        actionability = "pullback_only"
    elif bullish_context and has_nearby_trigger and not is_severely_extended:
        actionability = "breakout_only"
    elif (
        raw_today_focus_score >= 75
        and bullish_context
        and has_reasonable_invalidation
        and (has_nearby_trigger or has_nearby_retest_area)
        and not is_severely_extended
        and not stale_extended_ignition
    ):
        actionability = "ready_today"
    elif raw_today_focus_score >= 45:
        actionability = "needs_more_time"
    else:
        actionability = "avoid"

    if actionability == "ready_today" and not has_nearby_trigger and not has_nearby_retest_area:
        actionability = "needs_more_time"
        warnings.append("No nearby trigger or retest area; not actionable today despite bullish context.")

    if actionability == "ready_today" and is_extended and not has_nearby_trigger:
        actionability = "pullback_only"
        result["do_not_chase_above"] = current_price
        warnings.append("Extended without a nearby breakout trigger; downgraded from ready_today to pullback_only.")

    if is_severely_extended:
        if actionability == "ready_today":
            actionability = "pullback_only" if bullish_context else "needs_more_time"
        elif bullish_context and actionability not in {"avoid", "pullback_only"}:
            actionability = "pullback_only"
        elif not bullish_context and actionability != "avoid":
            actionability = "needs_more_time"
        result["do_not_chase_above"] = current_price
        warnings.append("Severely extended from EMA8/EMA21; not a ready_today setup.")

    if stale_extended_ignition and actionability == "ready_today":
        actionability = "pullback_only" if bullish_context else "needs_more_time"
        warnings.append("Ignition impulse is stale and price is already far above ignition close; not ready_today.")
    elif stale_extended_ignition and actionability in {"breakout_only", "needs_more_time"}:
        warnings.append("Ignition impulse is stale and price is already far above ignition close; not ready_today.")

    if actionability == "ready_today" and not has_reasonable_invalidation:
        actionability = "pullback_only" if bullish_context and is_extended else "needs_more_time"
        warnings.append("No nearby invalidation level; same-day risk is too wide.")

    very_nearby_retest_area = (
        (support_distance is not None and support_distance <= 2)
        or (demand_distance is not None and demand_distance <= 3)
        or (ema8_below_distance is not None and 0 <= ema8_below_distance <= 2)
        or (ema21_below_distance is not None and 0 <= ema21_below_distance <= 3)
    )
    today_focus_score = raw_today_focus_score
    if actionability == "pullback_only":
        if is_severely_extended:
            today_focus_score = min(today_focus_score, 68)
        elif not very_nearby_retest_area:
            today_focus_score = min(today_focus_score, 74)
    elif actionability == "needs_more_time":
        today_focus_score = min(today_focus_score, 64)
    elif actionability == "avoid":
        today_focus_score = min(today_focus_score, 44)
    elif actionability == "breakout_only" and not (
        trigger_distance is not None and trigger_distance <= 3 and has_reasonable_invalidation
    ):
        today_focus_score = min(today_focus_score, 84)

    if actionability == "breakout_only":
        preferred_entry_style = "breakout"
    elif actionability == "pullback_only":
        preferred_entry_style = "pullback"
    elif actionability == "ready_today":
        preferred_entry_style = "breakout" if has_nearby_trigger else "retest"
    else:
        preferred_entry_style = "no_trade"

    if is_extended and result["do_not_chase_above"] is None:
        result["do_not_chase_above"] = current_price if not has_nearby_trigger else result["trigger_level"]

    reference_level = None
    if has_nearby_trigger:
        reference_level = result["trigger_level"]
    elif nearest_support is not None and support_distance is not None and support_distance <= 4:
        reference_level = nearest_support
    elif nearest_demand is not None and demand_distance is not None and demand_distance <= 6:
        reference_level = nearest_demand["high"]
    elif ema8 is not None and ema8_below_distance is not None and ema8_below_distance <= 4:
        reference_level = ema8
    elif ema21 is not None and ema21_below_distance is not None and ema21_below_distance <= 6:
        reference_level = ema21

    if actionability == "ready_today":
        trigger_or_retest = _level_token(reference_level) or "not identified"
        invalidation = _level_token(result["invalidation_level"]) or "not identified"
        same_day_thesis = (
            "Clean enough to watch today with a defined same-day path. "
            f"Trigger/retest reference: {trigger_or_retest}. "
            f"Invalidation reference: {invalidation}."
        )
    elif actionability == "breakout_only":
        trigger = _level_token(result["trigger_level"]) or "not identified"
        same_day_thesis = (
            "Actionable only if price confirms through the trigger with volume. "
            f"Trigger reference: {trigger}. Do not anticipate before confirmation."
        )
    elif actionability == "pullback_only":
        same_day_thesis = (
            "Technically strong but extended; better only on a controlled pullback or retest. "
            "Do not chase current price."
        )
    elif actionability == "needs_more_time":
        same_day_thesis = (
            "Constructive elements are present, but the setup lacks a clean same-day trigger, "
            "retest, or invalidation. Needs more structure."
        )
    else:
        same_day_thesis = (
            "Not suitable for today's focus list based on current structure, volume, trend, "
            "or risk/reward."
        )

    diagnostics = {
        "pct_above_ema8": pct_above_ema8,
        "pct_above_ema21": pct_above_ema21,
        "nearest_support_distance_pct": support_distance,
        "nearest_resistance_distance_pct": trigger_distance,
        "nearest_demand_distance_pct": demand_distance,
        "nearest_supply_distance_pct": supply_distance,
        "ignition_age_bars": ignition_age_bars,
        "pct_from_ignition_close": pct_from_ignition,
        "has_nearby_trigger": has_nearby_trigger,
        "has_nearby_retest_area": has_nearby_retest_area,
        "has_reasonable_invalidation": has_reasonable_invalidation,
        "is_extended": is_extended,
        "is_severely_extended": is_severely_extended,
    }

    result.update(
        {
            "today_focus_score": today_focus_score,
            "actionability": actionability,
            "preferred_entry_style": preferred_entry_style,
            "same_day_thesis": same_day_thesis,
            "why_today": why_today,
            "warnings": warnings,
            "disqualifiers": disqualifiers,
            "diagnostics": diagnostics,
        }
    )
    return result


def batch_evaluate_today_focus(technical_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Evaluate today focus for multiple technical items.
    """
    results: List[Dict[str, Any]] = []

    for item in technical_items or []:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol") or item.get("Symbol")
        technicals = item.get("technicals")
        if not symbol or not isinstance(technicals, dict):
            continue

        focus_result = evaluate_today_focus(str(symbol), technicals)
        if "candidate" in item:
            focus_result["candidate"] = item["candidate"]
        results.append(focus_result)

    return results


if __name__ == "__main__":
    from data_fetcher import fetch_stock_data
    from technical import analyze_stock_technicals

    symbols = ["MSFT", "NVDA", "F", "NKE"]
    outputs = []

    for ticker in symbols:
        try:
            stock_data = fetch_stock_data(ticker, period="6mo", interval="1d")
            technical_data = analyze_stock_technicals(ticker, stock_data)
            outputs.append(evaluate_today_focus(ticker, technical_data))
        except Exception as exc:
            outputs.append(
                {
                    "symbol": ticker,
                    "today_focus_score": 0,
                    "actionability": "avoid",
                    "trigger_level": None,
                    "invalidation_level": None,
                    "do_not_chase_above": None,
                    "preferred_entry_style": "no_trade",
                    "same_day_thesis": f"Evaluation failed: {exc}",
                    "why_today": [],
                    "warnings": [],
                    "disqualifiers": [str(exc)],
                    "diagnostics": _empty_diagnostics(),
                }
            )

    print(json.dumps(outputs, indent=2))

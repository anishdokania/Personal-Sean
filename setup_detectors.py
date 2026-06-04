"""
Loose post-primary setup detectors for visual chart review retrieval.

These detectors answer "is there something interesting enough to inspect?"
They deliberately avoid one strict all-or-nothing setup score.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import pandas as pd

from detector_models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_ORDER,
    DetectorCandidate,
    DetectorHit,
)


REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
WARNING_ONLY_DETECTORS = {"EXTENSION_CHASE_RISK", "FAILED_BREAKOUT_WARNING"}
LOW_SIGNAL_DETECTORS = {"TREND_ALIGNMENT_EMA_RECLAIM", "EXTENSION_CHASE_RISK"}
FAMILY_PRIORITY = [
    "Power gap/catalyst gap",
    "Breakout/retest",
    "Leaders near highs",
    "Inside-day compression",
    "Right-side/base setups",
    "Possible accumulation/emerging reclaim",
    "High RVOL unusual activity",
    "Trend/reclaim",
]


def _safe_float(value: Any) -> Optional[float]:
    """Return a finite float or None."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _clean_text(value: Any) -> str:
    """Return clean optional metadata text."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _row_value(row: Any, *keys: str) -> Any:
    """Read the first available key from a row/dict-like object."""
    for key in keys:
        if hasattr(row, "get"):
            value = row.get(key)
            if _clean_text(value) or _safe_float(value) is not None:
                return value
    return None


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return numeric OHLCV with moving averages, ranges, and ATR context."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("missing OHLCV data")

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"OHLCV missing required columns: {', '.join(missing)}")

    frame = df.copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=REQUIRED_COLUMNS)
    if frame.empty:
        raise ValueError("no clean OHLCV rows")

    if not frame.index.is_monotonic_increasing:
        frame = frame.sort_index()

    frame["EMA8"] = frame["Close"].ewm(span=8, adjust=False).mean()
    frame["EMA21"] = frame["Close"].ewm(span=21, adjust=False).mean()
    frame["EMA50"] = frame["Close"].ewm(span=50, adjust=False).mean()
    frame["AvgVolume5"] = frame["Volume"].rolling(5, min_periods=3).mean()
    frame["AvgVolume20"] = frame["Volume"].rolling(20, min_periods=10).mean()
    frame["AvgVolume60"] = frame["Volume"].rolling(60, min_periods=20).mean()

    previous_close = frame["Close"].shift(1)
    frame["TrueRange"] = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["ATR14"] = frame["TrueRange"].rolling(14, min_periods=10).mean()
    frame["ATR20"] = frame["TrueRange"].rolling(20, min_periods=10).mean()
    frame["ATR60"] = frame["TrueRange"].rolling(60, min_periods=20).mean()
    frame["Range"] = frame["High"] - frame["Low"]
    frame["Body"] = (frame["Close"] - frame["Open"]).abs()
    frame["UpperWick"] = frame["High"] - frame[["Open", "Close"]].max(axis=1)
    frame["LowerWick"] = frame[["Open", "Close"]].min(axis=1) - frame["Low"]
    frame["CloseLocation"] = (
        (frame["Close"] - frame["Low"]) / frame["Range"].replace(0, pd.NA)
    )
    frame["Green"] = frame["Close"] > frame["Open"]
    frame["Red"] = frame["Close"] < frame["Open"]
    return frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def _range_pct(frame: pd.DataFrame, lookback: int) -> Optional[float]:
    """Return high-low range percentage over a lookback."""
    recent = frame.tail(lookback)
    if recent.empty:
        return None
    close = _safe_float(frame["Close"].iloc[-1])
    high = _safe_float(recent["High"].max())
    low = _safe_float(recent["Low"].min())
    if close is None or close <= 0 or high is None or low is None:
        return None
    return ((high - low) / close) * 100


def _rolling_high(frame: pd.DataFrame, lookback: int, exclude_latest: bool = False) -> Optional[float]:
    """Return a recent high."""
    data = frame.iloc[:-1] if exclude_latest else frame
    if data.empty:
        return None
    recent = data.tail(lookback)
    if recent.empty:
        return None
    return _safe_float(recent["High"].max())


def _rolling_low(frame: pd.DataFrame, lookback: int, exclude_latest: bool = False) -> Optional[float]:
    """Return a recent low."""
    data = frame.iloc[:-1] if exclude_latest else frame
    if data.empty:
        return None
    recent = data.tail(lookback)
    if recent.empty:
        return None
    return _safe_float(recent["Low"].min())


def _distance_pct(close: Optional[float], level: Optional[float]) -> Optional[float]:
    """Return percentage distance from close to level."""
    if close is None or level is None or close <= 0:
        return None
    return ((level / close) - 1) * 100


def _pct_above(value: Optional[float], reference: Optional[float]) -> Optional[float]:
    """Return percentage above a reference."""
    if value is None or reference is None or reference <= 0:
        return None
    return ((value / reference) - 1) * 100


def _confidence_from_tag_count(tag_count: int) -> str:
    """Map evidence count to loose confidence."""
    if tag_count >= 4:
        return CONFIDENCE_HIGH
    if tag_count >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _format_optional_pct(value: Optional[float]) -> str:
    """Format optional percentage diagnostics."""
    return f"{value:.1f}%" if value is not None else "N/A"


def _nearest_recent_stop(frame: pd.DataFrame, lookback: int = 10) -> Optional[float]:
    """Return a nearby swing/low reference for long setups."""
    recent_low = _rolling_low(frame, lookback)
    ema21 = _safe_float(frame["EMA21"].iloc[-1]) if "EMA21" in frame.columns else None
    close = _safe_float(frame["Close"].iloc[-1])
    candidates = [level for level in [recent_low, ema21] if level is not None]
    if close is None or not candidates:
        return recent_low
    below = [level for level in candidates if level < close]
    return max(below) if below else min(candidates)


def _average_volume(frame: pd.DataFrame) -> Optional[float]:
    avg_volume = _safe_float(frame["AvgVolume20"].iloc[-1])
    if avg_volume is None:
        avg_volume = _safe_float(frame["Volume"].tail(20).mean())
    return avg_volume


def _current_candidate(symbol: str, row: Any, frame: pd.DataFrame) -> DetectorCandidate:
    """Build the base detector candidate record."""
    latest = frame.iloc[-1]
    avg_volume = _average_volume(frame)
    volume = _safe_float(latest["Volume"])
    rel_volume = volume / avg_volume if volume is not None and avg_volume else None
    sector = (
        _clean_text(_row_value(row, "sector_name"))
        or _clean_text(_row_value(row, "Sector", "sector"))
        or _clean_text(_row_value(row, "Industry", "industry"))
    )
    return DetectorCandidate(
        ticker=symbol,
        company=_clean_text(_row_value(row, "Company", "company", "Security")),
        sector=sector,
        close=_safe_float(latest["Close"]),
        volume=volume,
        avg_volume=avg_volume,
        rel_volume=rel_volume,
    )


def _detector_inside_day(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 2:
        return None
    latest = frame.iloc[-1]
    prior = frame.iloc[-2]
    if not (latest["High"] < prior["High"] and latest["Low"] > prior["Low"]):
        return None

    close = _safe_float(latest["Close"])
    high_20 = _rolling_high(frame, 20)
    high_60 = _rolling_high(frame, 60)
    tags = ["INSIDE_DAY"]
    notes = ["Inside day: current high is below prior high and low is above prior low."]

    if (
        close is not None
        and ((high_20 is not None and close >= high_20 * 0.95) or (high_60 is not None and close >= high_60 * 0.92))
    ):
        tags.append("INSIDE_DAY_NEAR_HIGHS")
        notes.append("Inside day is close to recent highs.")

    if latest["Range"] > 0 and prior["Range"] > 0 and latest["Range"] <= prior["Range"] * 0.7:
        tags.append("TIGHT_INSIDE_DAY")
        notes.append("Inside-day range is materially smaller than the prior range.")

    confidence = CONFIDENCE_HIGH if "INSIDE_DAY_NEAR_HIGHS" in tags and "TIGHT_INSIDE_DAY" in tags else CONFIDENCE_MEDIUM
    return DetectorHit(
        name="INSIDE_DAY",
        confidence=confidence,
        tags=tags,
        setup_family="Inside-day compression",
        trigger_level=_safe_float(latest["High"]),
        stop_reference=_safe_float(latest["Low"]),
        notes=notes,
    )


def _detector_tight_range_compression(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 20:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    atr20 = _safe_float(latest["ATR20"])
    atr60 = _safe_float(latest["ATR60"])
    tags: list[str] = []
    notes: list[str] = []

    range_3 = _range_pct(frame, 3)
    range_5 = _range_pct(frame, 5)
    range_10 = _range_pct(frame, 10)
    if range_3 is not None and range_3 <= 6:
        tags.append("TIGHT_3D_BASE")
    if range_5 is not None and range_5 <= 9:
        tags.append("TIGHT_5D_BASE")
    if range_10 is not None and range_10 <= 14:
        tags.append("TIGHT_10D_BASE")

    range_values = [value for value in [range_3, range_5, range_10] if value is not None]
    if len(range_values) >= 2 and range_values[0] <= range_values[-1] * 0.75:
        tags.append("RANGE_CONTRACTION")
        notes.append("Recent multi-day range is contracting.")

    true_range_5 = _safe_float(frame["TrueRange"].tail(5).mean())
    true_range_20 = _safe_float(frame["TrueRange"].tail(20).mean())
    if (
        true_range_5 is not None
        and true_range_20 is not None
        and true_range_5 <= true_range_20 * 0.8
    ) or (atr20 is not None and atr60 is not None and atr20 <= atr60 * 0.85):
        tags.append("ATR_CONTRACTION")
        notes.append("ATR/recent candle ranges are contracting versus longer context.")

    if not tags:
        return None
    if len(tags) < 2 and "ATR_CONTRACTION" not in tags:
        return None

    trigger = max(
        level
        for level in [_rolling_high(frame, 5), _rolling_high(frame, 10)]
        if level is not None
    )
    if close is not None:
        notes.append(
            "Compression ranges: "
            f"3D {_format_optional_pct(range_3)} / "
            f"5D {_format_optional_pct(range_5)} / "
            f"10D {_format_optional_pct(range_10)}."
        )

    return DetectorHit(
        name="TIGHT_RANGE_COMPRESSION",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Inside-day compression",
        trigger_level=trigger,
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=notes,
    )


def _detector_big_base_near_highs(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 50:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    ema50 = _safe_float(latest["EMA50"])
    high_60 = _rolling_high(frame, 60)
    high_252 = _rolling_high(frame, min(252, len(frame)))
    base_high = high_252 or high_60
    base_low = _rolling_low(frame, min(100, len(frame)))
    if close is None or ema50 is None or base_high is None or base_low is None or base_high <= base_low:
        return None

    near_high = close >= base_high * 0.85 or (high_60 is not None and close >= high_60 * 0.9)
    above_50 = close > ema50
    base_range_pct = ((base_high - base_low) / close) * 100
    range_position = (close - base_low) / (base_high - base_low)
    recent_tight = (_range_pct(frame, 10) or 999) <= 14

    if not (near_high and above_50 and base_range_pct <= 40 and range_position >= 0.55):
        return None

    tags = ["BIG_BASE_NEAR_HIGHS", "LEADER_BASE"]
    notes = ["Large base/range is near highs and price is above the 50 EMA."]
    if recent_tight:
        tags.append("UPPER_RANGE_TIGHTENING")
        notes.append("Recent action is tightening in the upper portion of the range.")
    if close >= base_high * 0.97:
        tags.append("BASE_HIGH_PROXIMITY")
        notes.append("Price is close to the base high.")

    confidence = CONFIDENCE_HIGH if "UPPER_RANGE_TIGHTENING" in tags and "BASE_HIGH_PROXIMITY" in tags else CONFIDENCE_MEDIUM
    return DetectorHit(
        name="BIG_BASE_NEAR_HIGHS",
        confidence=confidence,
        tags=tags,
        setup_family="Leaders near highs",
        trigger_level=base_high,
        stop_reference=_nearest_recent_stop(frame, 20),
        notes=notes,
    )


def _detector_right_side_of_base(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 40:
        return None

    base_len = min(100, len(frame))
    base = frame.tail(base_len)
    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    base_high = _safe_float(base["High"].max())
    base_low = _safe_float(base["Low"].min())
    if close is None or base_high is None or base_low is None or base_high <= base_low:
        return None

    midpoint = base_low + ((base_high - base_low) * 0.5)
    range_position = (close - base_low) / (base_high - base_low)
    prior_lows = frame.iloc[-40:-10]["Low"] if len(frame) >= 40 else frame.iloc[:-10]["Low"]
    recent_low = _rolling_low(frame, 10)
    higher_low = (
        recent_low is not None
        and not prior_lows.empty
        and recent_low >= float(prior_lows.min()) * 1.02
    )
    approaching_base_high = close >= base_high * 0.88
    recent_high = _rolling_high(frame, 20)
    recent_pullback_low = _rolling_low(frame, 10)
    shallow_pullback = False
    if recent_high is not None and recent_pullback_low is not None and base_high > base_low:
        shallow_pullback = (recent_high - recent_pullback_low) <= (base_high - base_low) * 0.35

    if not (close > midpoint and range_position >= 0.5 and (higher_low or shallow_pullback) and approaching_base_high):
        return None

    tags = ["RIGHT_SIDE_OF_BASE", "BASE_MATURING"]
    notes = ["Price is on the right side of a multi-week base/range."]
    if higher_low:
        tags.append("HIGHER_LOW_IN_BASE")
    if shallow_pullback:
        tags.append("SHALLOW_RIGHT_SIDE_PULLBACK")

    return DetectorHit(
        name="RIGHT_SIDE_OF_BASE",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Right-side/base setups",
        trigger_level=base_high,
        stop_reference=recent_low,
        notes=notes,
    )


def _detector_possible_accumulation_base(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 60:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    ema21 = _safe_float(latest["EMA21"])
    ema50 = _safe_float(latest["EMA50"])
    high_100 = _rolling_high(frame, min(100, len(frame)))
    low_100 = _rolling_low(frame, min(100, len(frame)))
    low_20 = _rolling_low(frame, 20)
    prior_low_20 = _safe_float(frame.iloc[-40:-20]["Low"].min()) if len(frame) >= 40 else None
    if close is None or high_100 is None or low_100 is None or high_100 <= 0:
        return None

    decline_from_high = ((high_100 - (low_20 or low_100)) / high_100) * 100
    prior_downtrend = decline_from_high >= 18
    stopped_lower_lows = (
        low_20 is not None
        and prior_low_20 is not None
        and low_20 >= prior_low_20 * 0.97
    )
    base_range_pct = ((high_100 - low_100) / close) * 100

    recent = frame.tail(30)
    high_volume_green = recent[
        (recent["Green"])
        & (recent["Volume"] >= recent["AvgVolume20"].fillna(recent["Volume"].mean()) * 1.3)
        & (recent["CloseLocation"] >= 0.55)
    ]
    up_days = recent[recent["Green"]]
    down_days = recent[recent["Red"]]
    up_volume = _safe_float(up_days["Volume"].mean()) if not up_days.empty else None
    down_volume = _safe_float(down_days["Volume"].mean()) if not down_days.empty else None
    green_volume_dominance = (
        up_volume is not None
        and down_volume is not None
        and up_volume >= down_volume * 1.05
    )
    reclaiming_ema = (
        (ema21 is not None and close >= ema21 * 0.97)
        or (ema50 is not None and close >= ema50 * 0.97)
    )
    recent_low = _rolling_low(frame, 10)
    prior_recent_low = _safe_float(frame.iloc[-30:-10]["Low"].min()) if len(frame) >= 30 else None
    higher_low = (
        recent_low is not None
        and prior_recent_low is not None
        and recent_low >= prior_recent_low * 1.02
    )

    if not (
        prior_downtrend
        and base_range_pct <= 55
        and (stopped_lower_lows or higher_low)
        and (len(high_volume_green) >= 2 or green_volume_dominance or reclaiming_ema)
    ):
        return None

    tags = ["POSSIBLE_ACCUMULATION_BASE"]
    notes = ["Possible accumulation only: prior decline has started to stabilize."]
    if len(high_volume_green) >= 2:
        tags.append("HIGH_VOLUME_REVERSAL_FROM_LOW")
    if green_volume_dominance:
        tags.append("GREEN_VOLUME_DOMINANCE")
    if stopped_lower_lows:
        tags.append("FAILED_BREAKDOWN_RECLAIM")
    if reclaiming_ema:
        tags.append("EMERGING_BASE_RECLAIM")

    return DetectorHit(
        name="POSSIBLE_ACCUMULATION_BASE",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Possible accumulation/emerging reclaim",
        trigger_level=_rolling_high(frame, 20),
        stop_reference=recent_low or low_20,
        notes=notes,
    )


def _detector_catalyst_gap(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 30:
        return None

    gaps: list[tuple[int, float]] = []
    start_idx = max(1, len(frame) - 30)
    for idx in range(start_idx, len(frame)):
        row = frame.iloc[idx]
        prev = frame.iloc[idx - 1]
        prev_close = _safe_float(prev["Close"])
        open_price = _safe_float(row["Open"])
        avg_volume = _safe_float(frame["Volume"].iloc[max(0, idx - 20):idx].mean())
        volume = _safe_float(row["Volume"])
        if not prev_close or open_price is None or not avg_volume or volume is None:
            continue
        gap_pct = ((open_price / prev_close) - 1) * 100
        if gap_pct >= 5 and volume >= avg_volume * 1.8:
            gaps.append((idx, gap_pct))

    if not gaps:
        return None

    gap_idx, gap_pct = gaps[-1]
    gap_day = frame.iloc[gap_idx]
    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    gap_low = _safe_float(gap_day["Low"])
    gap_mid = _safe_float((gap_day["High"] + gap_day["Low"]) / 2)
    gap_high = _safe_float(gap_day["High"])
    if close is None or gap_low is None or gap_mid is None:
        return None

    holding_mid = close >= gap_mid or close >= gap_low * 1.02
    if not holding_mid:
        return None

    after_gap = frame.iloc[gap_idx + 1 :]
    post_gap_tight = False
    post_gap_volume_contracts = False
    if len(after_gap) >= 3:
        post_gap_tight = (_range_pct(after_gap, min(10, len(after_gap))) or 999) <= 18
        avg_after_gap_volume = _safe_float(after_gap["Volume"].mean())
        gap_volume = _safe_float(gap_day["Volume"])
        post_gap_volume_contracts = (
            avg_after_gap_volume is not None
            and gap_volume is not None
            and avg_after_gap_volume <= gap_volume * 0.75
        )

    tags = ["CATALYST_GAP"]
    notes = [f"Large gap up of {gap_pct:.1f}% on elevated volume is still holding."]
    if post_gap_tight:
        tags.append("POST_GAP_FLAG")
    if close >= (gap_high or close) * 0.9:
        tags.append("GAP_HOLDING_HIGH")
    if post_gap_tight and post_gap_volume_contracts:
        tags.append("GAP_BASE_TRIGGER_READY")

    return DetectorHit(
        name="POWER_EARNINGS_GAP_CATALYST_GAP",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Power gap/catalyst gap",
        trigger_level=_rolling_high(frame.iloc[gap_idx:], min(10, len(frame) - gap_idx)),
        stop_reference=gap_mid if close >= gap_mid else gap_low,
        notes=notes,
    )


def _detector_high_relative_volume(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 20:
        return None

    latest = frame.iloc[-1]
    volume = _safe_float(latest["Volume"])
    avg_volume = _average_volume(frame)
    if volume is None or not avg_volume:
        return None

    rel_volume = volume / avg_volume
    if rel_volume < 1.1:
        return None

    close = _safe_float(latest["Close"])
    prior_high_5 = _rolling_high(frame, 5, exclude_latest=True)
    ema21 = _safe_float(latest["EMA21"])
    prior_ema21 = _safe_float(frame["EMA21"].iloc[-2]) if len(frame) >= 2 else None
    prior_close = _safe_float(frame["Close"].iloc[-2]) if len(frame) >= 2 else None
    positive_tags: list[str] = []
    if latest["Green"] and latest["CloseLocation"] >= 0.55:
        positive_tags.append("RVOL_WITH_GREEN_CLOSE")
    if close is not None and prior_high_5 is not None and close > prior_high_5:
        positive_tags.append("RVOL_BREAKOUT")
    if (
        close is not None
        and ema21 is not None
        and prior_close is not None
        and prior_ema21 is not None
        and prior_close < prior_ema21
        and close > ema21
    ):
        positive_tags.append("RVOL_RECLAIM")
        positive_tags.append("HIGH_RVOL_RECLAIM")

    if not positive_tags:
        return None

    tags = ["HIGH_REL_VOLUME", *positive_tags]
    if rel_volume >= 1.5:
        tags.append("UNUSUAL_VOLUME")
    notes = [f"Relative volume is {rel_volume:.1f}x and paired with constructive price action."]
    confidence = CONFIDENCE_HIGH if rel_volume >= 2.0 else CONFIDENCE_MEDIUM if rel_volume >= 1.3 else CONFIDENCE_LOW
    return DetectorHit(
        name="HIGH_RELATIVE_VOLUME",
        confidence=confidence,
        tags=tags,
        setup_family="High RVOL unusual activity",
        trigger_level=_rolling_high(frame, 5),
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=notes,
    )


def _breakout_levels(frame: pd.DataFrame) -> list[tuple[str, Optional[float]]]:
    """Return named trigger levels used by proximity and clarity detectors."""
    high_52w = _rolling_high(frame, min(252, len(frame)), exclude_latest=True)
    return [
        ("prior_day_high", _rolling_high(frame, 1, exclude_latest=True)),
        ("5d_high", _rolling_high(frame, 5, exclude_latest=True)),
        ("20d_high", _rolling_high(frame, 20, exclude_latest=True)),
        ("60d_high", _rolling_high(frame, 60, exclude_latest=True)),
        ("52w_high", high_52w),
    ]


def _nearest_trigger(frame: pd.DataFrame, max_pct: float = 4.0) -> tuple[Optional[str], Optional[float], Optional[float]]:
    """Return the nearest clean high/pivot trigger near current price."""
    close = _safe_float(frame["Close"].iloc[-1])
    atr = _safe_float(frame["ATR14"].iloc[-1])
    if close is None or close <= 0:
        return None, None, None

    candidates: list[tuple[str, float, float]] = []
    for name, level in _breakout_levels(frame):
        if level is None or level <= 0:
            continue
        distance = _distance_pct(close, level)
        if distance is None:
            continue
        atr_pct = ((atr / close) * 100) if atr is not None and atr > 0 else max_pct
        allowed = max_pct if name != "52w_high" else 8.0
        if -1.0 <= distance <= max(allowed, atr_pct * 0.8):
            candidates.append((name, level, distance))

    if not candidates:
        return None, None, None
    return min(candidates, key=lambda item: abs(item[2]))


def _detector_breakout_proximity(frame: pd.DataFrame) -> Optional[DetectorHit]:
    name, level, distance = _nearest_trigger(frame, max_pct=3.0)
    if name is None or level is None or distance is None:
        return None

    tags = ["NEAR_BREAKOUT_TRIGGER", "CLEAR_TRIGGER_NEARBY"]
    if name in {"20d_high", "60d_high", "52w_high"}:
        tags.append(f"NEAR_{name.upper()}")
    if name == "52w_high":
        tags.append("NEAR_52W_HIGH")
    tags.append("CLEAR_TRIGGER_NEARBY")
    notes = [f"Nearest trigger is {name.replace('_', ' ')} at {level:.2f}, {distance:.1f}% from close."]

    high_20 = _rolling_high(frame, 20)
    high_60 = _rolling_high(frame, 60)
    close = _safe_float(frame["Close"].iloc[-1])
    ema8 = _safe_float(frame["EMA8"].iloc[-1])
    ema21 = _safe_float(frame["EMA21"].iloc[-1])
    ema50 = _safe_float(frame["EMA50"].iloc[-1])
    if (
        close is not None
        and ema8 is not None
        and ema21 is not None
        and ema50 is not None
        and close > ema8 > ema21 > ema50
        and ((high_20 and close >= high_20 * 0.97) or (high_60 and close >= high_60 * 0.94))
    ):
        tags.append("LEADING_NAME_NEAR_TRIGGER")

    return DetectorHit(
        name="BREAKOUT_PROXIMITY",
        confidence=CONFIDENCE_HIGH if abs(distance) <= 1.5 else CONFIDENCE_MEDIUM,
        tags=tags,
        setup_family="Breakout/retest",
        trigger_level=level,
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=notes,
    )


def _detector_breakout_confirmed(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 20:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    prior_day_high = _rolling_high(frame, 1, exclude_latest=True)
    high_20 = _rolling_high(frame, 20, exclude_latest=True)
    high_60 = _rolling_high(frame, 60, exclude_latest=True)
    trigger = max(level for level in [prior_day_high, high_20] if level is not None) if any(level is not None for level in [prior_day_high, high_20]) else None
    if close is None or trigger is None or close <= trigger:
        return None

    volume = _safe_float(latest["Volume"])
    avg_volume20 = _average_volume(frame)
    avg_volume5 = _safe_float(frame["Volume"].tail(5).mean())
    strong_close = _safe_float(latest["CloseLocation"]) is not None and latest["CloseLocation"] >= 0.7
    volume_expansion = (
        volume is not None
        and (
            (avg_volume20 is not None and volume >= avg_volume20 * 1.1)
            or (avg_volume5 is not None and volume >= avg_volume5 * 1.1)
        )
    )
    ema8 = _safe_float(latest["EMA8"])
    pct_above_ema8 = _pct_above(close, ema8)
    not_extended = pct_above_ema8 is None or pct_above_ema8 <= 10
    higher_breakout = high_60 is not None and close > high_60

    if not (strong_close and volume_expansion):
        return None

    tags = ["DAILY_BREAKOUT_CONFIRMED", "CLOSE_ABOVE_TRIGGER", "STRONG_CLOSE"]
    if volume_expansion:
        tags.append("HIGH_VOLUME_BREAKOUT")
    if not_extended:
        tags.append("BREAKOUT_NOT_EXTENDED")
    if higher_breakout:
        tags.append("NEAR_60D_HIGH")

    return DetectorHit(
        name="BREAKOUT_CONFIRMED",
        confidence=CONFIDENCE_HIGH if not_extended else CONFIDENCE_MEDIUM,
        tags=tags,
        setup_family="Breakout/retest",
        trigger_level=trigger,
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=[f"Close is above trigger {trigger:.2f} with strong close and volume expansion."],
    )


def _detector_breakout_retest(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 35:
        return None

    close = _safe_float(frame["Close"].iloc[-1])
    if close is None:
        return None

    breakout_record: Optional[tuple[int, float, float]] = None
    for idx in range(max(20, len(frame) - 10), len(frame) - 1):
        prior_high = _safe_float(frame["High"].iloc[idx - 20 : idx].max())
        row = frame.iloc[idx]
        row_close = _safe_float(row["Close"])
        row_volume = _safe_float(row["Volume"])
        row_avg_volume = _safe_float(row["AvgVolume20"])
        if (
            prior_high is not None
            and row_close is not None
            and row_close > prior_high * 1.002
            and row_volume is not None
            and row_avg_volume is not None
            and row_volume >= row_avg_volume * 1.15
        ):
            breakout_record = (idx, prior_high, row_volume)

    if breakout_record is None:
        return None

    breakout_idx, breakout_level, breakout_volume = breakout_record
    latest = frame.iloc[-1]
    current_low = _safe_float(latest["Low"])
    current_close = _safe_float(latest["Close"])
    if current_low is None or current_close is None:
        return None

    near_level = current_low <= breakout_level * 1.035 and current_close >= breakout_level * 0.985
    if not near_level:
        return None

    pullback = frame.iloc[breakout_idx + 1 :]
    pullback_volume = _safe_float(pullback["Volume"].mean()) if not pullback.empty else None
    low_volume_pullback = pullback_volume is not None and pullback_volume <= breakout_volume * 0.75
    holding_level = current_close >= breakout_level

    tags = ["BREAKOUT_RETEST"]
    if holding_level:
        tags.append("RETEST_HOLDING")
    if low_volume_pullback:
        tags.append("LOW_VOLUME_PULLBACK")
    if current_low < breakout_level and current_close >= breakout_level:
        tags.append("RECLAIMED_BREAKOUT_LEVEL")

    if len(tags) < 2:
        return None

    return DetectorHit(
        name="BREAKOUT_RETEST",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Breakout/retest",
        trigger_level=_rolling_high(frame, 5),
        stop_reference=breakout_level,
        notes=[f"Recent breakout level near {breakout_level:.2f} is being tested/held."],
    )


def _detector_failed_breakdown_hammer(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 10:
        return None

    latest = frame.iloc[-1]
    candle_range = _safe_float(latest["Range"])
    if not candle_range or candle_range <= 0:
        return None
    lower_wick = _safe_float(latest["LowerWick"]) or 0.0
    body = _safe_float(latest["Body"]) or 0.0
    close_location = _safe_float(latest["CloseLocation"]) or 0.0
    prior_low_5 = _rolling_low(frame, 5, exclude_latest=True)
    prior_low_20 = _rolling_low(frame, 20, exclude_latest=True)
    current_low = _safe_float(latest["Low"])
    current_close = _safe_float(latest["Close"])
    volume = _safe_float(latest["Volume"])
    avg_volume = _average_volume(frame)

    long_lower_wick = lower_wick >= max(body * 1.5, candle_range * 0.35)
    upper_close = close_location >= 0.55
    undercut_level = prior_low_5 or prior_low_20
    undercut_reclaim = (
        current_low is not None
        and current_close is not None
        and undercut_level is not None
        and current_low < undercut_level
        and current_close > undercut_level
    )

    if not (long_lower_wick and upper_close and (undercut_reclaim or close_location >= 0.7)):
        return None

    tags = ["HAMMER_REVERSAL", "LOWER_WICK_DEMAND"]
    if undercut_reclaim:
        tags.extend(["FAILED_BREAKDOWN", "FAILED_BREAKDOWN_RECLAIM", "SELLERS_TRAPPED", "SUPPORT_RECLAIM"])
    if volume is not None and avg_volume is not None and volume >= avg_volume * 1.1:
        tags.append("HIGH_VOLUME_REVERSAL_FROM_LOW")

    return DetectorHit(
        name="FAILED_BREAKDOWN_RECLAIM_HAMMER_REVERSAL",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Possible accumulation/emerging reclaim",
        trigger_level=_safe_float(latest["High"]),
        stop_reference=_safe_float(latest["Low"]),
        notes=["Long lower wick/reclaim suggests sellers may be trapped near support."],
    )


def _detector_bull_flag_wedge(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 25:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    ema8 = _safe_float(latest["EMA8"])
    ema21 = _safe_float(latest["EMA21"])
    ema50 = _safe_float(latest["EMA50"])
    window = frame.tail(25)
    impulse_low = _safe_float(window["Low"].iloc[:15].min())
    impulse_high = _safe_float(window["High"].iloc[5:20].max())
    recent_low = _rolling_low(frame, 10)
    if close is None or impulse_low is None or impulse_high is None or recent_low is None or impulse_low <= 0:
        return None

    impulse_pct = ((impulse_high / impulse_low) - 1) * 100
    if impulse_pct < 8:
        return None

    impulse_height = impulse_high - impulse_low
    pullback_depth = impulse_high - recent_low
    shallow_retrace = impulse_height > 0 and pullback_depth <= impulse_height * 0.55
    avg_recent_volume = _safe_float(frame["Volume"].tail(5).mean())
    avg_prior_volume = _safe_float(frame["Volume"].tail(20).head(10).mean())
    volume_contracts = (
        avg_recent_volume is not None
        and avg_prior_volume is not None
        and avg_recent_volume <= avg_prior_volume * 0.85
    )
    near_ema = (
        (ema8 is not None and abs((close / ema8) - 1) <= 0.04)
        or (ema21 is not None and abs((close / ema21) - 1) <= 0.05)
    )
    above_50 = ema50 is not None and close > ema50
    range_5 = _range_pct(frame, 5)
    range_10 = _range_pct(frame, 10)
    wedge = range_5 is not None and range_10 is not None and range_5 <= range_10 * 0.8

    if not (shallow_retrace and (volume_contracts or near_ema or wedge) and (near_ema or above_50)):
        return None

    tags = ["POSSIBLE_BULL_FLAG", "CONTROLLED_PULLBACK", "SHALLOW_RETRACE"]
    if ema8 is not None and abs((close / ema8) - 1) <= 0.04:
        tags.append("FLAG_INTO_8EMA")
    if ema21 is not None and abs((close / ema21) - 1) <= 0.05:
        tags.append("FLAG_INTO_21EMA")
    if wedge:
        tags.append("WEDGE_COMPRESSION")

    return DetectorHit(
        name="POSSIBLE_BULL_FLAG_WEDGE_COMPRESSION",
        confidence=_confidence_from_tag_count(len(tags)),
        tags=tags,
        setup_family="Right-side/base setups",
        trigger_level=_rolling_high(frame, 10),
        stop_reference=recent_low,
        notes=["Prior impulse is digesting in a controlled pullback/compression area."],
    )


def _detector_trend_alignment(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 50:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    ema8 = _safe_float(latest["EMA8"])
    ema21 = _safe_float(latest["EMA21"])
    ema50 = _safe_float(latest["EMA50"])
    if close is None or ema8 is None or ema21 is None or ema50 is None:
        return None

    tags: list[str] = []
    notes: list[str] = []
    if close > ema8 and close > ema21 and close > ema50:
        tags.append("ABOVE_8_21_50")
    if close > ema8 > ema21 > ema50:
        tags.append("LEADER_TREND")
        notes.append("EMA stack is aligned 8 > 21 > 50 with price above the stack.")

    if len(frame) >= 2:
        prior = frame.iloc[-2]
        prior_close = _safe_float(prior["Close"])
        for label in ["8", "21", "50"]:
            ema_column = f"EMA{label}"
            prior_ema = _safe_float(prior[ema_column])
            current_ema = _safe_float(latest[ema_column])
            if (
                prior_close is not None
                and prior_ema is not None
                and current_ema is not None
                and prior_close < prior_ema
                and close > current_ema
            ):
                tags.append(f"RECLAIMED_{label}EMA")
                tags.append("EMERGING_RECLAIM")

    if not tags:
        return None

    return DetectorHit(
        name="TREND_ALIGNMENT_EMA_RECLAIM",
        confidence=CONFIDENCE_MEDIUM if "LEADER_TREND" in tags or "EMERGING_RECLAIM" in tags else CONFIDENCE_LOW,
        tags=tags,
        setup_family="Trend/reclaim",
        trigger_level=_rolling_high(frame, 5),
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=notes or ["Price has constructive EMA alignment or reclaim behavior."],
    )


def _detector_extension(frame: pd.DataFrame) -> Optional[DetectorHit]:
    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    ema8 = _safe_float(latest["EMA8"])
    if close is None or ema8 is None or ema8 <= 0:
        return None

    pct_above_ema8 = ((close / ema8) - 1) * 100
    tags: list[str] = []
    notes: list[str] = [f"Close is {pct_above_ema8:.1f}% from EMA8."]
    if pct_above_ema8 <= 3:
        tags.append("NEAR_8EMA_IDEAL")
    elif pct_above_ema8 <= 8:
        tags.append("MILD_EXTENSION")
    elif pct_above_ema8 <= 12:
        tags.extend(["MILD_EXTENSION", "CHASE_RISK"])
    else:
        tags.extend(["OVEREXTENDED", "CHASE_RISK"])

    if len(frame) >= 2:
        prior_close = _safe_float(frame["Close"].iloc[-2])
        open_price = _safe_float(latest["Open"])
        if prior_close is not None and open_price is not None and prior_close > 0:
            gap_pct = ((open_price / prior_close) - 1) * 100
            if gap_pct >= 8:
                tags.append("CHASE_RISK")
                notes.append(f"Current session gapped {gap_pct:.1f}%.")

    return DetectorHit(
        name="EXTENSION_CHASE_RISK",
        confidence=CONFIDENCE_LOW,
        tags=tags,
        setup_family=None,
        notes=notes,
    )


def _detector_trigger_clarity(frame: pd.DataFrame) -> Optional[DetectorHit]:
    trigger_name, trigger_level, distance = _nearest_trigger(frame, max_pct=4.0)
    tags: list[str] = []
    notes: list[str] = []
    if trigger_level is None or distance is None:
        return DetectorHit(
            name="TRIGGER_CLARITY",
            confidence=CONFIDENCE_LOW,
            tags=["NO_CLEAR_TRIGGER"],
            notes=["No nearby clean prior-high/base trigger was found."],
        )

    if distance <= 4:
        tags.extend(["CLEAR_TRIGGER", "NEAR_TRIGGER"])
        notes.append(f"Nearby trigger from {trigger_name.replace('_', ' ')} at {trigger_level:.2f}.")
    else:
        tags.append("TRIGGER_TOO_FAR")
        notes.append(f"Nearest trigger is {distance:.1f}% away.")

    levels = [level for _, level in _breakout_levels(frame) if level is not None]
    clustered = [
        level
        for level in levels
        if trigger_level > 0 and abs((level / trigger_level) - 1) <= 0.015
    ]
    if len(clustered) >= 2:
        tags.append("MULTI_TOUCH_RESISTANCE")

    return DetectorHit(
        name="TRIGGER_CLARITY",
        confidence=CONFIDENCE_MEDIUM if "CLEAR_TRIGGER" in tags else CONFIDENCE_LOW,
        tags=tags,
        setup_family=None,
        trigger_level=trigger_level,
        stop_reference=_nearest_recent_stop(frame, 10),
        notes=notes,
    )


def _detector_risk_reward(frame: pd.DataFrame) -> Optional[DetectorHit]:
    _, trigger_level, _ = _nearest_trigger(frame, max_pct=4.0)
    close = _safe_float(frame["Close"].iloc[-1])
    entry = trigger_level or close
    stop = _nearest_recent_stop(frame, 10)
    if close is None or entry is None or stop is None or entry <= 0 or stop >= entry:
        return None

    risk_pct = ((entry - stop) / entry) * 100
    high_60 = _rolling_high(frame, 60)
    high_252 = _rolling_high(frame, min(252, len(frame)))
    target_candidates = [
        level
        for level in [high_60, high_252]
        if level is not None and level > entry * 1.01
    ]
    target = min(target_candidates) if target_candidates else entry + (entry - stop) * 2
    reward = target - entry
    risk = entry - stop
    rr = reward / risk if risk > 0 else None

    tags: list[str] = []
    notes = [f"Approximate entry {entry:.2f}, stop {stop:.2f}, risk {risk_pct:.1f}%."]
    if risk_pct <= 6:
        tags.append("TIGHT_RISK")
    elif risk_pct > 10:
        tags.append("STOP_TOO_WIDE")
    if rr is not None and rr >= 1.5:
        tags.append("GOOD_RR")
    elif rr is not None and rr < 1.0:
        tags.append("RESISTANCE_TOO_CLOSE")

    if not tags:
        return None
    return DetectorHit(
        name="RISK_REWARD_VIABILITY",
        confidence=CONFIDENCE_MEDIUM if "GOOD_RR" in tags or "TIGHT_RISK" in tags else CONFIDENCE_LOW,
        tags=tags,
        setup_family=None,
        trigger_level=entry,
        stop_reference=stop,
        notes=notes,
    )


def _detector_failed_breakout_warning(frame: pd.DataFrame) -> Optional[DetectorHit]:
    if len(frame) < 20:
        return None

    latest = frame.iloc[-1]
    close = _safe_float(latest["Close"])
    open_price = _safe_float(latest["Open"])
    resistance = _rolling_high(frame, 20, exclude_latest=True)
    high = _safe_float(latest["High"])
    candle_range = _safe_float(latest["Range"])
    upper_wick = _safe_float(latest["UpperWick"]) or 0.0
    close_location = _safe_float(latest["CloseLocation"]) or 0.0
    volume = _safe_float(latest["Volume"])
    avg_volume = _average_volume(frame)
    if close is None or open_price is None or resistance is None or high is None or not candle_range:
        return None

    failed_intraday_break = high > resistance * 1.005 and close < resistance
    upper_wick_supply = upper_wick >= candle_range * 0.35 and close_location <= 0.55
    heavy_red = close < open_price and volume is not None and avg_volume is not None and volume >= avg_volume * 1.3

    recent_breakout_failure = False
    for idx in range(max(20, len(frame) - 6), len(frame) - 1):
        prior_high = _safe_float(frame["High"].iloc[idx - 20 : idx].max())
        breakout_close = _safe_float(frame["Close"].iloc[idx])
        breakout_low = _safe_float(frame["Low"].iloc[idx])
        if (
            prior_high is not None
            and breakout_close is not None
            and breakout_low is not None
            and breakout_close > prior_high
            and close < breakout_low
        ):
            recent_breakout_failure = True
            break

    if not (failed_intraday_break or (upper_wick_supply and heavy_red) or recent_breakout_failure):
        return None

    tags = ["FAILED_BREAKOUT", "DO_NOT_CHASE"]
    if upper_wick_supply:
        tags.extend(["SHOOTING_STAR_WARNING", "UPPER_WICK_SUPPLY"])
    if failed_intraday_break or recent_breakout_failure:
        tags.append("BREAKOUT_FAILURE")
    if heavy_red:
        tags.append("HEAVY_RED_VOLUME")

    confidence = CONFIDENCE_HIGH if "BREAKOUT_FAILURE" in tags and "HEAVY_RED_VOLUME" in tags else CONFIDENCE_MEDIUM
    return DetectorHit(
        name="FAILED_BREAKOUT_WARNING",
        confidence=confidence,
        tags=tags,
        setup_family=None,
        notes=["Failed breakout or heavy upper-wick supply warning fired."],
    )


def _select_setup_family(candidate: DetectorCandidate) -> str:
    """Choose a primary family for report grouping."""
    hit_families = [hit.setup_family for hit in candidate.hits if hit.setup_family]
    for family in FAMILY_PRIORITY:
        if family in hit_families:
            return family
    return hit_families[0] if hit_families else "Unclassified"


def _interest_rank(candidate: DetectorCandidate) -> float:
    """Compute a sort-only interest rank from tags and warnings."""
    rank = 0.0
    for hit in candidate.hits:
        if hit.name in WARNING_ONLY_DETECTORS:
            continue
        rank += {CONFIDENCE_HIGH: 9, CONFIDENCE_MEDIUM: 5, CONFIDENCE_LOW: 2}.get(
            hit.confidence, 1
        )
    rank += len(candidate.high_value_tags) * 4
    if candidate.trigger_level is not None:
        rank += 4
    if candidate.stop_reference is not None:
        rank += 2
    if "GOOD_RR" in candidate.detector_tags:
        rank += 3
    if "TIGHT_RISK" in candidate.detector_tags:
        rank += 2
    if "OVEREXTENDED" in candidate.warning_tags:
        rank -= 6
    elif "CHASE_RISK" in candidate.warning_tags:
        rank -= 3
    if "FAILED_BREAKOUT" in candidate.warning_tags:
        rank -= 8
    if candidate.reject_reason:
        rank -= 100
    return round(rank, 2)


def _finalize_candidate(candidate: DetectorCandidate) -> DetectorCandidate:
    """Apply tag-based retention and obvious reject logic."""
    candidate.setup_family = _select_setup_family(candidate)
    meaningful_hits = [
        hit
        for hit in candidate.hits
        if hit.name not in WARNING_ONLY_DETECTORS and hit.name not in LOW_SIGNAL_DETECTORS
    ]
    medium_or_better_hits = [
        hit
        for hit in meaningful_hits
        if CONFIDENCE_ORDER.get(hit.confidence, 0) >= CONFIDENCE_ORDER[CONFIDENCE_MEDIUM]
    ]
    fresh_catalyst_or_breakout = bool(
        candidate.detector_tags
        & {
            "CATALYST_GAP",
            "POWER_EARNINGS_GAP",
            "HIGH_VOLUME_BREAKOUT",
            "DAILY_BREAKOUT_CONFIRMED",
            "BREAKOUT_RETEST",
            "FAILED_BREAKDOWN_RECLAIM",
        }
    )

    if candidate.avg_volume is not None and candidate.avg_volume < 300_000:
        candidate.reject_reason = "illiquid_after_primary_gate_context"
    elif "BREAKOUT_FAILURE" in candidate.warning_tags and "HEAVY_RED_VOLUME" in candidate.warning_tags:
        candidate.reject_reason = "major_failed_breakout_or_heavy_red_breakdown"
    elif (
        "OVEREXTENDED" in candidate.warning_tags
        and "CHASE_RISK" in candidate.warning_tags
        and not fresh_catalyst_or_breakout
    ):
        candidate.reject_reason = "very_extended_without_fresh_catalyst"
    elif not meaningful_hits and not candidate.high_value_tags:
        candidate.reject_reason = "no_meaningful_setup_tags"
    elif (
        "NO_CLEAR_TRIGGER" in candidate.warning_tags
        and not candidate.high_value_tags
        and len(medium_or_better_hits) < 2
    ):
        candidate.reject_reason = "too_far_from_actionable_trigger"
    elif (
        "STOP_TOO_WIDE" in candidate.warning_tags
        and not candidate.high_value_tags
        and "GOOD_RR" not in candidate.detector_tags
    ):
        candidate.reject_reason = "stop_too_wide_without_nearby_invalidation"

    keep_by_high_value = bool(candidate.high_value_tags)
    keep_by_medium_cluster = len(medium_or_better_hits) >= 2
    keep_by_many_loose_hits = len(meaningful_hits) >= 3
    candidate.chart_needed = bool(
        not candidate.reject_reason
        and (keep_by_high_value or keep_by_medium_cluster or keep_by_many_loose_hits)
    )
    if not candidate.chart_needed and not candidate.reject_reason:
        candidate.reject_reason = "insufficient_detector_cluster"

    candidate.interest_rank = _interest_rank(candidate)
    if candidate.chart_needed:
        candidate.notes.append("Needs visual chart review; detector layer is not final setup approval.")
    return candidate


DETECTORS = [
    _detector_inside_day,
    _detector_tight_range_compression,
    _detector_big_base_near_highs,
    _detector_right_side_of_base,
    _detector_possible_accumulation_base,
    _detector_catalyst_gap,
    _detector_high_relative_volume,
    _detector_breakout_proximity,
    _detector_breakout_confirmed,
    _detector_breakout_retest,
    _detector_failed_breakdown_hammer,
    _detector_bull_flag_wedge,
    _detector_trend_alignment,
    _detector_extension,
    _detector_trigger_clarity,
    _detector_risk_reward,
    _detector_failed_breakout_warning,
]


def evaluate_setup_detectors(
    symbol: str, df: pd.DataFrame, metadata: Optional[dict[str, Any]] = None
) -> DetectorCandidate:
    """
    Run all loose detectors for one primary-gated symbol.

    Missing fields or insufficient history become an explicit rejected candidate
    instead of crashing the batch.
    """
    symbol_clean = str(symbol or "").strip().upper()
    if not symbol_clean:
        raise ValueError("symbol is required")
    metadata = metadata or {}

    try:
        frame = _prepare_frame(df)
        candidate = _current_candidate(symbol_clean, metadata, frame)
    except Exception as exc:
        candidate = DetectorCandidate(ticker=symbol_clean)
        candidate.reject_reason = f"detector_data_error: {exc}"
        candidate.source_error = str(exc)
        candidate.interest_rank = _interest_rank(candidate)
        return candidate

    for detector in DETECTORS:
        try:
            hit = detector(frame)
        except Exception as exc:
            candidate.notes.append(f"{detector.__name__} skipped: {exc}")
            continue
        if hit is not None:
            candidate.add_hit(hit)

    return _finalize_candidate(candidate)


def evaluate_detector_candidates(rows: Any) -> tuple[list[DetectorCandidate], dict[str, str]]:
    """
    Run setup detectors for a DataFrame/list of primary-gated rows.

    Rows are expected to include Symbol and preferably an `ohlcv` DataFrame.
    """
    if rows is None:
        return [], {}

    iterable: Iterable[Any]
    if isinstance(rows, pd.DataFrame):
        iterable = (row for _, row in rows.iterrows())
    else:
        iterable = list(rows)

    candidates: list[DetectorCandidate] = []
    failures: dict[str, str] = {}
    for row in iterable:
        symbol = str(_row_value(row, "Symbol", "ticker", "symbol") or "").strip().upper()
        if not symbol:
            continue
        df = _row_value(row, "ohlcv")
        metadata = row.to_dict() if hasattr(row, "to_dict") else dict(row or {})
        if not isinstance(df, pd.DataFrame):
            failure = "missing_ohlcv_for_detector_stage"
            failures[symbol] = failure
            candidate = DetectorCandidate(
                ticker=symbol,
                company=_clean_text(metadata.get("Company")),
                sector=_clean_text(metadata.get("Sector")),
                reject_reason=failure,
                source_error=failure,
            )
            candidate.interest_rank = _interest_rank(candidate)
            candidates.append(candidate)
            continue

        candidate = evaluate_setup_detectors(symbol, df, metadata=metadata)
        if candidate.source_error:
            failures[symbol] = candidate.source_error
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            bool(item.chart_needed),
            item.interest_rank,
            item.detector_count,
            item.rel_volume or 0,
        ),
        reverse=True,
    )
    return candidates, failures

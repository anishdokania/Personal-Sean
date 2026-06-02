"""
Technical analysis engine for the trading_system scanner.

Module 4 converts clean OHLCV data into deterministic blueprint features that
later modules can consume. It does not call AI services, generate reports, or
orchestrate scans.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd


REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
RegimeValue = Union[float, str, None]
VolumeBiasValue = Union[float, str, None]


def _validate_ohlcv(df: pd.DataFrame) -> None:
    """Validate that input data has the minimum columns needed for analysis."""
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame.")

    if df.empty:
        raise ValueError("Input OHLCV DataFrame is empty.")

    missing_columns = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required OHLCV columns: {', '.join(missing_columns)}")


def _prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean numeric copy of the OHLCV columns."""
    _validate_ohlcv(df)

    cleaned = df.copy()
    for column in REQUIRED_OHLCV_COLUMNS:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned = cleaned.dropna(subset=REQUIRED_OHLCV_COLUMNS)
    if cleaned.empty:
        raise ValueError("Input OHLCV DataFrame has no clean numeric rows.")

    return cleaned


def _safe_float(value: Any) -> Optional[float]:
    """Convert numeric values to plain floats and use None for invalid values."""
    if pd.isna(value) or not np.isfinite(value):
        return None

    return float(value)


def _format_date(value: Any) -> Optional[str]:
    """Format DataFrame index values into readable date strings."""
    if pd.isna(value):
        return None

    if hasattr(value, "date"):
        return value.date().isoformat()

    return str(value)


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add EMA/SMA and average-volume columns used by the blueprint rules.
    """
    analyzed = _prepare_ohlcv(df)

    analyzed["EMA8"] = analyzed["Close"].ewm(span=8, adjust=False).mean()
    analyzed["EMA21"] = analyzed["Close"].ewm(span=21, adjust=False).mean()
    analyzed["EMA50"] = analyzed["Close"].ewm(span=50, adjust=False).mean()
    analyzed["SMA20"] = analyzed["Close"].rolling(window=20, min_periods=20).mean()
    analyzed["AvgVolume20"] = analyzed["Volume"].rolling(window=20, min_periods=20).mean()

    return analyzed


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculate Average True Range and add an ATR column.
    """
    if period <= 0:
        raise ValueError("ATR period must be a positive integer.")

    analyzed = _prepare_ohlcv(df)

    previous_close = analyzed["Close"].shift(1)
    true_range = pd.concat(
        [
            analyzed["High"] - analyzed["Low"],
            (analyzed["High"] - previous_close).abs(),
            (analyzed["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_column = f"ATR{period}"
    analyzed[atr_column] = true_range.rolling(window=period, min_periods=period).mean()

    return analyzed


def _ensure_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Add moving-average columns if the caller passed raw OHLCV data."""
    required_columns = {"EMA8", "EMA21", "EMA50", "SMA20", "AvgVolume20"}
    if required_columns.issubset(df.columns):
        return _prepare_ohlcv(df)

    return add_moving_averages(df)


def _ensure_atr(df: pd.DataFrame) -> pd.DataFrame:
    """Add ATR14 if the caller passed data without it."""
    with_mas = _ensure_moving_averages(df)
    if "ATR14" in with_mas.columns:
        return with_mas

    with_atr = calculate_atr(with_mas, period=14)
    for column in ["EMA8", "EMA21", "EMA50", "SMA20", "AvgVolume20"]:
        if column in with_mas.columns:
            with_atr[column] = with_mas[column]

    return with_atr


def detect_ema_regime(df: pd.DataFrame) -> Dict[str, RegimeValue]:
    """
    Classify the latest close relative to the 8/21/50 EMA stack.
    """
    analyzed = _ensure_moving_averages(df)
    latest = analyzed.iloc[-1]

    close = float(latest["Close"])
    ema8 = float(latest["EMA8"])
    ema21 = float(latest["EMA21"])
    ema50 = float(latest["EMA50"])

    if close > ema8 > ema21 > ema50:
        regime = "strong_bullish"
    elif close > ema21 and close > ema50:
        regime = "bullish"
    elif close < ema8 < ema21 < ema50:
        regime = "strong_bearish"
    elif close < ema21 and close < ema50:
        regime = "bearish"
    else:
        regime = "mixed"

    return {
        "close": close,
        "ema8": ema8,
        "ema21": ema21,
        "ema50": ema50,
        "regime": regime,
    }


def detect_ignition_candle(
    df: pd.DataFrame, lookback: int = 20
) -> Dict[str, Any]:
    """
    Detect the most recent high-volume directional ignition candle.
    """
    if lookback <= 0:
        raise ValueError("lookback must be a positive integer.")

    analyzed = _ensure_moving_averages(df)
    recent = analyzed.tail(lookback)

    for date, row in reversed(list(recent.iterrows())):
        candle_range = row["High"] - row["Low"]
        if candle_range <= 0 or pd.isna(row["AvgVolume20"]) or row["AvgVolume20"] <= 0:
            continue

        body = abs(row["Close"] - row["Open"])
        body_to_range = body / candle_range
        relative_volume = row["Volume"] / row["AvgVolume20"]

        direction: Optional[str] = None
        if row["Close"] > row["Open"]:
            direction = "bullish"
        elif row["Close"] < row["Open"]:
            direction = "bearish"

        if direction and body_to_range >= 0.6 and relative_volume >= 2.0:
            return {
                "found": True,
                "direction": direction,
                "date": _format_date(date),
                "relative_volume": _safe_float(relative_volume),
                "body_to_range": _safe_float(body_to_range),
                "close": _safe_float(row["Close"]),
                "volume": _safe_float(row["Volume"]),
            }

    return {
        "found": False,
        "direction": None,
        "date": None,
        "relative_volume": None,
        "body_to_range": None,
        "close": None,
        "volume": None,
    }


def detect_volume_anomalies(
    df: pd.DataFrame, lookback: int = 20
) -> List[Dict[str, Any]]:
    """
    Detect simple spread/volume mismatches from the recent lookback window.
    """
    if lookback <= 0:
        raise ValueError("lookback must be a positive integer.")

    analyzed = _ensure_atr(df)
    recent = analyzed.tail(lookback)
    anomalies: List[Dict[str, Any]] = []

    for date, row in recent.iterrows():
        if (
            pd.isna(row["ATR14"])
            or row["ATR14"] <= 0
            or pd.isna(row["AvgVolume20"])
            or row["AvgVolume20"] <= 0
        ):
            continue

        candle_range = row["High"] - row["Low"]
        range_vs_atr = candle_range / row["ATR14"]
        relative_volume = row["Volume"] / row["AvgVolume20"]

        anomaly_type: Optional[str] = None
        if range_vs_atr >= 1.5 and relative_volume <= 0.7:
            anomaly_type = "wide_spread_low_volume"
        elif range_vs_atr <= 0.5 and relative_volume >= 1.5:
            anomaly_type = "narrow_spread_high_volume"

        if anomaly_type:
            anomalies.append(
                {
                    "date": _format_date(date),
                    "type": anomaly_type,
                    "range_vs_atr": _safe_float(range_vs_atr),
                    "relative_volume": _safe_float(relative_volume),
                    "close": _safe_float(row["Close"]),
                }
            )

    return anomalies


def detect_accumulation_distribution(
    df: pd.DataFrame, lookback: int = 20
) -> Dict[str, VolumeBiasValue]:
    """
    Estimate accumulation or distribution using up/down volume and OBV trend.
    """
    if lookback <= 0:
        raise ValueError("lookback must be a positive integer.")

    analyzed = _prepare_ohlcv(df)
    recent = analyzed.tail(lookback)

    up_days = recent[recent["Close"] > recent["Open"]]
    down_days = recent[recent["Close"] < recent["Open"]]

    up_day_avg_volume = up_days["Volume"].mean() if not up_days.empty else np.nan
    down_day_avg_volume = down_days["Volume"].mean() if not down_days.empty else np.nan

    close_change = analyzed["Close"].diff()
    obv_delta = np.where(
        close_change > 0,
        analyzed["Volume"],
        np.where(close_change < 0, -analyzed["Volume"], 0),
    )
    obv = pd.Series(obv_delta, index=analyzed.index).cumsum()

    if len(obv) <= lookback:
        comparison_obv = obv.iloc[0]
    else:
        comparison_obv = obv.iloc[-lookback - 1]

    latest_obv = obv.iloc[-1]
    if latest_obv > comparison_obv:
        obv_trend = "rising"
    elif latest_obv < comparison_obv:
        obv_trend = "falling"
    else:
        obv_trend = "flat"

    up_volume = _safe_float(up_day_avg_volume)
    down_volume = _safe_float(down_day_avg_volume)
    volume_bias = "neutral"

    if (
        up_volume is not None
        and down_volume is not None
        and up_volume > down_volume * 1.2
        and obv_trend == "rising"
    ):
        volume_bias = "accumulation"
    elif (
        up_volume is not None
        and down_volume is not None
        and down_volume > up_volume * 1.2
        and obv_trend == "falling"
    ):
        volume_bias = "distribution"

    return {
        "up_day_avg_volume": up_volume,
        "down_day_avg_volume": down_volume,
        "volume_bias": volume_bias,
        "obv_trend": obv_trend,
    }


def find_support_resistance(
    df: pd.DataFrame, lookback: int = 60, window: int = 3
) -> Dict[str, List[float]]:
    """
    Find recent swing-low support and swing-high resistance levels.
    """
    if lookback <= 0:
        raise ValueError("lookback must be a positive integer.")
    if window <= 0:
        raise ValueError("window must be a positive integer.")

    analyzed = _prepare_ohlcv(df)
    recent = analyzed.tail(lookback)

    support_levels: List[float] = []
    resistance_levels: List[float] = []

    for idx in range(window, len(recent) - window):
        low = recent["Low"].iloc[idx]
        high = recent["High"].iloc[idx]
        surrounding_lows = pd.concat(
            [recent["Low"].iloc[idx - window : idx], recent["Low"].iloc[idx + 1 : idx + window + 1]]
        )
        surrounding_highs = pd.concat(
            [
                recent["High"].iloc[idx - window : idx],
                recent["High"].iloc[idx + 1 : idx + window + 1],
            ]
        )

        if low < surrounding_lows.min():
            support_levels.append(float(low))
        if high > surrounding_highs.max():
            resistance_levels.append(float(high))

    return {
        "support_levels": support_levels[-5:],
        "resistance_levels": resistance_levels[-5:],
    }


def detect_supply_demand_zones(
    df: pd.DataFrame, lookback: int = 60
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Detect simple demand/supply zones preceding strong directional candles.
    """
    if lookback <= 1:
        raise ValueError("lookback must be greater than 1.")

    analyzed = _ensure_moving_averages(df)
    recent = analyzed.tail(lookback)
    demand_zones: List[Dict[str, Any]] = []
    supply_zones: List[Dict[str, Any]] = []

    for idx in range(1, len(recent)):
        row = recent.iloc[idx]
        previous = recent.iloc[idx - 1]
        previous_date = recent.index[idx - 1]

        candle_range = row["High"] - row["Low"]
        if candle_range <= 0 or pd.isna(row["AvgVolume20"]) or row["AvgVolume20"] <= 0:
            continue

        body_to_range = abs(row["Close"] - row["Open"]) / candle_range
        high_volume = row["Volume"] > row["AvgVolume20"] * 1.5

        if body_to_range < 0.6 or not high_volume:
            continue

        zone = {
            "date": _format_date(previous_date),
            "low": _safe_float(previous["Low"]),
            "high": _safe_float(previous["High"]),
        }

        if row["Close"] > row["Open"]:
            demand_zones.append(zone)
        elif row["Close"] < row["Open"]:
            supply_zones.append(zone)

    return {
        "demand_zones": demand_zones[-3:],
        "supply_zones": supply_zones[-3:],
    }


def analyze_stock_technicals(symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
    """
    Run the full Module 4 technical analysis stack for one stock.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string.")

    analyzed = add_moving_averages(df)
    analyzed = calculate_atr(analyzed, period=14)

    return {
        "symbol": symbol.strip().upper(),
        "ema_regime": detect_ema_regime(analyzed),
        "ignition_candle": detect_ignition_candle(analyzed),
        "volume_anomalies": detect_volume_anomalies(analyzed),
        "accumulation_distribution": detect_accumulation_distribution(analyzed),
        "support_resistance": find_support_resistance(analyzed),
        "supply_demand_zones": detect_supply_demand_zones(analyzed),
    }


if __name__ == "__main__":
    from data_fetcher import fetch_stock_data

    symbols = ["NVDA", "AAPL", "MSFT"]

    for symbol in symbols:
        print(f"\n=== {symbol} ===")

        try:
            stock_data = fetch_stock_data(symbol, period="6mo", interval="1d")
            technicals = analyze_stock_technicals(symbol, stock_data)
        except Exception as exc:
            print(f"Technical analysis failed for {symbol}: {exc}")
            continue

        print("EMA regime:")
        print(technicals["ema_regime"])
        print("Ignition candle:")
        print(technicals["ignition_candle"])
        print("Accumulation/distribution:")
        print(technicals["accumulation_distribution"])
        print("Support/resistance:")
        print(technicals["support_resistance"])
        print("Supply/demand zones:")
        print(technicals["supply_demand_zones"])
        print(f"Volume anomalies count: {len(technicals['volume_anomalies'])}")

"""
End-to-end MVP orchestrator for the trading_system scanner.

Module 7 connects the completed modules into a decision-support workflow:
sector scan, stock filtering, deterministic pre-AI selection, Claude analysis,
and Markdown report generation. It does not place orders or auto-trade.
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from claude_analyzer import (
    analyze_setup_with_claude,
    analyze_with_claude,
    build_failed_analysis,
)
from data_fetcher import fetch_stock_data
from detector_models import DetectorCandidate
from detector_report import save_detector_outputs
from setup_detectors import evaluate_detector_candidates
from focus_structure import evaluate_focus_structure
from report import (
    format_sector_leadership_section,
    format_market_context_section,
    generate_and_save_report,
    save_report,
)
from sector_scanner import rank_sectors
from stock_filter import scan_candidates
from technical import analyze_stock_technicals, calculate_atr
from today_focus import evaluate_today_focus


MAX_AI_ANALYSES = 10
MAX_CANDIDATES_TO_SCORE = None
PRIMARY_GATE_MIN_PRICE = 5.0
PRIMARY_GATE_MIN_MARKET_CAP = 300_000_000
PRIMARY_GATE_MIN_ATR14 = 1.5
PRIMARY_GATE_MIN_AVG_VOLUME = 1_000_000
# Default scans the broad U.S.-listed common-stock universe so discovery is not
# limited to S&P 500 names.
# For broad-universe testing, set MAX_UNIVERSE_SIZE = 300.
# For a full broad-universe scan, set MAX_UNIVERSE_SIZE = None.
UNIVERSE_MODE = "us_listed"
MAX_UNIVERSE_SIZE = None
USE_VISION_REVIEW = False
MAX_VISION_REVIEWS = 5
GENERATE_DETECTOR_CHARTS = False
DETECTOR_CHART_LIMIT = None
MARKET_INDEXES = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
}
AUDIT_CANDIDATE_FIELDS = [
    "Symbol",
    "Company",
    "Sector",
    "Exchange",
    "price",
    "sma21",
    "sma50",
    "market_cap",
    "atr",
    "avg_volume",
    "passes_price_sma21",
    "passes_price_sma50",
    "passes_price_min",
    "passes_market_cap",
    "passes_atr",
    "passes_avg_volume",
    "primary_gate_pass",
    "primary_gate_fail_reasons",
    "sector_etf",
    "sector_perf_1w",
    "sector_perf_1m",
    "sector_perf_3m",
    "sector_perf_6m",
    "sector_perf_1y",
    "sector_score",
    "sector_rank",
    "stock_perf_1w",
    "stock_perf_1m",
    "stock_perf_3m",
    "relative_strength_1m",
    "relative_strength_3m",
    "SectorAlignmentScore",
    "TechnicalPreAIScore",
    "TodayFocusScore",
    "FocusStructureScore",
    "BlueprintSetupScore",
    "BlueprintFitScore",
    "BlueprintFitPass",
    "BlueprintFitFailReasons",
    "FinalPreAIScore",
    "Actionability",
    "StructureType",
    "BlueprintSetupType",
    "TriggerLevel",
    "InvalidationLevel",
    "DoNotChaseAbove",
    "PreferredEntryStyle",
    "ClassificationReason",
    "GateFailureReasons",
]
SECTOR_ALIASES = {
    "technology": "Technology",
    "information technology": "Technology",
    "energy": "Energy",
    "communication services": "Communication Services",
    "communications": "Communication Services",
    "industrials": "Industrials",
    "industrial": "Industrials",
    "consumer discretionary": "Consumer Discretionary",
    "consumer cyclical": "Consumer Discretionary",
    "consumer cyclical / discretionary": "Consumer Discretionary",
    "financial": "Financials",
    "financials": "Financials",
    "financial services": "Financials",
    "real estate": "Real Estate",
    "basic materials": "Materials",
    "materials": "Materials",
    "utilities": "Utilities",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "consumer defensive / staples": "Consumer Staples",
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


def _as_bool(value: Any) -> bool:
    """Interpret common boolean-like values safely."""
    if isinstance(value, bool):
        return value

    if value is None or pd.isna(value):
        return False

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}

    return bool(value)


def _row_value(row: Any, key: str) -> Any:
    """Read a value from a pandas row or dictionary-like object."""
    if hasattr(row, "get"):
        return row.get(key)

    return None


def _format_pct(value: Any) -> str:
    """Format a diagnostic percentage value for concise terminal output."""
    numeric_value = _as_float(value)
    if numeric_value is None:
        return "N/A"

    return f"{numeric_value:.1f}%"


def _format_today_diagnostics(today_focus: Any) -> str:
    """Return a short diagnostics summary for selected-candidate printouts."""
    if not isinstance(today_focus, dict):
        return "diagnostics unavailable"

    diagnostics = today_focus.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return "diagnostics unavailable"

    trigger = (
        _format_pct(diagnostics.get("nearest_resistance_distance_pct"))
        if diagnostics.get("has_nearby_trigger")
        else "none"
    )
    invalidation_far = not bool(diagnostics.get("has_reasonable_invalidation"))

    return (
        f"ext8: {_format_pct(diagnostics.get('pct_above_ema8'))}, "
        f"ext21: {_format_pct(diagnostics.get('pct_above_ema21'))}, "
        f"trigger: {trigger}, "
        f"invalidation_far: {str(invalidation_far).lower()}"
    )


def _join_items(value: Any) -> str:
    """Format list-like audit values as readable semicolon-delimited text."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, tuple):
        return "; ".join(str(item) for item in value)

    return str(value)


def _nested_value(mapping: Any, *keys: str) -> Any:
    """Safely read a nested dictionary value."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def _clean_audit_value(value: Any) -> Any:
    """Return CSV values as plain Python values, converting NaN to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        text_value = value.strip()
        return text_value if text_value else None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value


def _has_clean_value(value: Any) -> bool:
    """Return True when a value is not empty/NaN."""
    return _clean_audit_value(value) is not None


def _normalize_sector(value: Any) -> Optional[str]:
    """Normalize sector/industry names to the ETF proxy sector labels."""
    if value is None:
        return None

    text_value = str(value).strip()
    if not text_value:
        return None

    normalized = " ".join(text_value.lower().replace("&", "and").split())
    return SECTOR_ALIASES.get(normalized)


def _metadata_get(mapping: Any, key: str) -> Any:
    """Read yfinance fast_info/info values across object/dict variants."""
    if mapping is None:
        return None
    if hasattr(mapping, "get"):
        try:
            return mapping.get(key)
        except Exception:
            pass

    return getattr(mapping, key, None)


def fetch_symbol_metadata(
    symbol: str, include_profile: bool = False
) -> dict[str, Any]:
    """Fetch minimal yfinance metadata used by the primary hard gate."""
    metadata = {"market_cap": None, "sector": None, "industry": None}
    symbol_clean = str(symbol or "").strip().upper()
    if not symbol_clean:
        return metadata

    try:
        ticker = yf.Ticker(symbol_clean)
    except Exception:
        return metadata

    try:
        fast_info = getattr(ticker, "fast_info", None)
        metadata["market_cap"] = _as_float(_metadata_get(fast_info, "market_cap"))
    except Exception:
        pass

    if metadata["market_cap"] is not None and not include_profile:
        return metadata

    if metadata["market_cap"] is None or include_profile:
        try:
            info = getattr(ticker, "info", None)
            if isinstance(info, dict):
                metadata["market_cap"] = metadata["market_cap"] or _as_float(
                    info.get("marketCap")
                )
                metadata["sector"] = info.get("sector")
                metadata["industry"] = info.get("industry")
        except Exception:
            pass

    return metadata


def _latest_rolling_mean(series: Any, window: int) -> Optional[float]:
    """Return the latest rolling mean for a clean numeric series."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < window:
        return None

    return _as_float(values.tail(window).mean())


def _pct_change_over_bars(series: Any, bars: int) -> Optional[float]:
    """Return percentage change from roughly N trading bars ago to latest."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if bars <= 0 or len(values) <= bars:
        return None

    start_value = _as_float(values.iloc[-bars - 1])
    end_value = _as_float(values.iloc[-1])
    if start_value is None or end_value is None or start_value <= 0:
        return None

    return ((end_value / start_value) - 1) * 100


def _latest_atr14(df: pd.DataFrame) -> Optional[float]:
    """Return latest ATR14 from an OHLCV frame when calculable."""
    try:
        atr_frame = calculate_atr(df, period=14)
        if "ATR14" not in atr_frame.columns or atr_frame.empty:
            return None
        return _as_float(atr_frame["ATR14"].iloc[-1])
    except Exception:
        return None


def map_symbol_to_sector_etf(
    symbol: str, sector_or_industry: Any
) -> tuple[Optional[str], Optional[str]]:
    """Map sector/industry metadata to the Option C sector ETF proxy."""
    sector_name = _normalize_sector(sector_or_industry)
    if sector_name is None:
        return None, None

    sector_to_etf = {
        "Technology": "XLK",
        "Energy": "XLE",
        "Communication Services": "XLC",
        "Industrials": "XLI",
        "Consumer Discretionary": "XLY",
        "Financials": "XLF",
        "Real Estate": "XLRE",
        "Materials": "XLB",
        "Utilities": "XLU",
        "Healthcare": "XLV",
        "Consumer Staples": "XLP",
    }
    return sector_name, sector_to_etf.get(sector_name)


def _sector_lookup(sector_table: Any) -> dict[str, dict[str, Any]]:
    """Build ETF-keyed sector leadership lookup records."""
    if not isinstance(sector_table, pd.DataFrame) or sector_table.empty:
        return {}

    lookup: dict[str, dict[str, Any]] = {}
    for _, row in sector_table.iterrows():
        etf = str(row.get("ETF") or "").strip().upper()
        if not etf:
            continue
        lookup[etf] = {
            "sector": row.get("Sector"),
            "etf": etf,
            "rank": _as_float(row.get("SectorRank")),
            "score": _as_float(row.get("Score")),
            "perf_1w": _as_float(row.get("1W_Return")),
            "perf_1m": _as_float(row.get("1M_Return")),
            "perf_3m": _as_float(row.get("3M_Return")),
            "perf_6m": _as_float(row.get("6M_Return")),
            "perf_1y": _as_float(row.get("1Y_Return")),
        }

    return lookup


def sector_leadership_records(sector_table: Any) -> list[dict[str, Any]]:
    """Convert sector leadership table into report-friendly records."""
    lookup = _sector_lookup(sector_table)
    records = list(lookup.values())
    return sorted(
        records,
        key=lambda item: item["rank"] if item.get("rank") is not None else float("inf"),
    )


def load_sector_leadership_table() -> pd.DataFrame:
    """Calculate sector ETF proxy leadership without crashing the scan."""
    try:
        table = rank_sectors()
    except Exception as exc:
        print(f"Sector leadership unavailable: {exc}", flush=True)
        return pd.DataFrame()

    if table.empty:
        print("Sector leadership unavailable: no ETF data returned.", flush=True)
    else:
        print(f"Sector leadership calculated for {len(table)} sector ETFs.", flush=True)

    if table.attrs.get("errors"):
        print(f"Sector leadership skipped ETFs: {table.attrs['errors']}", flush=True)

    return table


def evaluate_market_context() -> dict[str, Any]:
    """Evaluate broad-market trend context using index ETF EMA regimes."""
    regime_scores = {
        "strong_bullish": 100,
        "bullish": 75,
        "mixed": 50,
        "bearish": 25,
        "strong_bearish": 0,
    }
    index_records: list[dict[str, Any]] = []
    warnings: list[str] = []

    for symbol, name in MARKET_INDEXES.items():
        try:
            df = fetch_stock_data(symbol, period="6mo", interval="1d")
            technicals = analyze_stock_technicals(symbol, df)
            ema_regime = technicals.get("ema_regime")
            if not isinstance(ema_regime, dict):
                raise ValueError("missing EMA regime")
            regime = str(ema_regime.get("regime") or "mixed").strip().lower()
            score = regime_scores.get(regime, 50)
            index_records.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "regime": regime,
                    "score": score,
                    "close": _as_float(ema_regime.get("close")),
                    "ema8": _as_float(ema_regime.get("ema8")),
                    "ema21": _as_float(ema_regime.get("ema21")),
                    "ema50": _as_float(ema_regime.get("ema50")),
                }
            )
        except Exception as exc:
            warnings.append(f"{symbol} market context unavailable: {exc}")

    if not index_records:
        return {
            "market_score": None,
            "market_bias": "unknown",
            "indexes": [],
            "warnings": warnings or ["Market context unavailable."],
        }

    market_score = sum(float(item["score"]) for item in index_records) / len(index_records)
    if market_score >= 75:
        market_bias = "risk_on"
    elif market_score >= 50:
        market_bias = "mixed_constructive"
    elif market_score >= 25:
        market_bias = "mixed_defensive"
    else:
        market_bias = "risk_off"

    if market_bias in {"mixed_defensive", "risk_off"}:
        warnings.append("Broad-market EMA context is not supportive for aggressive long focus.")

    return {
        "market_score": market_score,
        "market_bias": market_bias,
        "indexes": index_records,
        "warnings": warnings,
    }


def attach_sector_leadership(
    candidate: dict[str, Any], sector_table: Any
) -> dict[str, Any]:
    """Attach sector ETF/rank/score context when sector metadata is available."""
    enriched = dict(candidate)
    sector_value = (
        enriched.get("Sector")
        or enriched.get("sector")
        or enriched.get("Industry")
        or enriched.get("industry")
    )
    sector_name, sector_etf = map_symbol_to_sector_etf(
        str(enriched.get("Symbol") or ""), sector_value
    )
    lookup = _sector_lookup(sector_table)
    sector_record = lookup.get(str(sector_etf or "").upper(), {})

    if sector_name and not enriched.get("Sector"):
        enriched["Sector"] = sector_name

    enriched.update(
        {
            "sector_name": sector_name,
            "sector_etf": sector_etf,
            "sector_rank": sector_record.get("rank"),
            "sector_score": sector_record.get("score"),
            "sector_perf_1w": sector_record.get("perf_1w"),
            "sector_perf_1m": sector_record.get("perf_1m"),
            "sector_perf_3m": sector_record.get("perf_3m"),
            "sector_perf_6m": sector_record.get("perf_6m"),
            "sector_perf_1y": sector_record.get("perf_1y"),
        }
    )
    return enriched


def _evaluate_primary_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    """Evaluate primary hard-universe gate checks and explicit failure reasons."""
    price = _as_float(metrics.get("price"))
    sma21 = _as_float(metrics.get("sma21"))
    sma50 = _as_float(metrics.get("sma50"))
    market_cap = _as_float(metrics.get("market_cap"))
    atr = _as_float(metrics.get("atr"))
    avg_volume = _as_float(metrics.get("avg_volume"))

    fail_reasons: list[str] = []
    passes_price_sma21 = price is not None and sma21 is not None and price > sma21
    passes_price_sma50 = price is not None and sma50 is not None and price > sma50
    passes_price_min = price is not None and price > PRIMARY_GATE_MIN_PRICE
    passes_market_cap = (
        market_cap is not None and market_cap > PRIMARY_GATE_MIN_MARKET_CAP
    )
    passes_atr = atr is not None and atr > PRIMARY_GATE_MIN_ATR14
    passes_avg_volume = (
        avg_volume is not None and avg_volume > PRIMARY_GATE_MIN_AVG_VOLUME
    )

    if price is None:
        fail_reasons.append("missing_price")
    if sma21 is None:
        fail_reasons.append("missing_sma21")
    elif price is not None and price <= sma21:
        fail_reasons.append("price_below_21sma")
    if sma50 is None:
        fail_reasons.append("missing_sma50")
    elif price is not None and price <= sma50:
        fail_reasons.append("price_below_50sma")
    if price is not None and price <= PRIMARY_GATE_MIN_PRICE:
        fail_reasons.append("price_below_5")
    if market_cap is None:
        fail_reasons.append("missing_market_cap")
    elif market_cap <= PRIMARY_GATE_MIN_MARKET_CAP:
        fail_reasons.append("market_cap_below_300m")
    if atr is None:
        fail_reasons.append("missing_atr14")
    elif atr <= PRIMARY_GATE_MIN_ATR14:
        fail_reasons.append("atr14_below_1.5")
    if avg_volume is None:
        fail_reasons.append("missing_avg_volume")
    elif avg_volume <= PRIMARY_GATE_MIN_AVG_VOLUME:
        fail_reasons.append("avg_volume_below_1m")

    primary_gate_pass = bool(
        passes_price_sma21
        and passes_price_sma50
        and passes_price_min
        and passes_market_cap
        and passes_atr
        and passes_avg_volume
    )
    return {
        "passes_price_sma21": passes_price_sma21,
        "passes_price_sma50": passes_price_sma50,
        "passes_price_min": passes_price_min,
        "passes_market_cap": passes_market_cap,
        "passes_atr": passes_atr,
        "passes_avg_volume": passes_avg_volume,
        "primary_gate_pass": primary_gate_pass,
        "primary_gate_fail_reasons": _join_items(fail_reasons),
    }


def apply_primary_universe_gate(
    candidate: Any,
    sector_table: Any,
    ohlcv: Optional[pd.DataFrame] = None,
) -> dict[str, Any]:
    """Fetch/enrich one symbol and apply the primary hard universe gate."""
    row = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate or {})
    symbol = str(row.get("Symbol") or "").strip().upper()
    row["Symbol"] = symbol

    metadata = fetch_symbol_metadata(symbol)
    if not row.get("Sector"):
        row["Sector"] = metadata.get("sector") or row.get("Sector")
    if metadata.get("industry"):
        row["Industry"] = metadata.get("industry")

    row = attach_sector_leadership(row, sector_table)
    row["market_cap"] = _as_float(metadata.get("market_cap"))

    try:
        df = ohlcv if isinstance(ohlcv, pd.DataFrame) else fetch_stock_data(
            symbol, period="1y", interval="1d"
        )
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        volumes = pd.to_numeric(df["Volume"], errors="coerce").dropna()
        price = _as_float(closes.iloc[-1]) if not closes.empty else None
        sma20 = _latest_rolling_mean(closes, 20)
        sma21 = _latest_rolling_mean(closes, 21)
        sma50 = _latest_rolling_mean(closes, 50)
        avg_volume = _latest_rolling_mean(volumes, 20)
        atr = _latest_atr14(df)
        stock_perf_1w = _pct_change_over_bars(closes, 5)
        stock_perf_1m = _pct_change_over_bars(closes, 21)
        stock_perf_3m = _pct_change_over_bars(closes, 63)
        sector_perf_1m = _as_float(row.get("sector_perf_1m"))
        sector_perf_3m = _as_float(row.get("sector_perf_3m"))

        row.update(
            {
                "price": price,
                "sma21": sma21,
                "sma50": sma50,
                "atr": atr,
                "avg_volume": avg_volume,
                "Close": price,
                "SMA20": sma20,
                "AvgVolume20": avg_volume,
                "DollarVolume20": (
                    price * avg_volume
                    if price is not None and avg_volume is not None
                    else None
                ),
                "stock_perf_1w": stock_perf_1w,
                "stock_perf_1m": stock_perf_1m,
                "stock_perf_3m": stock_perf_3m,
                "relative_strength_1m": (
                    stock_perf_1m - sector_perf_1m
                    if stock_perf_1m is not None and sector_perf_1m is not None
                    else None
                ),
                "relative_strength_3m": (
                    stock_perf_3m - sector_perf_3m
                    if stock_perf_3m is not None and sector_perf_3m is not None
                    else None
                ),
                "AboveSMA20": (
                    bool(price > sma20)
                    if price is not None and sma20 is not None
                    else False
                ),
                "ohlcv": df,
            }
        )
        row.update(_evaluate_primary_gate(row))
        if _as_bool(row.get("primary_gate_pass")) and not row.get("sector_etf"):
            profile = fetch_symbol_metadata(symbol, include_profile=True)
            if profile.get("sector") and not row.get("Sector"):
                row["Sector"] = profile.get("sector")
            if profile.get("industry"):
                row["Industry"] = profile.get("industry")
            row = attach_sector_leadership(row, sector_table)
            stock_perf_1m = _as_float(row.get("stock_perf_1m"))
            stock_perf_3m = _as_float(row.get("stock_perf_3m"))
            sector_perf_1m = _as_float(row.get("sector_perf_1m"))
            sector_perf_3m = _as_float(row.get("sector_perf_3m"))
            row["relative_strength_1m"] = (
                stock_perf_1m - sector_perf_1m
                if stock_perf_1m is not None and sector_perf_1m is not None
                else None
            )
            row["relative_strength_3m"] = (
                stock_perf_3m - sector_perf_3m
                if stock_perf_3m is not None and sector_perf_3m is not None
                else None
            )
        return row
    except Exception as exc:
        row.update(
            {
                "price": None,
                "sma21": None,
                "sma50": None,
                "atr": None,
                "avg_volume": None,
                "passes_price_sma21": False,
                "passes_price_sma50": False,
                "passes_price_min": False,
                "passes_market_cap": False,
                "passes_atr": False,
                "passes_avg_volume": False,
                "primary_gate_pass": False,
                "primary_gate_fail_reasons": f"data_fetch_failed: {exc}",
            }
        )
        return row


def apply_primary_universe_gate_to_candidates(
    candidates_df: pd.DataFrame, sector_table: Any
) -> pd.DataFrame:
    """Apply the primary hard universe gate to every scanned candidate row."""
    if candidates_df is None or candidates_df.empty:
        empty = pd.DataFrame()
        empty.attrs["primary_gate_results"] = empty
        empty.attrs["primary_gate_survivor_count"] = 0
        empty.attrs["primary_gate_rejected_count"] = 0
        return empty

    total_count = len(candidates_df)
    gated_rows = []
    for idx, candidate in candidates_df.iterrows():
        symbol = str(candidate.get("Symbol") or "").strip().upper()
        print(f"Primary gate {idx + 1}/{total_count} {symbol}...", flush=True)
        gated_rows.append(apply_primary_universe_gate(candidate, sector_table))

    gated_results = pd.DataFrame(gated_rows)
    if gated_results.empty:
        gated_results.attrs["primary_gate_results"] = gated_results
        gated_results.attrs["primary_gate_survivor_count"] = 0
        gated_results.attrs["primary_gate_rejected_count"] = 0
        return gated_results

    survivors = gated_results[gated_results["primary_gate_pass"].apply(_as_bool)].copy()
    if not survivors.empty:
        survivors = survivors.sort_values(
            by=["DollarVolume20", "avg_volume", "price"],
            ascending=[False, False, False],
        ).reset_index(drop=True)

    survivors.attrs["primary_gate_results"] = gated_results.reset_index(drop=True)
    survivors.attrs["primary_gate_survivor_count"] = len(survivors)
    survivors.attrs["primary_gate_rejected_count"] = total_count - len(survivors)
    return survivors


def get_focus_gate_failure_reasons(row: Any) -> list[str]:
    """Return specific reasons a scored row fails the focus quality gate."""
    failure_reasons: list[str] = []
    actionability = str(_row_value(row, "Actionability") or "").strip().lower()
    today_score = _as_float(_row_value(row, "TodayFocusScore")) or 0.0
    structure_score = _as_float(_row_value(row, "FocusStructureScore")) or 0.0
    blueprint_fit_score = _as_float(_row_value(row, "BlueprintFitScore")) or 0.0
    sector_alignment_score = _as_float(_row_value(row, "SectorAlignmentScore"))
    relative_strength_1m = _as_float(_row_value(row, "relative_strength_1m"))
    relative_strength_3m = _as_float(_row_value(row, "relative_strength_3m"))
    structure_type = str(_row_value(row, "StructureType") or "").strip().lower()

    if actionability not in {"ready_today", "breakout_only", "pullback_only"}:
        failure_reasons.append(f"actionability_not_allowed: {actionability or 'missing'}")
    if today_score < 70:
        failure_reasons.append(f"today_focus_score_below_70: {today_score:g}")
    if structure_score < 65:
        failure_reasons.append(f"focus_structure_score_below_65: {structure_score:g}")
    if blueprint_fit_score < 65:
        failure_reasons.append(f"blueprint_fit_score_below_65: {blueprint_fit_score:g}")
    if sector_alignment_score is not None and sector_alignment_score < 45:
        failure_reasons.append(
            f"sector_alignment_score_below_45: {sector_alignment_score:g}"
        )
    if relative_strength_1m is not None and relative_strength_1m <= -8:
        if relative_strength_3m is None or relative_strength_3m <= -12:
            failure_reasons.append(
                "stock_underperforming_sector: "
                f"1m {relative_strength_1m:g}, "
                f"3m {relative_strength_3m:g}"
            )
    if structure_type in {"extended_no_base", "sloppy_chop", "no_clear_structure"}:
        failure_reasons.append(f"rejected_structure_type: {structure_type}")

    today_focus = _row_value(row, "today_focus")
    focus_structure = _row_value(row, "focus_structure")
    today_diagnostics = (
        today_focus.get("diagnostics")
        if isinstance(today_focus, dict) and isinstance(today_focus.get("diagnostics"), dict)
        else {}
    )
    structure_diagnostics = (
        focus_structure.get("diagnostics")
        if isinstance(focus_structure, dict)
        and isinstance(focus_structure.get("diagnostics"), dict)
        else {}
    )
    trigger_invalidation = structure_diagnostics.get("trigger_invalidation")
    if not isinstance(trigger_invalidation, dict):
        trigger_invalidation = {}

    blueprint_fit_pass = (
        focus_structure.get("blueprint_fit_pass")
        if isinstance(focus_structure, dict)
        else None
    )
    if blueprint_fit_pass is False:
        blueprint_reasons = focus_structure.get("blueprint_fit_fail_reasons")
        if isinstance(blueprint_reasons, list):
            for reason in blueprint_reasons:
                failure_reasons.append(f"blueprint_fit_failed: {reason}")
        else:
            failure_reasons.append("blueprint_fit_failed")

    trigger_nearby = bool(
        isinstance(focus_structure, dict) and focus_structure.get("trigger_nearby")
    )
    trigger_level_exists = bool(
        trigger_invalidation.get("trigger_level") is not None
        or (isinstance(today_focus, dict) and today_focus.get("trigger_level") is not None)
    )
    retest_path_exists = bool(
        today_diagnostics.get("has_nearby_retest_area")
        or (isinstance(focus_structure, dict) and focus_structure.get("controlled_digestion"))
        or (
            isinstance(focus_structure, dict)
            and focus_structure.get("invalidation_nearby")
            and focus_structure.get("holding_ema_structure")
        )
    )
    invalidation_exists = bool(
        (isinstance(focus_structure, dict) and focus_structure.get("invalidation_nearby"))
        or trigger_invalidation.get("invalidation_level") is not None
        or (isinstance(today_focus, dict) and today_focus.get("invalidation_level") is not None)
    )

    extension_risk = (
        str(focus_structure.get("extension_risk", "")).strip().lower()
        if isinstance(focus_structure, dict)
        else ""
    )
    severely_extended_without_digestion = bool(
        extension_risk == "high"
        and not (
            isinstance(focus_structure, dict)
            and focus_structure.get("controlled_digestion")
        )
    )
    if severely_extended_without_digestion:
        failure_reasons.append("severe_extension_without_digestion")

    if not trigger_level_exists and not retest_path_exists:
        failure_reasons.append("no_trigger_or_retest_path")

    if actionability == "pullback_only":
        if structure_score < 70:
            failure_reasons.append(f"pullback_only_structure_below_70: {structure_score:g}")
        if not retest_path_exists:
            failure_reasons.append("pullback_only_missing_retest_path")
    elif actionability == "breakout_only":
        if not (trigger_nearby or trigger_level_exists):
            failure_reasons.append("breakout_only_missing_trigger")
    elif actionability == "ready_today":
        if structure_score < 70:
            failure_reasons.append(f"ready_today_structure_below_70: {structure_score:g}")
        if not invalidation_exists:
            failure_reasons.append("ready_today_missing_invalidation")

    focus_disqualifiers = (
        focus_structure.get("disqualifiers")
        if isinstance(focus_structure, dict)
        and isinstance(focus_structure.get("disqualifiers"), list)
        else []
    )
    for disqualifier in focus_disqualifiers:
        failure_reasons.append(f"structure_disqualifier: {disqualifier}")

    return failure_reasons


def passes_focus_quality_gate(row: Any) -> bool:
    """Return True only when a scored row qualifies for Claude review."""
    return len(get_focus_gate_failure_reasons(row)) == 0


def build_focus_gate_audit_rows(scored_candidates: Any) -> list[dict[str, Any]]:
    """Build CSV-ready audit rows for every technically/focus scored candidate."""
    if scored_candidates is None:
        return []

    if isinstance(scored_candidates, pd.DataFrame):
        rows = [row for _, row in scored_candidates.iterrows()]
    else:
        rows = list(scored_candidates or [])

    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        today_focus = _row_value(row, "today_focus")
        if not isinstance(today_focus, dict):
            today_focus = {}

        today_diagnostics = today_focus.get("diagnostics")
        if not isinstance(today_diagnostics, dict):
            today_diagnostics = {}

        focus_structure = _row_value(row, "focus_structure")
        if not isinstance(focus_structure, dict):
            focus_structure = {}

        structure_diagnostics = focus_structure.get("diagnostics")
        if not isinstance(structure_diagnostics, dict):
            structure_diagnostics = {}

        digestion = structure_diagnostics.get("controlled_digestion")
        compression = structure_diagnostics.get("compression")
        trigger_invalidation = structure_diagnostics.get("trigger_invalidation")
        volume_dryup = structure_diagnostics.get("volume_dryup")
        ema_structure = structure_diagnostics.get("ema_structure")
        blueprint_setup = structure_diagnostics.get("blueprint_setup")
        if not isinstance(blueprint_setup, dict):
            blueprint_setup = {}
        blueprint_diagnostics = blueprint_setup.get("diagnostics")
        if not isinstance(blueprint_diagnostics, dict):
            blueprint_diagnostics = {}
        blueprint_setup_score = _row_value(row, "BlueprintSetupScore")
        if blueprint_setup_score is None:
            blueprint_setup_score = focus_structure.get("blueprint_setup_score")
        blueprint_fit_score = _row_value(row, "BlueprintFitScore")
        if blueprint_fit_score is None:
            blueprint_fit_score = focus_structure.get("blueprint_fit_score")

        primary_gate_present = _has_clean_value(_row_value(row, "primary_gate_pass"))
        primary_gate_pass = (
            _as_bool(_row_value(row, "primary_gate_pass"))
            if primary_gate_present
            else True
        )
        if primary_gate_present and not primary_gate_pass:
            primary_reason = _join_items(_row_value(row, "primary_gate_fail_reasons"))
            failure_reasons = [
                "primary_gate_failed"
                + (f": {primary_reason}" if primary_reason else "")
            ]
        elif (
            primary_gate_present
            and primary_gate_pass
            and _as_bool(_row_value(row, "primary_gate_audit_only"))
        ):
            failure_reasons = ["not_technically_scored_after_primary_gate"]
        else:
            failure_reasons = get_focus_gate_failure_reasons(row)

        audit_rows.append(
            {
                "Symbol": _row_value(row, "Symbol"),
                "Company": _row_value(row, "Company"),
                "Sector": _row_value(row, "Sector"),
                "Exchange": _row_value(row, "Exchange"),
                "price": _row_value(row, "price"),
                "sma21": _row_value(row, "sma21"),
                "sma50": _row_value(row, "sma50"),
                "market_cap": _row_value(row, "market_cap"),
                "atr": _row_value(row, "atr"),
                "avg_volume": _row_value(row, "avg_volume"),
                "passes_price_sma21": _row_value(row, "passes_price_sma21"),
                "passes_price_sma50": _row_value(row, "passes_price_sma50"),
                "passes_price_min": _row_value(row, "passes_price_min"),
                "passes_market_cap": _row_value(row, "passes_market_cap"),
                "passes_atr": _row_value(row, "passes_atr"),
                "passes_avg_volume": _row_value(row, "passes_avg_volume"),
                "primary_gate_pass": _row_value(row, "primary_gate_pass"),
                "primary_gate_fail_reasons": _row_value(
                    row, "primary_gate_fail_reasons"
                ),
                "sector_etf": _row_value(row, "sector_etf"),
                "sector_perf_1w": _row_value(row, "sector_perf_1w"),
                "sector_perf_1m": _row_value(row, "sector_perf_1m"),
                "sector_perf_3m": _row_value(row, "sector_perf_3m"),
                "sector_perf_6m": _row_value(row, "sector_perf_6m"),
                "sector_perf_1y": _row_value(row, "sector_perf_1y"),
                "sector_score": _row_value(row, "sector_score"),
                "sector_rank": _row_value(row, "sector_rank"),
                "stock_perf_1w": _row_value(row, "stock_perf_1w"),
                "stock_perf_1m": _row_value(row, "stock_perf_1m"),
                "stock_perf_3m": _row_value(row, "stock_perf_3m"),
                "relative_strength_1m": _row_value(row, "relative_strength_1m"),
                "relative_strength_3m": _row_value(row, "relative_strength_3m"),
                "SectorAlignmentScore": _row_value(row, "SectorAlignmentScore"),
                "BasicPreAIScore": _row_value(row, "PreAIScore"),
                "TechnicalPreAIScore": _row_value(row, "TechnicalPreAIScore"),
                "TodayFocusScore": _row_value(row, "TodayFocusScore"),
                "FocusStructureScore": _row_value(row, "FocusStructureScore"),
                "BlueprintSetupScore": blueprint_setup_score,
                "BlueprintFitScore": blueprint_fit_score,
                "BlueprintFitPass": focus_structure.get("blueprint_fit_pass"),
                "BlueprintFitFailReasons": _join_items(
                    focus_structure.get("blueprint_fit_fail_reasons")
                ),
                "FinalPreAIScore": _row_value(row, "FinalPreAIScore"),
                "DollarVolume20": _row_value(row, "DollarVolume20"),
                "PassedFocusGate": primary_gate_pass and not failure_reasons,
                "GateFailureReasons": _join_items(failure_reasons),
                "Actionability": _row_value(row, "Actionability"),
                "TriggerLevel": today_focus.get("trigger_level"),
                "InvalidationLevel": today_focus.get("invalidation_level"),
                "DoNotChaseAbove": today_focus.get("do_not_chase_above"),
                "PreferredEntryStyle": today_focus.get("preferred_entry_style"),
                "TodayFocusWarnings": _join_items(today_focus.get("warnings")),
                "TodayFocusDisqualifiers": _join_items(today_focus.get("disqualifiers")),
                "PctAboveEMA8": today_diagnostics.get("pct_above_ema8"),
                "PctAboveEMA21": today_diagnostics.get("pct_above_ema21"),
                "NearestSupportDistancePct": today_diagnostics.get("nearest_support_distance_pct"),
                "NearestResistanceDistancePct": today_diagnostics.get("nearest_resistance_distance_pct"),
                "NearestDemandDistancePct": today_diagnostics.get("nearest_demand_distance_pct"),
                "NearestSupplyDistancePct": today_diagnostics.get("nearest_supply_distance_pct"),
                "IgnitionAgeBars": today_diagnostics.get("ignition_age_bars"),
                "PctFromIgnitionClose": today_diagnostics.get("pct_from_ignition_close"),
                "HasNearbyTrigger": today_diagnostics.get("has_nearby_trigger"),
                "HasNearbyRetestArea": today_diagnostics.get("has_nearby_retest_area"),
                "HasReasonableInvalidation": today_diagnostics.get("has_reasonable_invalidation"),
                "IsExtended": today_diagnostics.get("is_extended"),
                "IsSeverelyExtended": today_diagnostics.get("is_severely_extended"),
                "StructureType": _row_value(row, "StructureType"),
                "BlueprintSetupType": _row_value(row, "BlueprintSetupType")
                or focus_structure.get("blueprint_setup_type"),
                "BlueprintSetupMatch": focus_structure.get("blueprint_setup_match"),
                "BlueprintSetupEvidence": _join_items(
                    focus_structure.get("blueprint_setup_evidence")
                ),
                "BlueprintSetupWarnings": _join_items(
                    focus_structure.get("blueprint_setup_warnings")
                ),
                "ImpulsePresent": focus_structure.get("impulse_present"),
                "ControlledDigestion": focus_structure.get("controlled_digestion"),
                "CompressionPresent": focus_structure.get("compression_present"),
                "VolumeDryup": focus_structure.get("volume_dryup"),
                "HoldingEMAStructure": focus_structure.get("holding_ema_structure"),
                "TriggerNearby": focus_structure.get("trigger_nearby"),
                "InvalidationNearby": focus_structure.get("invalidation_nearby"),
                "ExtensionRisk": focus_structure.get("extension_risk"),
                "StructureVerdict": focus_structure.get("structure_verdict"),
                "StructureReasons": _join_items(focus_structure.get("reasons")),
                "StructureWarnings": _join_items(focus_structure.get("warnings")),
                "StructureDisqualifiers": _join_items(focus_structure.get("disqualifiers")),
                "ClassificationReason": focus_structure.get("classification_reason"),
                "ScoreBeforeCaps": focus_structure.get("score_before_caps"),
                "ScoreAfterCaps": focus_structure.get("score_after_caps"),
                "ScoreCapReasons": _join_items(
                    _nested_value(structure_diagnostics, "score_cap_reasons")
                ),
                "PullbackDepthPct": _nested_value(digestion, "pullback_depth_pct"),
                "RangeContractionPct": _nested_value(compression, "range_contraction_pct"),
                "LowerHighs": _nested_value(compression, "lower_highs"),
                "HigherLows": _nested_value(compression, "higher_lows"),
                "CompressionQuality": _nested_value(compression, "quality"),
                "DigestionQuality": _nested_value(digestion, "quality"),
                "EMAQuality": _nested_value(ema_structure, "quality"),
                "TriggerDistancePct": _nested_value(trigger_invalidation, "trigger_distance_pct"),
                "InvalidationDistancePct": _nested_value(
                    trigger_invalidation, "invalidation_distance_pct"
                ),
                "VolumeDryupPct": _nested_value(volume_dryup, "dryup_pct"),
                "StructurePctAboveEMA8": _nested_value(ema_structure, "pct_above_ema8"),
                "StructurePctAboveEMA21": _nested_value(ema_structure, "pct_above_ema21"),
                "BlueprintRangePosition": blueprint_diagnostics.get("range_position"),
                "BlueprintBaseRangePct": blueprint_diagnostics.get("base_range_pct"),
                "BlueprintShortRangePct": blueprint_diagnostics.get("short_range_pct"),
                "BlueprintRedVolumeExpansion": blueprint_diagnostics.get(
                    "red_volume_expansion"
                ),
            }
        )

    return audit_rows


def save_focus_gate_audit(scored_candidates: Any, output_dir: str = "reports") -> str:
    """Save the focus-gate audit CSV and return its filepath."""
    reports_dir = Path(output_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filepath = reports_dir / f"focus_gate_audit_{timestamp}.csv"
    pd.DataFrame(build_focus_gate_audit_rows(scored_candidates)).to_csv(
        filepath, index=False
    )
    return str(filepath)


def _source_ohlcv_lookup(rows: Any) -> dict[str, pd.DataFrame]:
    """Build a symbol-to-OHLCV lookup from DataFrame/list rows."""
    lookup: dict[str, pd.DataFrame] = {}
    if rows is None:
        return lookup

    iterable = rows.iterrows() if isinstance(rows, pd.DataFrame) else enumerate(rows or [])
    for _, row in iterable:
        symbol = str(_row_value(row, "Symbol") or "").strip().upper()
        df = _row_value(row, "ohlcv")
        if symbol and isinstance(df, pd.DataFrame):
            lookup[symbol] = df

    return lookup


def _detector_chart_tag_summary(candidate: DetectorCandidate) -> str:
    """Return short detector tags for chart filename/title metadata."""
    tags = sorted(candidate.high_value_tags) or sorted(candidate.detector_tags)
    return "_".join(tags[:4])


def attach_detector_charts(
    candidates: list[DetectorCandidate],
    source_rows: Any,
    chart_output_dir: str = "charts/detectors",
    chart_limit: Optional[int] = DETECTOR_CHART_LIMIT,
) -> int:
    """Generate standardized detector charts for chart-needed candidates."""
    chart_candidates = [candidate for candidate in candidates if candidate.chart_needed]
    chart_candidates.sort(key=lambda item: item.interest_rank, reverse=True)
    if chart_limit is not None:
        chart_candidates = chart_candidates[:chart_limit]

    if not chart_candidates:
        return 0

    from chart_generator import generate_detector_chart_set

    ohlcv_lookup = _source_ohlcv_lookup(source_rows)
    generated_count = 0
    for candidate in chart_candidates:
        df = ohlcv_lookup.get(candidate.ticker)
        if not isinstance(df, pd.DataFrame):
            candidate.warning_tags.add("CHART_DATA_MISSING")
            candidate.notes.append("Chart generation skipped because OHLCV was missing.")
            continue

        try:
            chart_paths = generate_detector_chart_set(
                candidate.ticker,
                df,
                output_dir=chart_output_dir,
                trigger_level=candidate.trigger_level,
                stop_reference=candidate.stop_reference,
                tag_summary=_detector_chart_tag_summary(candidate),
            )
            candidate.chart_6m_path = chart_paths.get("daily_6m", "")
            candidate.chart_1y_path = chart_paths.get("daily_1y", "")
            generated_count += 1
        except Exception as exc:
            candidate.warning_tags.add("CHART_GENERATION_FAILED")
            candidate.notes.append(f"Chart generation failed: {exc}")

    return generated_count


def run_post_primary_detector_stage(
    primary_survivors: Any,
    output_dir: str = "reports",
    primary_gated_count: Optional[int] = None,
    generate_charts: bool = GENERATE_DETECTOR_CHARTS,
    detector_chart_limit: Optional[int] = DETECTOR_CHART_LIMIT,
) -> dict[str, str]:
    """Run loose setup detectors and save detector audit outputs."""
    evaluated_count = len(primary_survivors) if hasattr(primary_survivors, "__len__") else 0
    primary_count = evaluated_count if primary_gated_count is None else primary_gated_count

    print("Running post-primary setup detectors...", flush=True)
    detector_candidates, detector_failures = evaluate_detector_candidates(primary_survivors)

    chart_count = 0
    if generate_charts:
        chart_limit_text = "all chart-needed candidates" if detector_chart_limit is None else str(detector_chart_limit)
        print(f"Generating detector charts for {chart_limit_text}...", flush=True)
        chart_count = attach_detector_charts(
            detector_candidates,
            primary_survivors,
            chart_limit=detector_chart_limit,
        )

    output_paths = save_detector_outputs(
        detector_candidates,
        output_dir=output_dir,
        primary_gated_count=primary_count,
        detector_failures=detector_failures,
        include_json=True,
    )
    detector_hits_count = sum(
        1 for candidate in detector_candidates if candidate.detector_count > 0
    )
    chart_needed_count = sum(
        1 for candidate in detector_candidates if candidate.chart_needed
    )
    rejected_count = sum(
        1 for candidate in detector_candidates if candidate.reject_reason
    )

    print(f"Detector candidates evaluated: {len(detector_candidates)}", flush=True)
    print(f"Detector hits: {detector_hits_count}", flush=True)
    print(f"Detector chart-needed candidates: {chart_needed_count}", flush=True)
    print(f"Detector obvious rejects: {rejected_count}", flush=True)
    if generate_charts:
        print(f"Detector chart sets generated: {chart_count}", flush=True)
    print(f"Detector CSV saved: {output_paths.get('csv')}", flush=True)
    print(f"Detector report saved: {output_paths.get('report')}", flush=True)
    if output_paths.get("json"):
        print(f"Detector JSON saved: {output_paths.get('json')}", flush=True)

    return output_paths


def build_combined_audit_candidates(
    primary_gate_results: Any, scored_candidates: Any
) -> pd.DataFrame:
    """Combine primary-gate audit rows with fully scored focus-gate rows."""
    primary_df = (
        primary_gate_results.copy()
        if isinstance(primary_gate_results, pd.DataFrame)
        else pd.DataFrame()
    )
    scored_df = (
        scored_candidates.copy()
        if isinstance(scored_candidates, pd.DataFrame)
        else pd.DataFrame()
    )

    if primary_df.empty:
        return scored_df

    scored_symbols = set()
    if not scored_df.empty and "Symbol" in scored_df.columns:
        scored_symbols = {
            str(symbol).strip().upper()
            for symbol in scored_df["Symbol"].dropna().tolist()
        }

    audit_only = primary_df.copy()
    if scored_symbols and "Symbol" in audit_only.columns:
        audit_only = audit_only[
            ~audit_only["Symbol"].astype(str).str.upper().isin(scored_symbols)
        ].copy()
    audit_only["primary_gate_audit_only"] = True

    if scored_df.empty:
        return audit_only.reset_index(drop=True)

    return pd.concat([audit_only, scored_df], ignore_index=True, sort=False)


def attach_candidate_context_to_analysis(
    analysis: dict[str, Any], candidate: Any
) -> dict[str, Any]:
    """Attach deterministic gate/sector context to a report analysis."""
    if not isinstance(analysis, dict):
        return analysis

    row = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate or {})
    for field in [
        "Sector",
        "Exchange",
        "price",
        "sma21",
        "sma50",
        "market_cap",
        "atr",
        "avg_volume",
        "passes_price_sma21",
        "passes_price_sma50",
        "passes_price_min",
        "passes_market_cap",
        "passes_atr",
        "passes_avg_volume",
        "primary_gate_pass",
        "primary_gate_fail_reasons",
        "sector_name",
        "sector_etf",
        "sector_perf_1w",
        "sector_perf_1m",
        "sector_perf_3m",
        "sector_perf_6m",
        "sector_perf_1y",
        "sector_score",
        "sector_rank",
        "stock_perf_1w",
        "stock_perf_1m",
        "stock_perf_3m",
        "relative_strength_1m",
        "relative_strength_3m",
        "SectorAlignmentScore",
        "BlueprintSetupScore",
        "BlueprintFitScore",
        "BlueprintSetupType",
    ]:
        if field in row:
            analysis[field] = row.get(field)

    return analysis


def score_candidate_pre_ai(row: Any) -> float:
    """
    Score a Module 3 candidate before deciding whether to spend a Claude call.

    The score intentionally uses only deterministic candidate columns:
    Close, AvgVolume20, SMA20, and AboveSMA20.
    """
    score = 0.0

    close = _as_float(_row_value(row, "Close"))
    sma20 = _as_float(_row_value(row, "SMA20"))
    avg_volume20 = _as_float(_row_value(row, "AvgVolume20"))
    above_sma20 = _as_bool(_row_value(row, "AboveSMA20"))

    if above_sma20:
        score += 20

    if close is not None and sma20 is not None and sma20 > 0:
        pct_above_sma20 = ((close / sma20) - 1) * 100

        if 0 <= pct_above_sma20 <= 5:
            score += 10
        elif 5 < pct_above_sma20 <= 10:
            score += 7
        elif pct_above_sma20 > 10:
            score += 3

    if avg_volume20 is not None:
        if avg_volume20 > 20_000_000:
            score += 20
        elif avg_volume20 > 5_000_000:
            score += 15
        elif avg_volume20 > 1_000_000:
            score += 10

    return score


def score_sector_alignment(row: Any) -> float:
    """
    Score whether a stock belongs to a current leading sector.

    The blueprint prioritizes strong names in strong sectors. Unknown sector
    metadata stays neutral so broad-universe discovery is not blocked by
    incomplete yfinance profile data.
    """
    sector_rank = _as_float(_row_value(row, "sector_rank"))
    sector_score = _as_float(_row_value(row, "sector_score"))
    relative_strength_1m = _as_float(_row_value(row, "relative_strength_1m"))
    relative_strength_3m = _as_float(_row_value(row, "relative_strength_3m"))

    if sector_rank is None:
        base_score = 50.0
    else:
        base_score = {
            1: 100,
            2: 92,
            3: 84,
            4: 76,
            5: 68,
            6: 58,
            7: 50,
            8: 42,
            9: 34,
            10: 26,
            11: 18,
        }.get(int(sector_rank), 50)

    momentum_adjustment = 0.0
    if sector_score is not None:
        momentum_adjustment = max(-12.0, min(12.0, sector_score / 2))

    relative_strength_adjustment = 0.0
    if relative_strength_1m is not None:
        if relative_strength_1m >= 8:
            relative_strength_adjustment += 12
        elif relative_strength_1m >= 4:
            relative_strength_adjustment += 7
        elif relative_strength_1m <= -8:
            relative_strength_adjustment -= 15
        elif relative_strength_1m <= -4:
            relative_strength_adjustment -= 8
    if relative_strength_3m is not None:
        if relative_strength_3m >= 12:
            relative_strength_adjustment += 8
        elif relative_strength_3m >= 6:
            relative_strength_adjustment += 4
        elif relative_strength_3m <= -12:
            relative_strength_adjustment -= 10
        elif relative_strength_3m <= -6:
            relative_strength_adjustment -= 5

    return max(
        0.0,
        min(
            100.0,
            float(base_score) + momentum_adjustment + relative_strength_adjustment,
        ),
    )


def select_candidates_for_ai(
    candidates_df: pd.DataFrame, max_ai_analyses: int = MAX_AI_ANALYSES
) -> pd.DataFrame:
    """
    Add deterministic pre-AI scores and return the top candidates for Claude.
    """
    if max_ai_analyses <= 0:
        raise ValueError("max_ai_analyses must be a positive integer.")

    if candidates_df is None or candidates_df.empty:
        return pd.DataFrame()

    selected = candidates_df.copy()
    selected["PreAIScore"] = selected.apply(score_candidate_pre_ai, axis=1)

    return (
        selected.sort_values(
            by=["PreAIScore", "AvgVolume20", "Close"],
            ascending=[False, False, False],
        )
        .head(max_ai_analyses)
        .reset_index(drop=True)
    )


def _levels_on_side(levels: Any, current_price: float, side: str) -> list[float]:
    """Return numeric levels below or above current price."""
    matching_levels = []

    for level in levels or []:
        numeric_level = _as_float(level)
        if numeric_level is None:
            continue

        if side == "below" and numeric_level < current_price:
            matching_levels.append(numeric_level)
        elif side == "above" and numeric_level > current_price:
            matching_levels.append(numeric_level)

    return matching_levels


def _zones_on_side(zones: Any, current_price: float, side: str) -> list[dict[str, Any]]:
    """Return zones fully below or fully above current price."""
    matching_zones = []

    for zone in zones or []:
        if not isinstance(zone, dict):
            continue

        low = _as_float(zone.get("low"))
        high = _as_float(zone.get("high"))
        if low is None or high is None:
            continue

        if side == "below" and high < current_price:
            matching_zones.append(zone)
        elif side == "above" and low > current_price:
            matching_zones.append(zone)

    return matching_zones


def score_technicals_pre_ai(symbol: str, candidate_row: Any, technicals: dict[str, Any]) -> float:
    """
    Score a candidate with deterministic Module 4 blueprint features before Claude.
    """
    if not isinstance(technicals, dict):
        return 0.0

    score = 0.0

    ema_regime = technicals.get("ema_regime")
    if not isinstance(ema_regime, dict):
        ema_regime = {}

    regime = str(ema_regime.get("regime", "")).strip().lower()
    score += {
        "strong_bullish": 30,
        "bullish": 20,
        "mixed": 0,
        "bearish": -20,
        "strong_bearish": -30,
    }.get(regime, 0)

    ignition_candle = technicals.get("ignition_candle")
    if isinstance(ignition_candle, dict) and ignition_candle.get("found"):
        direction = str(ignition_candle.get("direction", "")).strip().lower()
        if direction == "bullish":
            score += 25
        elif direction == "bearish":
            score -= 25

    accumulation_distribution = technicals.get("accumulation_distribution")
    if not isinstance(accumulation_distribution, dict):
        accumulation_distribution = {}

    volume_bias = str(accumulation_distribution.get("volume_bias", "")).strip().lower()
    if volume_bias == "accumulation":
        score += 20
    elif volume_bias == "distribution":
        score -= 25

    obv_trend = str(accumulation_distribution.get("obv_trend", "")).strip().lower()
    if obv_trend == "rising":
        score += 10
    elif obv_trend == "falling":
        score -= 10

    current_price = _as_float(ema_regime.get("close"))
    if current_price is not None and current_price > 0:
        support_resistance = technicals.get("support_resistance")
        if not isinstance(support_resistance, dict):
            support_resistance = {}

        support_below = _levels_on_side(
            support_resistance.get("support_levels"), current_price, "below"
        )
        resistance_above = _levels_on_side(
            support_resistance.get("resistance_levels"), current_price, "above"
        )

        score += 10 if support_below else -10
        if resistance_above:
            score += 5

        supply_demand_zones = technicals.get("supply_demand_zones")
        if not isinstance(supply_demand_zones, dict):
            supply_demand_zones = {}

        demand_below = _zones_on_side(
            supply_demand_zones.get("demand_zones"), current_price, "below"
        )
        supply_above = _zones_on_side(
            supply_demand_zones.get("supply_zones"), current_price, "above"
        )

        if demand_below:
            score += 10

        if supply_above:
            nearest_supply_low = min(
                _as_float(zone.get("low"))
                for zone in supply_above
                if _as_float(zone.get("low")) is not None
            )
            if ((nearest_supply_low / current_price) - 1) * 100 <= 5:
                score -= 10

        ema8 = _as_float(ema_regime.get("ema8"))
        if ema8 is not None and ema8 > 0:
            pct_above_ema8 = ((current_price / ema8) - 1) * 100
            if pct_above_ema8 > 15:
                score -= 20
            elif pct_above_ema8 > 8:
                score -= 10

    volume_anomalies = technicals.get("volume_anomalies")
    score += -5 if volume_anomalies else 5

    return score


def build_technical_shortlist(
    candidates_df: pd.DataFrame,
    max_ai_analyses: int = MAX_AI_ANALYSES,
    max_candidates_to_score: Optional[int] = MAX_CANDIDATES_TO_SCORE,
) -> pd.DataFrame:
    """
    Score hard-gate survivors with setup layers and return the Claude shortlist.
    """
    if max_ai_analyses < 0:
        raise ValueError("max_ai_analyses must be zero or a positive integer.")
    if max_candidates_to_score is not None and max_candidates_to_score <= 0:
        raise ValueError("max_candidates_to_score must be a positive integer.")

    if candidates_df is None or candidates_df.empty:
        return pd.DataFrame()

    scored_pool = candidates_df.copy()
    scored_pool["PreAIScore"] = scored_pool.apply(score_candidate_pre_ai, axis=1)
    if max_candidates_to_score is None or max_candidates_to_score >= len(scored_pool):
        candidates_to_score = scored_pool.reset_index(drop=True)
        print(
            f"Scoring all {len(candidates_to_score)} hard-gate survivors through technical/focus layers...",
            flush=True,
        )
    else:
        candidates_to_score = select_candidates_for_ai(
            scored_pool, max_ai_analyses=max_candidates_to_score
        )
        print(
            f"Capping technical/focus scoring at top {len(candidates_to_score)} hard-gate survivors by pre-score...",
            flush=True,
        )

    scored_rows = []
    failures = {}

    for _, candidate in candidates_to_score.iterrows():
        symbol = str(candidate.get("Symbol", "")).strip().upper()
        if not symbol:
            continue

        print(f"Scoring technicals for {symbol}...", flush=True)

        try:
            candidate_ohlcv = candidate.get("ohlcv")
            df = (
                candidate_ohlcv
                if isinstance(candidate_ohlcv, pd.DataFrame)
                else fetch_stock_data(symbol, period="6mo", interval="1d")
            )
            technicals = analyze_stock_technicals(symbol, df)
            technical_score = score_technicals_pre_ai(symbol, candidate, technicals)
            today_focus = evaluate_today_focus(symbol, technicals)
            today_focus_score = _as_float(today_focus.get("today_focus_score")) or 0.0
            focus_structure = evaluate_focus_structure(symbol, df, technicals)
            focus_structure_score = (
                _as_float(focus_structure.get("focus_structure_score")) or 0.0
            )
            blueprint_setup_score = (
                _as_float(focus_structure.get("blueprint_setup_score")) or 0.0
            )
            blueprint_fit_score = (
                _as_float(focus_structure.get("blueprint_fit_score")) or 0.0
            )
            sector_alignment_score = score_sector_alignment(candidate)
            final_pre_ai_score = (
                (0.15 * technical_score)
                + (0.25 * today_focus_score)
                + (0.25 * focus_structure_score)
                + (0.20 * blueprint_fit_score)
                + (0.15 * sector_alignment_score)
            )
        except Exception as exc:
            failures[symbol] = str(exc)
            print(f"Skipping {symbol} during technical/focus scoring: {exc}", flush=True)
            continue

        scored_row = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate)
        scored_row.update(
            {
                "Symbol": symbol,
                "PreAIScore": candidate.get("PreAIScore"),
                "TechnicalPreAIScore": technical_score,
                "TodayFocusScore": today_focus_score,
                "Actionability": today_focus.get("actionability"),
                "FocusStructureScore": focus_structure_score,
                "StructureType": focus_structure.get("structure_type"),
                "BlueprintSetupScore": blueprint_setup_score,
                "BlueprintFitScore": blueprint_fit_score,
                "BlueprintSetupType": focus_structure.get("blueprint_setup_type"),
                "SectorAlignmentScore": sector_alignment_score,
                "FinalPreAIScore": final_pre_ai_score,
                "ohlcv": df,
                "technicals": technicals,
                "today_focus": today_focus,
                "focus_structure": focus_structure,
            }
        )
        scored_rows.append(scored_row)

    if not scored_rows:
        empty = pd.DataFrame()
        empty.attrs["attempted_count"] = len(candidates_to_score)
        empty.attrs["scored_count"] = 0
        empty.attrs["qualified_count"] = 0
        empty.attrs["all_scored_results"] = empty
        empty.attrs["failures"] = failures
        return empty

    technical_results = pd.DataFrame(scored_rows)
    technical_results["PassedFocusQualityGate"] = technical_results.apply(
        passes_focus_quality_gate, axis=1
    )
    qualified_results = technical_results[technical_results["PassedFocusQualityGate"]].copy()
    qualified_results = qualified_results.sort_values(
        by=[
            "FinalPreAIScore",
            "BlueprintFitScore",
            "FocusStructureScore",
            "TodayFocusScore",
            "BlueprintSetupScore",
            "SectorAlignmentScore",
            "TechnicalPreAIScore",
            "AvgVolume20",
            "Close",
        ],
        ascending=[False, False, False, False, False, False, False, False, False],
    ).reset_index(drop=True)
    technical_results = technical_results.sort_values(
        by=[
            "FinalPreAIScore",
            "BlueprintFitScore",
            "FocusStructureScore",
            "TodayFocusScore",
            "BlueprintSetupScore",
            "SectorAlignmentScore",
            "TechnicalPreAIScore",
            "AvgVolume20",
            "Close",
        ],
        ascending=[False, False, False, False, False, False, False, False, False],
    ).reset_index(drop=True)

    shortlist = qualified_results.head(max_ai_analyses).reset_index(drop=True)
    shortlist.attrs["attempted_count"] = len(candidates_to_score)
    shortlist.attrs["scored_count"] = len(technical_results)
    shortlist.attrs["qualified_count"] = len(qualified_results)
    shortlist.attrs["all_scored_results"] = technical_results
    shortlist.attrs["failures"] = failures
    return shortlist


def _no_candidates_report(
    output_dir: str,
    sector_leadership: Optional[list[dict[str, Any]]] = None,
    market_context: Optional[dict[str, Any]] = None,
) -> str:
    """Save a simple report for scans with no passing candidates."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    market_section = format_market_context_section(market_context)
    sector_section = format_sector_leadership_section(sector_leadership)
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

No candidates passed the deterministic premarket filters.

{market_section}

{sector_section}

---

## Detailed Analysis

No AI analysis was run because there were no candidates to review.
"""
    return save_report(markdown_text, output_dir=output_dir)


def _no_qualified_report(
    output_dir: str,
    scanned_count: int,
    attempted_count: int,
    scored_count: int,
    qualified_count: int,
    hard_gate_survivors: Optional[int] = None,
    hard_gate_rejected: Optional[int] = None,
    audit_filepath: Optional[str] = None,
    sector_leadership: Optional[list[dict[str, Any]]] = None,
    market_context: Optional[dict[str, Any]] = None,
) -> str:
    """Save a report when quality gates filter out every candidate."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    market_section = format_market_context_section(market_context)
    sector_section = format_sector_leadership_section(sector_leadership)
    audit_note = (
        f"\nFocus gate audit saved separately:\n{audit_filepath}\n"
        if audit_filepath
        else ""
    )
    hard_gate_rows = ""
    if hard_gate_survivors is not None:
        hard_gate_rows += f"| Hard gate survivors | {hard_gate_survivors} |\n"
    if hard_gate_rejected is not None:
        hard_gate_rows += f"| Hard gate rejected | {hard_gate_rejected} |\n"
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

No high-quality same-day focus-list setups passed the filters today.

| Metric | Count |
|---|---:|
| Symbols loaded for hard gate | {scanned_count} |
{hard_gate_rows}\
| Candidates technically scored | {scored_count} |
| Candidates evaluated by quality gates | {attempted_count} |
| Qualified after focus gates | {qualified_count} |
| Selected for Claude | 0 |

{market_section}

{sector_section}

---

## Detailed Analysis

Claude analysis was not run because the focus-quality gates filtered out every
candidate. This means no ticker met the required combination of same-day
actionability, focus-list structure, nearby trigger/retest path, and nearby
invalidation.{audit_note}

## Focus Structure

The Focus Structure Layer requires the Sean-style shape before spending Claude
calls: recent impulse, controlled digestion, compression/base behavior, EMA
hold or reclaim, nearby trigger or retest path, nearby invalidation, and no
severe extension without digestion. Candidates with `extended_no_base`,
`sloppy_chop`, or `no_clear_structure` are excluded from AI review. The
Blueprint Fit layer also requires a named blueprint setup, volume confirmation,
compression or a tight base, EMA support, and a clear trigger/retest reference.
"""
    return save_report(markdown_text, output_dir=output_dir)


def _error_report(
    output_dir: str,
    message: str,
    sector_leadership: Optional[list[dict[str, Any]]] = None,
    market_context: Optional[dict[str, Any]] = None,
) -> str:
    """Save a report when the scan cannot reach the AI-analysis stage."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    market_section = format_market_context_section(market_context)
    sector_section = format_sector_leadership_section(sector_leadership)
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

The premarket scan did not complete.

{market_section}

{sector_section}

---

## Details

{message}
"""
    return save_report(markdown_text, output_dir=output_dir)


def _no_ai_report(
    output_dir: str,
    reason: str,
    scanned_count: int,
    attempted_count: int,
    scored_count: int,
    qualified_count: int,
    selected_count: int,
    hard_gate_survivors: Optional[int] = None,
    hard_gate_rejected: Optional[int] = None,
    audit_filepath: Optional[str] = None,
    qualified_candidates: Optional[pd.DataFrame] = None,
    sector_leadership: Optional[list[dict[str, Any]]] = None,
    market_context: Optional[dict[str, Any]] = None,
) -> str:
    """Save a report when scanning completes but Claude is intentionally skipped."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    market_section = format_market_context_section(market_context)
    sector_section = format_sector_leadership_section(sector_leadership)
    audit_note = (
        f"\nFocus gate audit saved separately:\n{audit_filepath}\n"
        if audit_filepath
        else ""
    )
    hard_gate_rows = ""
    if hard_gate_survivors is not None:
        hard_gate_rows += f"| Hard gate survivors | {hard_gate_survivors} |\n"
    if hard_gate_rejected is not None:
        hard_gate_rows += f"| Hard gate rejected | {hard_gate_rejected} |\n"
    qualified_lines = []
    if isinstance(qualified_candidates, pd.DataFrame) and not qualified_candidates.empty:
        for _, row in qualified_candidates.iterrows():
            sector_name = row.get("sector_name") or row.get("Sector") or "Unknown"
            sector_etf = row.get("sector_etf") or "N/A"
            sector_rank = _as_float(row.get("sector_rank"))
            sector_score = _as_float(row.get("sector_score"))
            sector_rank_text = (
                str(int(sector_rank))
                if sector_rank is not None and sector_rank.is_integer()
                else "N/A"
            )
            sector_score_text = (
                f"{sector_score:.1f}" if sector_score is not None else "N/A"
            )
            blueprint_setup_score = _as_float(row.get("BlueprintSetupScore")) or 0.0
            blueprint_fit_score = _as_float(row.get("BlueprintFitScore")) or 0.0
            sector_alignment_score = _as_float(row.get("SectorAlignmentScore")) or 0.0
            qualified_lines.append(
                "- "
                f"{row.get('Symbol')} | "
                f"Final {row.get('FinalPreAIScore'):.1f} | "
                f"Tech {row.get('TechnicalPreAIScore'):.1f} | "
                f"Today {row.get('TodayFocusScore'):.1f} | "
                f"Structure {row.get('FocusStructureScore'):.1f} | "
                f"Blueprint {blueprint_setup_score:.1f} | "
                f"Fit {blueprint_fit_score:.1f} | "
                f"SectorAlign {sector_alignment_score:.1f} | "
                f"{row.get('Actionability')} | {row.get('StructureType')} | "
                f"{row.get('BlueprintSetupType') or 'no_blueprint_setup'} | "
                f"Sector: {sector_name} / {sector_etf} | "
                f"Rank: {sector_rank_text} | Score: {sector_score_text}"
            )

    qualified_text = "\n".join(qualified_lines) if qualified_lines else "- None"
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

Claude analysis was not run.

Reason: {reason}

| Metric | Count |
|---|---:|
| Symbols loaded for hard gate | {scanned_count} |
{hard_gate_rows}\
| Candidates technically scored | {scored_count} |
| Candidates evaluated by quality gates | {attempted_count} |
| Qualified after focus gates | {qualified_count} |
| Selected for Claude | {selected_count} |
| Claude calls | 0 |

{market_section}

{sector_section}

{audit_note}

---

## Qualified Candidates

{qualified_text}
"""
    return save_report(markdown_text, output_dir=output_dir)


def _no_audit_candidates_report(
    output_dir: str,
    audit_path: str,
    sector_leadership: Optional[list[dict[str, Any]]] = None,
    market_context: Optional[dict[str, Any]] = None,
) -> str:
    """Save a report when an audit CSV has no passing focus-gate rows."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    market_section = format_market_context_section(market_context)
    sector_section = format_sector_leadership_section(sector_leadership)
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

No qualified candidates were found in the focus gate audit.

| Metric | Value |
|---|---|
| Source audit | {audit_path} |
| Claude calls | 0 |

{market_section}

{sector_section}

---

## Detailed Analysis

Analyze-from-audit mode loaded the source audit, but no rows had
`PassedFocusGate` set to true. Claude analysis was not run.
"""
    return save_report(markdown_text, output_dir=output_dir)


def _format_elapsed(elapsed_seconds: float) -> str:
    """Format elapsed seconds as minutes/seconds."""
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)
    return f"{minutes} minutes {seconds} seconds"


def load_candidates_from_audit(
    audit_path: str, max_ai_analyses: int = MAX_AI_ANALYSES
) -> list[dict[str, Any]]:
    """Load passing focus-gate candidates from a saved audit CSV."""
    if max_ai_analyses < 0:
        raise ValueError("max_ai_analyses must be zero or a positive integer.")

    path = Path(audit_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Audit CSV not found: {audit_path}")
    if not path.is_file():
        raise ValueError(f"Audit path is not a file: {audit_path}")

    audit_df = pd.read_csv(path)
    required_columns = ["Symbol", "PassedFocusGate", "FinalPreAIScore"]
    missing_columns = [
        column for column in required_columns if column not in audit_df.columns
    ]
    if missing_columns:
        raise ValueError(
            "Audit CSV is missing required columns: "
            f"{', '.join(missing_columns)}"
        )

    print(f"Audit rows loaded: {len(audit_df)}", flush=True)
    passed = audit_df[audit_df["PassedFocusGate"].apply(_as_bool)].copy()
    print(f"Passed candidates found: {len(passed)}", flush=True)
    if passed.empty:
        return []

    passed["_AuditSortFinalPreAIScore"] = passed["FinalPreAIScore"].apply(
        lambda value: _as_float(value) if _as_float(value) is not None else float("-inf")
    )
    passed = passed.sort_values(
        by="_AuditSortFinalPreAIScore", ascending=False
    ).reset_index(drop=True)
    if max_ai_analyses > 0:
        passed = passed.head(max_ai_analyses).reset_index(drop=True)

    candidates: list[dict[str, Any]] = []
    for _, row in passed.iterrows():
        candidate = {
            field: _clean_audit_value(row.get(field))
            for field in AUDIT_CANDIDATE_FIELDS
            if field in row.index
        }
        candidates.append(candidate)

    print(f"Selected candidates from audit: {len(candidates)}", flush=True)
    if candidates:
        print(
            "Selected tickers: "
            + ", ".join(str(candidate.get("Symbol")) for candidate in candidates),
            flush=True,
        )

    return candidates


def load_primary_gate_rows_from_audit(audit_path: str) -> list[dict[str, Any]]:
    """Load rows that passed the primary hard gate from an audit CSV."""
    path = Path(audit_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Audit CSV not found: {audit_path}")
    if not path.is_file():
        raise ValueError(f"Audit path is not a file: {audit_path}")

    audit_df = pd.read_csv(path)
    if "Symbol" not in audit_df.columns:
        raise ValueError("Audit CSV is missing required column: Symbol")

    if "primary_gate_pass" in audit_df.columns:
        primary_passed = audit_df[audit_df["primary_gate_pass"].apply(_as_bool)].copy()
    elif "PassedFocusGate" in audit_df.columns:
        primary_passed = audit_df[audit_df["PassedFocusGate"].apply(_as_bool)].copy()
    else:
        primary_passed = audit_df.copy()

    rows: list[dict[str, Any]] = []
    for _, row in primary_passed.iterrows():
        candidate = {
            key: _clean_audit_value(value)
            for key, value in row.to_dict().items()
        }
        rows.append(candidate)

    return rows


def run_detectors_from_audit(
    audit_path: str,
    output_dir: str = "reports",
    generate_charts: bool = GENERATE_DETECTOR_CHARTS,
    detector_chart_limit: Optional[int] = DETECTOR_CHART_LIMIT,
) -> dict[str, str]:
    """Run only the detector stage from a saved audit CSV."""
    start_time = time.perf_counter()

    def finish(paths: dict[str, str]) -> dict[str, str]:
        elapsed = time.perf_counter() - start_time
        print(f"Detector report path: {paths.get('report')}", flush=True)
        print(f"Total runtime: {_format_elapsed(elapsed)}", flush=True)
        return paths

    print("Starting detector-only audit run...", flush=True)
    print("Configuration:", flush=True)
    print(f"- Audit path: {audit_path}", flush=True)
    print(f"- Output dir: {output_dir}", flush=True)
    print(f"- Generate detector charts: {generate_charts}", flush=True)
    if detector_chart_limit is not None:
        print(f"- Detector chart limit: {detector_chart_limit}", flush=True)

    try:
        primary_rows = load_primary_gate_rows_from_audit(audit_path)
    except Exception as exc:
        print(f"Could not load audit CSV: {exc}", flush=True)
        paths = save_detector_outputs(
            [],
            output_dir=output_dir,
            primary_gated_count=0,
            detector_failures={"audit": str(exc)},
            include_json=True,
        )
        return finish(paths)

    print(f"Primary-gate audit rows found: {len(primary_rows)}", flush=True)
    enriched_rows: list[dict[str, Any]] = []
    fetch_failures: dict[str, str] = {}
    for idx, row in enumerate(primary_rows, start=1):
        symbol = str(row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        print(f"Fetching detector OHLCV {idx}/{len(primary_rows)} {symbol}...", flush=True)
        try:
            df = fetch_stock_data(symbol, period="1y", interval="1d")
            enriched = dict(row)
            enriched["Symbol"] = symbol
            enriched["ohlcv"] = df
            enriched_rows.append(enriched)
        except Exception as exc:
            fetch_failures[symbol] = str(exc)
            enriched_rows.append(
                {
                    **dict(row),
                    "Symbol": symbol,
                    "detector_fetch_error": str(exc),
                }
            )

    detector_candidates, detector_failures = evaluate_detector_candidates(enriched_rows)
    detector_failures.update(fetch_failures)
    chart_count = 0
    if generate_charts:
        chart_limit_text = "all chart-needed candidates" if detector_chart_limit is None else str(detector_chart_limit)
        print(f"Generating detector charts for {chart_limit_text}...", flush=True)
        chart_count = attach_detector_charts(
            detector_candidates,
            enriched_rows,
            chart_limit=detector_chart_limit,
        )

    paths = save_detector_outputs(
        detector_candidates,
        output_dir=output_dir,
        primary_gated_count=len(primary_rows),
        detector_failures=detector_failures,
        include_json=True,
    )
    print(f"Detector candidates evaluated: {len(detector_candidates)}", flush=True)
    print(
        "Detector chart-needed candidates: "
        f"{sum(1 for candidate in detector_candidates if candidate.chart_needed)}",
        flush=True,
    )
    print(
        "Detector obvious rejects: "
        f"{sum(1 for candidate in detector_candidates if candidate.reject_reason)}",
        flush=True,
    )
    if generate_charts:
        print(f"Detector chart sets generated: {chart_count}", flush=True)
    print(f"Detector CSV saved: {paths.get('csv')}", flush=True)
    print(f"Detector report saved: {paths.get('report')}", flush=True)
    if paths.get("json"):
        print(f"Detector JSON saved: {paths.get('json')}", flush=True)
    return finish(paths)


def _build_deterministic_only_analysis(
    candidate: dict[str, Any],
    today_focus: dict[str, Any],
    focus_structure: dict[str, Any],
    audit_path: str,
) -> dict[str, Any]:
    """Build a report-ready analysis entry when Claude is intentionally skipped."""
    symbol = str(candidate.get("Symbol") or "").strip().upper()
    final_pre_ai_score = _as_float(candidate.get("FinalPreAIScore"))
    structure_type = (
        candidate.get("StructureType")
        or focus_structure.get("structure_type")
        or "deterministic_only"
    )
    actionability = (
        candidate.get("Actionability")
        or today_focus.get("actionability")
        or "deterministic_only"
    )

    analysis = {
        "symbol": symbol,
        "overall_score": final_pre_ai_score,
        "bias": "deterministic_only",
        "setup_type": structure_type,
        "setup_quality": actionability,
        "final_verdict": (
            "Selected from focus gate audit. Claude analysis was not run. "
            f"Source audit: {audit_path}"
        ),
        "today_focus": today_focus,
        "focus_structure": focus_structure,
        "technical_pre_ai_score": _as_float(candidate.get("TechnicalPreAIScore")),
        "today_focus_score": _as_float(candidate.get("TodayFocusScore")),
        "focus_structure_score": _as_float(candidate.get("FocusStructureScore")),
        "blueprint_setup_score": _as_float(candidate.get("BlueprintSetupScore")),
        "blueprint_fit_score": _as_float(candidate.get("BlueprintFitScore")),
        "blueprint_setup_type": candidate.get("BlueprintSetupType"),
        "sector_alignment_score": _as_float(candidate.get("SectorAlignmentScore")),
        "structure_type": structure_type,
        "final_pre_ai_score": final_pre_ai_score,
        "deterministic_only": True,
        "warnings": ["Claude analysis was not run."],
        "disqualifiers": [],
        "audit_source": audit_path,
    }
    return attach_candidate_context_to_analysis(analysis, candidate)


def _attach_audit_scores(
    analysis: dict[str, Any],
    candidate: dict[str, Any],
    today_focus: dict[str, Any],
    focus_structure: dict[str, Any],
    audit_path: str,
) -> dict[str, Any]:
    """Attach deterministic context and audit metadata to an analysis dictionary."""
    analysis["today_focus"] = today_focus
    analysis["focus_structure"] = focus_structure
    analysis["technical_pre_ai_score"] = _as_float(candidate.get("TechnicalPreAIScore"))
    analysis["today_focus_score"] = _as_float(candidate.get("TodayFocusScore"))
    analysis["focus_structure_score"] = _as_float(candidate.get("FocusStructureScore"))
    analysis["blueprint_setup_score"] = _as_float(candidate.get("BlueprintSetupScore"))
    analysis["blueprint_fit_score"] = _as_float(candidate.get("BlueprintFitScore"))
    analysis["blueprint_setup_type"] = candidate.get("BlueprintSetupType")
    analysis["sector_alignment_score"] = _as_float(candidate.get("SectorAlignmentScore"))
    analysis["structure_type"] = candidate.get("StructureType")
    analysis["final_pre_ai_score"] = _as_float(candidate.get("FinalPreAIScore"))
    analysis["audit_source"] = audit_path
    return attach_candidate_context_to_analysis(analysis, candidate)


def build_setup_judge_context(
    candidate: dict[str, Any],
    technicals: dict[str, Any],
    today_focus: dict[str, Any],
    focus_structure: dict[str, Any],
) -> dict[str, Any]:
    """Build the deterministic context Claude Setup Judge is allowed to review."""
    return {
        "symbol": str(candidate.get("Symbol") or "").strip().upper(),
        "company": candidate.get("Company"),
        "sector": candidate.get("Sector"),
        "exchange": candidate.get("Exchange"),
        "scores": {
            "technical_pre_ai_score": _as_float(candidate.get("TechnicalPreAIScore")),
            "today_focus_score": _as_float(candidate.get("TodayFocusScore")),
            "focus_structure_score": _as_float(candidate.get("FocusStructureScore")),
            "blueprint_setup_score": _as_float(candidate.get("BlueprintSetupScore")),
            "blueprint_fit_score": _as_float(candidate.get("BlueprintFitScore")),
            "blueprint_setup_type": candidate.get("BlueprintSetupType"),
            "sector_alignment_score": _as_float(candidate.get("SectorAlignmentScore")),
            "final_pre_ai_score": _as_float(candidate.get("FinalPreAIScore")),
        },
        "primary_gate": {
            "primary_gate_pass": candidate.get("primary_gate_pass"),
            "primary_gate_fail_reasons": candidate.get("primary_gate_fail_reasons"),
            "price": _as_float(candidate.get("price")),
            "sma21": _as_float(candidate.get("sma21")),
            "sma50": _as_float(candidate.get("sma50")),
            "market_cap": _as_float(candidate.get("market_cap")),
            "atr": _as_float(candidate.get("atr")),
            "avg_volume": _as_float(candidate.get("avg_volume")),
        },
        "sector_leadership": {
            "sector_name": candidate.get("sector_name") or candidate.get("Sector"),
            "sector_etf": candidate.get("sector_etf"),
            "sector_rank": _as_float(candidate.get("sector_rank")),
            "sector_score": _as_float(candidate.get("sector_score")),
            "sector_perf_1w": _as_float(candidate.get("sector_perf_1w")),
            "sector_perf_1m": _as_float(candidate.get("sector_perf_1m")),
            "sector_perf_3m": _as_float(candidate.get("sector_perf_3m")),
            "sector_perf_6m": _as_float(candidate.get("sector_perf_6m")),
            "sector_perf_1y": _as_float(candidate.get("sector_perf_1y")),
            "stock_perf_1w": _as_float(candidate.get("stock_perf_1w")),
            "stock_perf_1m": _as_float(candidate.get("stock_perf_1m")),
            "stock_perf_3m": _as_float(candidate.get("stock_perf_3m")),
            "relative_strength_1m": _as_float(candidate.get("relative_strength_1m")),
            "relative_strength_3m": _as_float(candidate.get("relative_strength_3m")),
        },
        "deterministic_levels": {
            "trigger_level": (
                today_focus.get("trigger_level")
                if isinstance(today_focus, dict)
                else candidate.get("TriggerLevel")
            ),
            "invalidation_level": (
                today_focus.get("invalidation_level")
                if isinstance(today_focus, dict)
                else candidate.get("InvalidationLevel")
            ),
            "do_not_chase_above": (
                today_focus.get("do_not_chase_above")
                if isinstance(today_focus, dict)
                else candidate.get("DoNotChaseAbove")
            ),
            "preferred_entry_style": (
                today_focus.get("preferred_entry_style")
                if isinstance(today_focus, dict)
                else candidate.get("PreferredEntryStyle")
            ),
        },
        "technicals": {
            "ema_regime": technicals.get("ema_regime"),
            "ignition_candle": technicals.get("ignition_candle"),
            "volume_anomalies": technicals.get("volume_anomalies"),
            "accumulation_distribution": technicals.get("accumulation_distribution"),
            "support_resistance": technicals.get("support_resistance"),
            "supply_demand_zones": technicals.get("supply_demand_zones"),
        },
        "today_focus": today_focus,
        "focus_structure": focus_structure,
    }


def attach_setup_judge(
    analysis: dict[str, Any], setup_judge: dict[str, Any]
) -> dict[str, Any]:
    """Attach Setup Judge output to a report analysis dictionary."""
    if isinstance(analysis, dict) and isinstance(setup_judge, dict):
        analysis["setup_judge"] = setup_judge
        if setup_judge.get("judge_action") == "veto" or not setup_judge.get(
            "manual_review_pass", True
        ):
            analysis["setup_judge_veto"] = True
    return analysis


def apply_setup_judge_to_candidates(
    enriched_candidates: list[dict[str, Any]],
    max_ai_analyses: int = MAX_AI_ANALYSES,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run Setup Judge on refreshed deterministic pass candidates."""
    selected = list(enriched_candidates or [])
    if max_ai_analyses > 0:
        selected = selected[:max_ai_analyses]

    stats = {"calls": 0, "parse_successes": 0, "failures": 0}
    judged_candidates: list[dict[str, Any]] = []

    for candidate in selected:
        symbol = str(candidate.get("Symbol") or "").strip().upper()
        if not symbol:
            continue

        print(f"Running Setup Judge for {symbol}...", flush=True)
        technicals = candidate.get("technicals")
        today_focus = candidate.get("today_focus")
        focus_structure = candidate.get("focus_structure")
        if not isinstance(technicals, dict):
            technicals = {}
        if not isinstance(today_focus, dict):
            today_focus = {}
        if not isinstance(focus_structure, dict):
            focus_structure = {}

        judge_context = build_setup_judge_context(
            candidate, technicals, today_focus, focus_structure
        )
        setup_judge = analyze_setup_with_claude(symbol, judge_context)
        stats["calls"] += 1
        if isinstance(setup_judge, dict) and setup_judge.get("setup_judge_failed"):
            stats["failures"] += 1
        else:
            stats["parse_successes"] += 1

        judged = dict(candidate)
        judged["setup_judge"] = setup_judge
        judged_candidates.append(judged)

    return judged_candidates, stats


def _build_setup_judge_veto_analysis(
    candidate: dict[str, Any],
    today_focus: dict[str, Any],
    focus_structure: dict[str, Any],
    setup_judge: dict[str, Any],
    audit_path: str,
) -> dict[str, Any]:
    """Build a report entry for candidates vetoed by Setup Judge."""
    symbol = str(candidate.get("Symbol") or "").strip().upper()
    veto_reasons = (
        setup_judge.get("veto_reasons")
        if isinstance(setup_judge.get("veto_reasons"), list)
        else []
    )
    thesis = setup_judge.get("one_line_thesis") or "Setup Judge vetoed this candidate."
    final_pre_ai_score = _as_float(candidate.get("FinalPreAIScore"))

    analysis = {
        "symbol": symbol,
        "overall_score": _as_float(setup_judge.get("judge_rank_score")),
        "bias": "setup_judge_veto",
        "setup_type": candidate.get("StructureType") or focus_structure.get("structure_type"),
        "setup_quality": setup_judge.get("setup_grade") or "F",
        "actionability": "reject",
        "trigger_level": setup_judge.get("trigger_level"),
        "invalidation_level": setup_judge.get("invalidation_level"),
        "do_not_chase_above": setup_judge.get("do_not_chase_above"),
        "entry_idea": "Setup Judge vetoed full Claude analysis.",
        "stop_idea": "Use deterministic invalidation only if manually reviewed.",
        "target_idea": "No target because Setup Judge vetoed this setup.",
        "same_day_plan": setup_judge.get("watch_plan") or ["Do not use without manual review."],
        "why_today": thesis,
        "final_verdict": f"Setup Judge veto: {thesis}",
        "warnings": veto_reasons or ["Setup Judge vetoed this candidate."],
        "disqualifiers": veto_reasons,
        "today_focus": today_focus,
        "focus_structure": focus_structure,
        "technical_pre_ai_score": _as_float(candidate.get("TechnicalPreAIScore")),
        "today_focus_score": _as_float(candidate.get("TodayFocusScore")),
        "focus_structure_score": _as_float(candidate.get("FocusStructureScore")),
        "blueprint_setup_score": _as_float(candidate.get("BlueprintSetupScore")),
        "blueprint_fit_score": _as_float(candidate.get("BlueprintFitScore")),
        "blueprint_setup_type": candidate.get("BlueprintSetupType"),
        "sector_alignment_score": _as_float(candidate.get("SectorAlignmentScore")),
        "structure_type": candidate.get("StructureType"),
        "final_pre_ai_score": final_pre_ai_score,
        "audit_source": audit_path,
        "setup_judge": setup_judge,
        "setup_judge_veto": True,
        "deterministic_only": True,
    }
    return attach_candidate_context_to_analysis(analysis, candidate)


def run_analysis_from_audit(
    audit_path: str,
    max_ai_analyses: int = MAX_AI_ANALYSES,
    output_dir: str = "reports",
    dry_run: bool = False,
    use_setup_judge: bool = False,
) -> str:
    """Run Claude or deterministic-only analysis from a saved focus gate audit."""
    start_time = time.perf_counter()
    if max_ai_analyses < 0:
        raise ValueError("max_ai_analyses must be zero or a positive integer.")

    def finish(filepath: str) -> str:
        elapsed = time.perf_counter() - start_time
        print(f"Report path: {filepath}", flush=True)
        print(f"Total runtime: {_format_elapsed(elapsed)}", flush=True)
        return filepath

    print("Starting audit-based analysis...", flush=True)
    print("Configuration:", flush=True)
    print("- Mode: analyze_from_audit", flush=True)
    print(f"- Audit path: {audit_path}", flush=True)
    print(f"- Max AI analyses cap: {max_ai_analyses}", flush=True)
    print(f"- Dry run: {dry_run}", flush=True)
    print(f"- Output dir: {output_dir}", flush=True)
    if use_setup_judge:
        print("- Setup Judge: enabled", flush=True)
    if dry_run:
        print("Dry run enabled: Claude will not be called.", flush=True)

    print("Calculating sector leadership...", flush=True)
    sector_leadership = load_sector_leadership_table()
    print("Calculating market context...", flush=True)
    market_context = evaluate_market_context()

    path = Path(audit_path).expanduser()
    if not path.exists():
        message = f"Audit CSV not found: {audit_path}"
        print(message, flush=True)
        filepath = _error_report(
            output_dir,
            message,
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    try:
        selected_candidates = load_candidates_from_audit(
            str(path), max_ai_analyses=max_ai_analyses
        )
    except Exception as exc:
        message = f"Could not load audit CSV: {exc}"
        print(message, flush=True)
        filepath = _error_report(
            output_dir,
            message,
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    if not selected_candidates:
        print("No passed candidates found in audit. Claude was not called.", flush=True)
        print("Claude calls: 0", flush=True)
        filepath = _no_audit_candidates_report(
            output_dir,
            str(path),
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    analyses: list[dict[str, Any]] = []
    enriched_candidates: list[dict[str, Any]] = []
    selected_tickers = [
        str(candidate.get("Symbol") or "").strip().upper()
        for candidate in selected_candidates
        if str(candidate.get("Symbol") or "").strip()
    ]
    print(f"Fetching fresh OHLCV for selected tickers: {', '.join(selected_tickers)}", flush=True)

    for candidate in selected_candidates:
        symbol = str(candidate.get("Symbol") or "").strip().upper()
        if not symbol:
            continue

        print(f"Refreshing deterministic context for {symbol}...", flush=True)
        try:
            df = fetch_stock_data(symbol, period="6mo", interval="1d")
            enriched = dict(candidate)
            enriched.update(
                {
                    "Symbol": symbol,
                    "ohlcv": df,
                }
            )
            enriched = apply_primary_universe_gate(
                enriched, sector_leadership, ohlcv=df
            )
            if not _as_bool(enriched.get("primary_gate_pass")):
                reason = enriched.get("primary_gate_fail_reasons") or "unknown"
                print(
                    f"Skipping {symbol}: primary hard gate failed: {reason}",
                    flush=True,
                )
                failed_analysis = build_failed_analysis(
                    symbol, f"Primary hard gate failed: {reason}"
                )
                failed_analysis["audit_source"] = str(path)
                failed_analysis = attach_candidate_context_to_analysis(
                    failed_analysis, enriched
                )
                analyses.append(failed_analysis)
                continue

            technicals = analyze_stock_technicals(symbol, df)
            today_focus = evaluate_today_focus(symbol, technicals)
            focus_structure = evaluate_focus_structure(symbol, df, technicals)
            technical_score = score_technicals_pre_ai(symbol, enriched, technicals)
            today_focus_score = _as_float(today_focus.get("today_focus_score")) or 0.0
            focus_structure_score = (
                _as_float(focus_structure.get("focus_structure_score")) or 0.0
            )
            blueprint_setup_score = (
                _as_float(focus_structure.get("blueprint_setup_score")) or 0.0
            )
            blueprint_fit_score = (
                _as_float(focus_structure.get("blueprint_fit_score")) or 0.0
            )
            sector_alignment_score = score_sector_alignment(enriched)
            final_pre_ai_score = (
                (0.15 * technical_score)
                + (0.25 * today_focus_score)
                + (0.25 * focus_structure_score)
                + (0.20 * blueprint_fit_score)
                + (0.15 * sector_alignment_score)
            )
            enriched.update(
                {
                    "technicals": technicals,
                    "today_focus": today_focus,
                    "focus_structure": focus_structure,
                    "TechnicalPreAIScore": technical_score,
                    "TodayFocusScore": today_focus_score,
                    "FocusStructureScore": focus_structure_score,
                    "BlueprintSetupScore": blueprint_setup_score,
                    "BlueprintFitScore": blueprint_fit_score,
                    "BlueprintSetupType": focus_structure.get("blueprint_setup_type"),
                    "SectorAlignmentScore": sector_alignment_score,
                    "StructureType": focus_structure.get("structure_type"),
                    "FinalPreAIScore": final_pre_ai_score,
                }
            )
            focus_gate_failures = get_focus_gate_failure_reasons(enriched)
            if focus_gate_failures:
                reason = _join_items(focus_gate_failures)
                print(
                    f"Skipping {symbol}: refreshed focus gate failed: {reason}",
                    flush=True,
                )
                failed_analysis = build_failed_analysis(
                    symbol, f"Refreshed focus gate failed: {reason}"
                )
                failed_analysis["audit_source"] = str(path)
                failed_analysis = attach_candidate_context_to_analysis(
                    failed_analysis, enriched
                )
                analyses.append(failed_analysis)
                continue

            enriched_candidates.append(enriched)
        except Exception as exc:
            print(f"Skipping {symbol}: {exc}", flush=True)
            failed_analysis = build_failed_analysis(
                symbol, f"Audit candidate refresh failed: {exc}"
            )
            failed_analysis["audit_source"] = str(path)
            failed_analysis["final_pre_ai_score"] = _as_float(
                candidate.get("FinalPreAIScore")
            )
            analyses.append(failed_analysis)

    if dry_run or max_ai_analyses == 0:
        reason = "dry-run mode enabled" if dry_run else "max_ai_analyses is 0"
        print(f"Claude analysis was not run because {reason}.", flush=True)
        for candidate in enriched_candidates:
            analyses.append(
                _build_deterministic_only_analysis(
                    candidate,
                    candidate.get("today_focus") or {},
                    candidate.get("focus_structure") or {},
                    str(path),
                )
            )
        print("Claude calls: 0", flush=True)
        print("Generating report...", flush=True)
        filepath = generate_and_save_report(
            analyses,
            output_dir=output_dir,
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    setup_judge_stats = {"calls": 0, "parse_successes": 0, "failures": 0}
    if use_setup_judge:
        print("Running Setup Judge from audit candidates...", flush=True)
        enriched_candidates, setup_judge_stats = apply_setup_judge_to_candidates(
            enriched_candidates, max_ai_analyses=max_ai_analyses
        )
        print(f"Setup Judge calls: {setup_judge_stats['calls']}", flush=True)
        print(
            f"Setup Judge parse successes: {setup_judge_stats['parse_successes']}",
            flush=True,
        )
        print(f"Setup Judge failures: {setup_judge_stats['failures']}", flush=True)

    print("Running Claude analysis from audit candidates...", flush=True)
    claude_calls = 0
    claude_parse_successes = 0
    claude_failures = 0
    for candidate in enriched_candidates:
        symbol = str(candidate.get("Symbol") or "").strip().upper()
        if not symbol:
            continue

        setup_judge = candidate.get("setup_judge")
        if (
            use_setup_judge
            and isinstance(setup_judge, dict)
            and (
                setup_judge.get("judge_action") == "veto"
                or not bool(setup_judge.get("manual_review_pass", True))
            )
        ):
            print(
                f"Setup Judge vetoed {symbol}; skipping full Claude analysis.",
                flush=True,
            )
            analyses.append(
                _build_setup_judge_veto_analysis(
                    candidate,
                    candidate.get("today_focus") or {},
                    candidate.get("focus_structure") or {},
                    setup_judge,
                    str(path),
                )
            )
            continue

        print(f"Analyzing {symbol} with Claude...", flush=True)
        try:
            technicals = candidate.get("technicals")
            if not isinstance(technicals, dict):
                raise ValueError("Technical analysis is missing.")

            today_focus = candidate.get("today_focus")
            focus_structure = candidate.get("focus_structure")
            claude_context = dict(technicals)
            if isinstance(today_focus, dict):
                claude_context["today_focus"] = today_focus
            if isinstance(focus_structure, dict):
                claude_context["focus_structure"] = focus_structure
            claude_context["primary_gate"] = {
                "primary_gate_pass": candidate.get("primary_gate_pass"),
                "price": _as_float(candidate.get("price")),
                "sma21": _as_float(candidate.get("sma21")),
                "sma50": _as_float(candidate.get("sma50")),
                "market_cap": _as_float(candidate.get("market_cap")),
                "atr": _as_float(candidate.get("atr")),
                "avg_volume": _as_float(candidate.get("avg_volume")),
            }
            claude_context["sector_leadership"] = {
                "sector_name": candidate.get("sector_name") or candidate.get("Sector"),
                "sector_etf": candidate.get("sector_etf"),
                "sector_rank": _as_float(candidate.get("sector_rank")),
                "sector_score": _as_float(candidate.get("sector_score")),
                "sector_perf_1m": _as_float(candidate.get("sector_perf_1m")),
                "sector_perf_3m": _as_float(candidate.get("sector_perf_3m")),
                "stock_perf_1m": _as_float(candidate.get("stock_perf_1m")),
                "stock_perf_3m": _as_float(candidate.get("stock_perf_3m")),
                "relative_strength_1m": _as_float(
                    candidate.get("relative_strength_1m")
                ),
                "relative_strength_3m": _as_float(
                    candidate.get("relative_strength_3m")
                ),
                "sector_alignment_score": _as_float(
                    candidate.get("SectorAlignmentScore")
                ),
            }
            claude_context["market_context"] = market_context

            analysis = analyze_with_claude(symbol, claude_context)
            claude_calls += 1
            if isinstance(analysis, dict) and analysis.get("analysis_failed") is True:
                claude_failures += 1
            else:
                claude_parse_successes += 1
            if isinstance(analysis, dict):
                analysis = _attach_audit_scores(
                    analysis,
                    candidate,
                    today_focus if isinstance(today_focus, dict) else {},
                    focus_structure if isinstance(focus_structure, dict) else {},
                    str(path),
                )
                if isinstance(setup_judge, dict):
                    analysis = attach_setup_judge(analysis, setup_judge)
            analyses.append(analysis)
        except Exception as exc:
            print(f"Skipping {symbol}: {exc}", flush=True)
            claude_failures += 1
            failed_analysis = build_failed_analysis(symbol, f"Analysis failed: {exc}")
            failed_analysis = _attach_audit_scores(
                failed_analysis,
                candidate,
                candidate.get("today_focus") or {},
                candidate.get("focus_structure") or {},
                str(path),
            )
            if isinstance(setup_judge, dict):
                failed_analysis = attach_setup_judge(failed_analysis, setup_judge)
            analyses.append(failed_analysis)

    print(f"Selected tickers: {', '.join(selected_tickers)}", flush=True)
    if use_setup_judge:
        print(f"Full Claude calls: {claude_calls}", flush=True)
        print(f"Full Claude parse successes: {claude_parse_successes}", flush=True)
        print(f"Full Claude failures: {claude_failures}", flush=True)
    else:
        print(f"Claude calls: {claude_calls}", flush=True)
    print("Generating report...", flush=True)
    filepath = generate_and_save_report(
        analyses,
        output_dir=output_dir,
        sector_leadership=sector_leadership_records(sector_leadership),
        market_context=market_context,
    )
    print(f"Saved report: {filepath}", flush=True)
    return finish(filepath)


def run_premarket_scan(
    max_ai_analyses: int = MAX_AI_ANALYSES,
    max_candidates_to_score: Optional[int] = MAX_CANDIDATES_TO_SCORE,
    output_dir: str = "reports",
    universe_mode: str = UNIVERSE_MODE,
    max_universe_size: Optional[int] = MAX_UNIVERSE_SIZE,
    dry_run: bool = False,
    generate_detector_charts: bool = GENERATE_DETECTOR_CHARTS,
    detector_chart_limit: Optional[int] = DETECTOR_CHART_LIMIT,
) -> str:
    """
    Run the end-to-end MVP scanner and save a Markdown premarket report.
    """
    start_time = time.perf_counter()
    effective_max_ai_analyses = 0 if dry_run else max_ai_analyses
    if effective_max_ai_analyses < 0:
        raise ValueError("max_ai_analyses must be zero or a positive integer.")

    def finish(filepath: str) -> str:
        elapsed = time.perf_counter() - start_time
        print(f"Report path: {filepath}", flush=True)
        print(f"Total runtime: {_format_elapsed(elapsed)}", flush=True)
        return filepath

    print("Starting premarket scan...", flush=True)
    print("Configuration:", flush=True)
    print(f"- Universe mode: {universe_mode}", flush=True)
    print(f"- Max universe size: {max_universe_size}", flush=True)
    score_limit_text = (
        "all hard-gate survivors"
        if max_candidates_to_score is None
        else str(max_candidates_to_score)
    )
    print(f"- Max candidates to score: {score_limit_text}", flush=True)
    print(f"- Max AI analyses cap: {effective_max_ai_analyses}", flush=True)
    print(f"- Dry run: {dry_run}", flush=True)
    print(f"- Generate detector charts: {generate_detector_charts}", flush=True)
    if detector_chart_limit is not None:
        print(f"- Detector chart limit: {detector_chart_limit}", flush=True)
    print(f"- Output dir: {output_dir}", flush=True)
    if dry_run:
        print("Dry run enabled: Claude will not be called.", flush=True)
    print("Calculating sector leadership...", flush=True)
    sector_leadership = load_sector_leadership_table()
    print("Calculating market context...", flush=True)
    market_context = evaluate_market_context()
    if str(universe_mode).strip().lower() == "us_listed":
        print("Loading broad U.S.-listed universe...", flush=True)
    else:
        print("Loading S&P 500 universe...", flush=True)
    print("Loading symbol rows for primary hard gate...", flush=True)

    try:
        candidates = scan_candidates(
            universe_mode=universe_mode,
            max_universe_size=max_universe_size,
        )
    except Exception as exc:
        print(f"Candidate scan failed: {exc}", flush=True)
        print("Generating report...", flush=True)
        filepath = _error_report(
            output_dir,
            f"Candidate scan failed: {exc}",
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    raw_universe_count = candidates.attrs.get("raw_universe_count")
    scanned_universe_count = candidates.attrs.get("scanned_universe_count")
    if raw_universe_count is not None:
        print(f"Raw universe count: {raw_universe_count}", flush=True)
    if scanned_universe_count is not None:
        print(f"Symbols scanned from universe: {scanned_universe_count}", flush=True)

    if candidates.empty:
        print("No symbols were available for the primary hard gate.", flush=True)
        detector_paths = run_post_primary_detector_stage(
            pd.DataFrame(),
            output_dir=output_dir,
            primary_gated_count=0,
            generate_charts=generate_detector_charts,
            detector_chart_limit=detector_chart_limit,
        )
        print(
            f"Detector stage saved report: {detector_paths.get('report')}",
            flush=True,
        )
        audit_filepath = save_focus_gate_audit([], output_dir=output_dir)
        print(f"Focus gate audit saved: {audit_filepath}", flush=True)
        print("Selected for Claude: 0", flush=True)
        print("Claude calls: 0", flush=True)
        print("Generating report...", flush=True)
        filepath = _no_candidates_report(
            output_dir,
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    scanned_count = len(candidates)
    print(f"Total symbols scanned: {scanned_count}", flush=True)
    print("Applying primary hard universe gate...", flush=True)
    primary_survivors = apply_primary_universe_gate_to_candidates(
        candidates, sector_leadership
    )
    primary_gate_results = primary_survivors.attrs.get(
        "primary_gate_results", pd.DataFrame()
    )
    hard_gate_survivors = primary_survivors.attrs.get(
        "primary_gate_survivor_count", len(primary_survivors)
    )
    hard_gate_rejected = primary_survivors.attrs.get(
        "primary_gate_rejected_count", scanned_count - len(primary_survivors)
    )
    print(f"Hard gate survivors: {hard_gate_survivors}", flush=True)
    print(f"Hard gate rejected: {hard_gate_rejected}", flush=True)

    if primary_survivors.empty:
        print("No symbols passed the primary hard universe gate.", flush=True)
        detector_paths = run_post_primary_detector_stage(
            primary_survivors,
            output_dir=output_dir,
            primary_gated_count=0,
            generate_charts=generate_detector_charts,
            detector_chart_limit=detector_chart_limit,
        )
        print(
            f"Detector stage saved report: {detector_paths.get('report')}",
            flush=True,
        )
        audit_filepath = save_focus_gate_audit(
            build_combined_audit_candidates(primary_gate_results, pd.DataFrame()),
            output_dir=output_dir,
        )
        print(f"Focus gate audit saved: {audit_filepath}", flush=True)
        print("Final technical shortlist count: 0", flush=True)
        print("Selected for Claude: 0", flush=True)
        print("Claude calls: 0", flush=True)
        print("Generating report...", flush=True)
        filepath = _no_qualified_report(
            output_dir,
            scanned_count=scanned_count,
            attempted_count=0,
            scored_count=0,
            qualified_count=0,
            hard_gate_survivors=hard_gate_survivors,
            hard_gate_rejected=hard_gate_rejected,
            audit_filepath=audit_filepath,
            sector_leadership=sector_leadership_records(sector_leadership),
            market_context=market_context,
        )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    detector_paths = run_post_primary_detector_stage(
        primary_survivors,
        output_dir=output_dir,
        primary_gated_count=hard_gate_survivors,
        generate_charts=generate_detector_charts,
        detector_chart_limit=detector_chart_limit,
    )
    print(f"Detector stage saved CSV: {detector_paths.get('csv')}", flush=True)
    print(f"Detector stage saved report: {detector_paths.get('report')}", flush=True)

    print("Building technical and focus-structure shortlist...", flush=True)
    selected_candidates = build_technical_shortlist(
        primary_survivors,
        max_ai_analyses=effective_max_ai_analyses,
        max_candidates_to_score=max_candidates_to_score,
    )

    attempted_count = selected_candidates.attrs.get("attempted_count", 0)
    scored_count = selected_candidates.attrs.get("scored_count", 0)
    qualified_count = selected_candidates.attrs.get("qualified_count", 0)
    all_scored_results = selected_candidates.attrs.get(
        "all_scored_results", selected_candidates
    )
    qualified_candidates = (
        all_scored_results[all_scored_results["PassedFocusQualityGate"]].copy()
        if isinstance(all_scored_results, pd.DataFrame)
        and "PassedFocusQualityGate" in all_scored_results.columns
        else pd.DataFrame()
    )
    audit_candidates = build_combined_audit_candidates(
        primary_gate_results, all_scored_results
    )
    audit_filepath = save_focus_gate_audit(audit_candidates, output_dir=output_dir)

    print(f"Scanned: {scanned_count}", flush=True)
    print(f"Hard gate survivors: {hard_gate_survivors}", flush=True)
    print(f"Hard gate rejected: {hard_gate_rejected}", flush=True)
    print(f"Technically scored: {scored_count}", flush=True)
    print(f"Qualified after focus gates: {qualified_count}", flush=True)
    print(f"Final technical shortlist count: {len(selected_candidates)}", flush=True)
    print(f"Selected for Claude: {len(selected_candidates)}", flush=True)
    print(f"Focus gate audit saved: {audit_filepath}", flush=True)

    if selected_candidates.empty:
        if qualified_count == 0:
            print("No candidates passed focus gates. Claude was not called.", flush=True)
        else:
            print("Qualified candidates exist, but Claude was not called.", flush=True)
        print("Claude calls: 0", flush=True)
        print("Generating report...", flush=True)
        if qualified_count > 0 and effective_max_ai_analyses == 0:
            reason = "dry-run mode enabled" if dry_run else "max_ai_analyses is 0"
            filepath = _no_ai_report(
                output_dir,
                reason=reason,
                scanned_count=scanned_count,
                attempted_count=attempted_count,
                scored_count=scored_count,
                qualified_count=qualified_count,
                selected_count=len(selected_candidates),
                hard_gate_survivors=hard_gate_survivors,
                hard_gate_rejected=hard_gate_rejected,
                audit_filepath=audit_filepath,
                qualified_candidates=qualified_candidates,
                sector_leadership=sector_leadership_records(sector_leadership),
                market_context=market_context,
            )
        else:
            filepath = _no_qualified_report(
                output_dir,
                scanned_count=scanned_count,
                attempted_count=attempted_count,
                scored_count=scored_count,
                qualified_count=qualified_count,
                hard_gate_survivors=hard_gate_survivors,
                hard_gate_rejected=hard_gate_rejected,
                audit_filepath=audit_filepath,
                sector_leadership=sector_leadership_records(sector_leadership),
                market_context=market_context,
            )
        print(f"Saved report: {filepath}", flush=True)
        return finish(filepath)

    print(
        "Technical/focus candidates scored successfully: "
        f"{scored_count} of {attempted_count}",
        flush=True,
    )

    print("Selected for Claude:", flush=True)
    for idx, row in selected_candidates.iterrows():
        print(
            f"{idx + 1}. {row['Symbol']} — "
            f"Final: {row['FinalPreAIScore']:.1f} — "
            f"Tech: {row['TechnicalPreAIScore']:.1f} — "
            f"Today: {row['TodayFocusScore']:.1f} — "
            f"Structure: {row['FocusStructureScore']:.1f} — "
            f"Blueprint: {row.get('BlueprintSetupScore', 0):.1f} — "
            f"Fit: {row.get('BlueprintFitScore', 0):.1f} — "
            f"Sector: {row.get('SectorAlignmentScore', 0):.1f} — "
            f"{row['Actionability']} — "
            f"{row['StructureType']} — "
            f"{row.get('BlueprintSetupType') or 'no_blueprint_setup'}",
            flush=True,
        )

    print("Running Claude analysis...", flush=True)
    analyses = []
    claude_calls = 0
    vision_reviews_run = 0
    if USE_VISION_REVIEW:
        print(
            f"Vision review enabled for up to {MAX_VISION_REVIEWS} selected candidates.",
            flush=True,
        )

    for _, row in selected_candidates.iterrows():
        symbol = str(row["Symbol"]).strip().upper()
        if not symbol:
            continue

        print(f"Analyzing {symbol} with Claude...", flush=True)

        try:
            technicals = row.get("technicals")
            if not isinstance(technicals, dict):
                raise ValueError("Precomputed technical analysis is missing.")

            today_focus = row.get("today_focus")
            claude_context = dict(technicals)
            if isinstance(today_focus, dict):
                claude_context["today_focus"] = today_focus
            focus_structure = row.get("focus_structure")
            if isinstance(focus_structure, dict):
                claude_context["focus_structure"] = focus_structure
            claude_context["primary_gate"] = {
                "primary_gate_pass": row.get("primary_gate_pass"),
                "price": _as_float(row.get("price")),
                "sma21": _as_float(row.get("sma21")),
                "sma50": _as_float(row.get("sma50")),
                "market_cap": _as_float(row.get("market_cap")),
                "atr": _as_float(row.get("atr")),
                "avg_volume": _as_float(row.get("avg_volume")),
            }
            claude_context["sector_leadership"] = {
                "sector_name": row.get("sector_name") or row.get("Sector"),
                "sector_etf": row.get("sector_etf"),
                "sector_rank": _as_float(row.get("sector_rank")),
                "sector_score": _as_float(row.get("sector_score")),
                "sector_perf_1m": _as_float(row.get("sector_perf_1m")),
                "sector_perf_3m": _as_float(row.get("sector_perf_3m")),
                "stock_perf_1m": _as_float(row.get("stock_perf_1m")),
                "stock_perf_3m": _as_float(row.get("stock_perf_3m")),
                "relative_strength_1m": _as_float(row.get("relative_strength_1m")),
                "relative_strength_3m": _as_float(row.get("relative_strength_3m")),
                "sector_alignment_score": _as_float(row.get("SectorAlignmentScore")),
            }
            claude_context["market_context"] = market_context

            analysis = analyze_with_claude(symbol, claude_context)
            claude_calls += 1
            if isinstance(analysis, dict) and isinstance(today_focus, dict):
                analysis["today_focus"] = today_focus
                analysis["technical_pre_ai_score"] = row.get("TechnicalPreAIScore")
                if isinstance(focus_structure, dict):
                    analysis["focus_structure"] = focus_structure
                    analysis["focus_structure_score"] = row.get("FocusStructureScore")
                    analysis["structure_type"] = row.get("StructureType")
                    analysis["blueprint_setup_score"] = row.get("BlueprintSetupScore")
                    analysis["blueprint_fit_score"] = row.get("BlueprintFitScore")
                    analysis["blueprint_setup_type"] = row.get("BlueprintSetupType")
                analysis["sector_alignment_score"] = row.get("SectorAlignmentScore")
                analysis["final_pre_ai_score"] = row.get("FinalPreAIScore")
                analysis = attach_candidate_context_to_analysis(analysis, row)
            if (
                USE_VISION_REVIEW
                and vision_reviews_run < MAX_VISION_REVIEWS
                and isinstance(analysis, dict)
            ):
                try:
                    from chart_generator import generate_chart_image
                    from vision_reviewer import review_chart_with_claude_vision

                    chart_df = row.get("ohlcv")
                    if not isinstance(chart_df, pd.DataFrame):
                        chart_df = fetch_stock_data(symbol, period="6mo", interval="1d")

                    print(f"Generating chart image for {symbol}...", flush=True)
                    chart_path = generate_chart_image(symbol, chart_df)

                    print(f"Running vision chart review for {symbol}...", flush=True)
                    analysis["vision_review"] = review_chart_with_claude_vision(
                        symbol, chart_path, technicals
                    )
                    vision_reviews_run += 1
                except Exception as exc:
                    analysis["vision_review"] = {
                        "symbol": symbol,
                        "vision_review_failed": True,
                        "error": str(exc),
                    }

            analyses.append(analysis)
        except Exception as exc:
            print(f"Skipping {symbol}: {exc}", flush=True)
            failed_analysis = build_failed_analysis(symbol, f"Analysis failed: {exc}")
            today_focus = row.get("today_focus")
            if isinstance(today_focus, dict):
                failed_analysis["today_focus"] = today_focus
            focus_structure = row.get("focus_structure")
            if isinstance(focus_structure, dict):
                failed_analysis["focus_structure"] = focus_structure
                failed_analysis["focus_structure_score"] = row.get("FocusStructureScore")
                failed_analysis["structure_type"] = row.get("StructureType")
                failed_analysis["blueprint_setup_score"] = row.get("BlueprintSetupScore")
                failed_analysis["blueprint_fit_score"] = row.get("BlueprintFitScore")
                failed_analysis["blueprint_setup_type"] = row.get("BlueprintSetupType")
            failed_analysis["sector_alignment_score"] = row.get("SectorAlignmentScore")
            failed_analysis["final_pre_ai_score"] = row.get("FinalPreAIScore")
            failed_analysis = attach_candidate_context_to_analysis(failed_analysis, row)
            analyses.append(failed_analysis)

    print(f"Claude calls: {claude_calls}", flush=True)
    print("Generating report...", flush=True)
    filepath = generate_and_save_report(
        analyses,
        output_dir=output_dir,
        sector_leadership=sector_leadership_records(sector_leadership),
        market_context=market_context,
    )
    print(f"Saved report: {filepath}", flush=True)
    return finish(filepath)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for scanner runs."""
    parser = argparse.ArgumentParser(
        description="Run the trading_system daily focus-list scanner."
    )
    parser.add_argument(
        "--universe",
        choices=["sp500", "us_listed"],
        default=UNIVERSE_MODE,
        help="Universe mode to scan.",
    )
    parser.add_argument(
        "--max-universe-size",
        type=int,
        default=MAX_UNIVERSE_SIZE,
        help="Maximum symbols to scan from the selected universe. Omit for full universe.",
    )
    parser.add_argument(
        "--max-ai-analyses",
        type=int,
        default=MAX_AI_ANALYSES,
        help="Maximum Claude calls. This is a cap, not a target. Use 0 for no Claude.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scan/filter/score/gates/audit/report with zero Claude calls.",
    )
    parser.add_argument(
        "--max-candidates-to-score",
        type=int,
        default=MAX_CANDIDATES_TO_SCORE,
        help=(
            "Maximum hard-gate survivors to run through full technical/focus "
            "scoring. Omit to score every hard-gate survivor."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory for generated reports and audit CSV files.",
    )
    parser.add_argument(
        "--analyze-from-audit",
        default=None,
        help=(
            "Load passed candidates from a focus gate audit CSV and analyze them "
            "without rescanning the universe."
        ),
    )
    parser.add_argument(
        "--detectors-from-audit",
        default=None,
        help=(
            "Run only the post-primary detector stage from a saved focus gate "
            "audit CSV. Primary-gate pass rows are refreshed with fresh OHLCV."
        ),
    )
    parser.add_argument(
        "--generate-detector-charts",
        action="store_true",
        help=(
            "Generate standardized 6M and 1Y detector charts for rows where "
            "chart_needed is true."
        ),
    )
    parser.add_argument(
        "--detector-chart-limit",
        type=int,
        default=DETECTOR_CHART_LIMIT,
        help="Maximum detector chart sets to generate. Omit for no cap.",
    )
    parser.add_argument(
        "--use-setup-judge",
        action="store_true",
        help=(
            "Run Claude Setup Judge v1 before full Claude analysis in "
            "analyze-from-audit mode."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Run the scanner from the command line."""
    args = parse_args()
    if args.detectors_from_audit:
        detector_paths = run_detectors_from_audit(
            audit_path=args.detectors_from_audit,
            output_dir=args.output_dir,
            generate_charts=args.generate_detector_charts,
            detector_chart_limit=args.detector_chart_limit,
        )
        print(f"Final detector report location: {detector_paths.get('report')}", flush=True)
        return

    if args.analyze_from_audit:
        filepath = run_analysis_from_audit(
            audit_path=args.analyze_from_audit,
            max_ai_analyses=args.max_ai_analyses,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            use_setup_judge=args.use_setup_judge,
        )
    else:
        if args.use_setup_judge:
            print(
                "Setup Judge v1 is currently implemented only for --analyze-from-audit; "
                "normal scan behavior is unchanged.",
                flush=True,
            )
        filepath = run_premarket_scan(
            max_ai_analyses=args.max_ai_analyses,
            max_candidates_to_score=args.max_candidates_to_score,
            output_dir=args.output_dir,
            universe_mode=args.universe,
            max_universe_size=args.max_universe_size,
            dry_run=args.dry_run,
            generate_detector_charts=args.generate_detector_charts,
            detector_chart_limit=args.detector_chart_limit,
        )
    print(f"Final report location: {filepath}", flush=True)


if __name__ == "__main__":
    main()

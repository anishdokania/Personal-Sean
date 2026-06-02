"""
End-to-end MVP orchestrator for the trading_system scanner.

Module 7 connects the completed modules into a decision-support workflow:
sector scan, stock filtering, deterministic pre-AI selection, Claude analysis,
and Markdown report generation. It does not place orders or auto-trade.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from claude_analyzer import analyze_with_claude, build_failed_analysis
from data_fetcher import fetch_stock_data
from report import generate_and_save_report, save_report
from stock_filter import scan_candidates
from technical import analyze_stock_technicals
from today_focus import evaluate_today_focus


MAX_AI_ANALYSES = 5
MAX_CANDIDATES_TO_SCORE = 50
USE_VISION_REVIEW = False
MAX_VISION_REVIEWS = 5


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
    max_candidates_to_score: int = MAX_CANDIDATES_TO_SCORE,
) -> pd.DataFrame:
    """
    Score candidates with Module 4 technicals and return the final Claude shortlist.
    """
    if max_ai_analyses <= 0:
        raise ValueError("max_ai_analyses must be a positive integer.")
    if max_candidates_to_score <= 0:
        raise ValueError("max_candidates_to_score must be a positive integer.")

    if candidates_df is None or candidates_df.empty:
        return pd.DataFrame()

    basic_limit = min(max_candidates_to_score, len(candidates_df))
    basic_candidates = select_candidates_for_ai(
        candidates_df, max_ai_analyses=basic_limit
    )
    print(
        f"Reducing to top {len(basic_candidates)} candidates by basic PreAIScore before technical scoring...",
        flush=True,
    )

    scored_rows = []
    failures = {}

    for _, candidate in basic_candidates.iterrows():
        symbol = str(candidate.get("Symbol", "")).strip().upper()
        if not symbol:
            continue

        print(f"Scoring technicals for {symbol}...", flush=True)

        try:
            df = fetch_stock_data(symbol, period="6mo", interval="1d")
            technicals = analyze_stock_technicals(symbol, df)
            technical_score = score_technicals_pre_ai(symbol, candidate, technicals)
            today_focus = evaluate_today_focus(symbol, technicals)
            today_focus_score = _as_float(today_focus.get("today_focus_score")) or 0.0
            final_pre_ai_score = (0.45 * technical_score) + (0.55 * today_focus_score)
        except Exception as exc:
            failures[symbol] = str(exc)
            print(f"Skipping {symbol} during technical scoring: {exc}", flush=True)
            continue

        scored_rows.append(
            {
                "Symbol": symbol,
                "Company": candidate.get("Company"),
                "Sector": candidate.get("Sector"),
                "Close": candidate.get("Close"),
                "AvgVolume20": candidate.get("AvgVolume20"),
                "PreAIScore": candidate.get("PreAIScore"),
                "TechnicalPreAIScore": technical_score,
                "TodayFocusScore": today_focus_score,
                "Actionability": today_focus.get("actionability"),
                "FinalPreAIScore": final_pre_ai_score,
                "ohlcv": df,
                "technicals": technicals,
                "today_focus": today_focus,
            }
        )

    if not scored_rows:
        empty = pd.DataFrame()
        empty.attrs["attempted_count"] = len(basic_candidates)
        empty.attrs["scored_count"] = 0
        empty.attrs["failures"] = failures
        return empty

    technical_results = pd.DataFrame(scored_rows)
    technical_results = technical_results.sort_values(
        by=["FinalPreAIScore", "TodayFocusScore", "TechnicalPreAIScore", "AvgVolume20", "Close"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)

    shortlist = technical_results.head(max_ai_analyses).reset_index(drop=True)
    shortlist.attrs["attempted_count"] = len(basic_candidates)
    shortlist.attrs["scored_count"] = len(technical_results)
    shortlist.attrs["failures"] = failures
    return shortlist


def _no_candidates_report(output_dir: str) -> str:
    """Save a simple report for scans with no passing candidates."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

No candidates passed the deterministic premarket filters.

---

## Detailed Analysis

No AI analysis was run because there were no candidates to review.
"""
    return save_report(markdown_text, output_dir=output_dir)


def _error_report(output_dir: str, message: str) -> str:
    """Save a report when the scan cannot reach the AI-analysis stage."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    markdown_text = f"""# Trading Blueprint Daily Focus List

Generated: {generated_at}

## Summary

The premarket scan did not complete.

---

## Details

{message}
"""
    return save_report(markdown_text, output_dir=output_dir)


def run_premarket_scan(
    top_n_sectors: int = 5,
    max_ai_analyses: int = MAX_AI_ANALYSES,
    max_candidates_to_score: int = MAX_CANDIDATES_TO_SCORE,
    output_dir: str = "reports",
) -> str:
    """
    Run the end-to-end MVP scanner and save a Markdown premarket report.
    """
    print("Starting premarket scan...", flush=True)
    print("Scanning sectors...", flush=True)
    print("Filtering stock candidates...", flush=True)

    try:
        candidates = scan_candidates(top_n_sectors=top_n_sectors)
    except Exception as exc:
        print(f"Candidate scan failed: {exc}", flush=True)
        print("Generating report...", flush=True)
        filepath = _error_report(output_dir, f"Candidate scan failed: {exc}")
        print(f"Saved report: {filepath}", flush=True)
        return filepath

    if candidates.empty:
        print("No candidates passed filters.", flush=True)
        print("Generating report...", flush=True)
        filepath = _no_candidates_report(output_dir)
        print(f"Saved report: {filepath}", flush=True)
        return filepath

    print(f"Candidates found: {len(candidates)}", flush=True)
    print("Building technical shortlist...", flush=True)
    selected_candidates = build_technical_shortlist(
        candidates,
        max_ai_analyses=max_ai_analyses,
        max_candidates_to_score=max_candidates_to_score,
    )

    if selected_candidates.empty:
        print("No candidates selected for AI analysis after technical scoring.", flush=True)
        print("Generating report...", flush=True)
        filepath = _no_candidates_report(output_dir)
        print(f"Saved report: {filepath}", flush=True)
        return filepath

    print(
        "Technical candidates scored successfully: "
        f"{selected_candidates.attrs.get('scored_count', len(selected_candidates))} "
        f"of {selected_candidates.attrs.get('attempted_count', len(selected_candidates))}",
        flush=True,
    )

    print("Selected for AI analysis:", flush=True)
    for idx, row in selected_candidates.iterrows():
        diagnostics_summary = _format_today_diagnostics(row.get("today_focus"))
        print(
            f"{idx + 1}. {row['Symbol']} — "
            f"Tech: {row['TechnicalPreAIScore']} — "
            f"Today: {row['TodayFocusScore']} — "
            f"{row['Actionability']} — "
            f"{diagnostics_summary}",
            flush=True,
        )

    print("Running Claude analysis...", flush=True)
    analyses = []
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

            analysis = analyze_with_claude(symbol, claude_context)
            if isinstance(analysis, dict) and isinstance(today_focus, dict):
                analysis["today_focus"] = today_focus
                analysis["technical_pre_ai_score"] = row.get("TechnicalPreAIScore")
                analysis["final_pre_ai_score"] = row.get("FinalPreAIScore")
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
            analyses.append(failed_analysis)

    print("Generating report...", flush=True)
    filepath = generate_and_save_report(analyses, output_dir=output_dir)
    print(f"Saved report: {filepath}", flush=True)
    return filepath


def main() -> None:
    """Run the MVP scanner from the command line."""
    filepath = run_premarket_scan()
    print(f"Final report location: {filepath}", flush=True)


if __name__ == "__main__":
    main()

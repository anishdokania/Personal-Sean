"""
Markdown report generation for the trading_system scanner.

Module 6 formats already-generated AI analysis dictionaries into readable
decision-support reports. It does not fetch market data, scan symbols, or call
Claude directly.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


Analysis = Dict[str, Any]


def _as_number(value: Any) -> Optional[float]:
    """Return a finite number when possible, otherwise None."""
    if isinstance(value, bool):
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numeric_value):
        return None

    return numeric_value


def _score_for_sort(analysis: Any) -> float:
    """Return a same-day-aware score for sorting, treating missing scores as 0."""
    if not isinstance(analysis, dict):
        return 0.0

    today_focus = analysis.get("today_focus")
    if isinstance(today_focus, dict):
        today_score = _as_number(today_focus.get("today_focus_score"))
        if today_score is not None:
            return today_score

    score = _as_number(analysis.get("overall_score"))
    return score if score is not None else 0.0


def _format_score(score: Any) -> str:
    """Format a score without inventing one when it is missing."""
    numeric_score = _as_number(score)
    if numeric_score is None:
        return "N/A"

    if numeric_score.is_integer():
        return str(int(numeric_score))

    return f"{numeric_score:.1f}"


def _format_price(value: Any) -> Optional[str]:
    """Format numeric price-like values consistently."""
    numeric_value = _as_number(value)
    if numeric_value is None:
        text_value = str(value).strip() if value is not None else ""
        return text_value or None

    return f"{numeric_value:.2f}"


def _clean_text(value: Any, default: str = "Not provided.") -> str:
    """Return clean display text for report fields."""
    if value is None:
        return default

    text_value = str(value).strip()
    return text_value if text_value else default


def _markdown_cell(value: Any, default: str = "Unknown") -> str:
    """Escape compact values for a Markdown table cell."""
    text_value = _clean_text(value, default=default).replace("\n", " ")
    return text_value.replace("|", "\\|")


def _as_list(value: Any) -> List[Any]:
    """Normalize optional list-like values for display."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)

    return [value]


def _format_level_list(levels: Any) -> str:
    """Format support/resistance levels, returning None for empty input."""
    formatted_levels = [
        formatted_level
        for level in _as_list(levels)
        if (formatted_level := _format_price(level))
    ]

    return ", ".join(formatted_levels) if formatted_levels else "None"


def _format_zone(zone: Any) -> Optional[str]:
    """Format one demand/supply zone dictionary."""
    if not isinstance(zone, dict):
        return _clean_text(zone, default="")

    low = _format_price(zone.get("low"))
    high = _format_price(zone.get("high"))
    date = _clean_text(zone.get("date"), default="")

    if low and high:
        zone_text = f"{low}-{high}"
    elif low:
        zone_text = f"low {low}"
    elif high:
        zone_text = f"high {high}"
    else:
        return None

    return f"{zone_text} ({date})" if date else zone_text


def _format_zone_list(zones: Any) -> str:
    """Format a list of demand/supply zones."""
    formatted_zones = [
        formatted_zone
        for zone in _as_list(zones)
        if (formatted_zone := _format_zone(zone))
    ]

    return ", ".join(formatted_zones) if formatted_zones else "None"


def _format_bullets(items: Any) -> str:
    """Format warnings/disqualifiers as Markdown bullets."""
    bullet_items = [
        _clean_text(item, default="")
        for item in _as_list(items)
        if _clean_text(item, default="")
    ]

    if not bullet_items:
        return "- None"

    return "\n".join(f"- {item}" for item in bullet_items)


def _format_raw_response(raw_response: Any) -> str:
    """Format an optional raw AI response for a failed-analysis section."""
    if raw_response is None:
        return ""

    raw_text = str(raw_response).strip()
    if not raw_text:
        return ""

    truncated = raw_text[:1000]
    if len(raw_text) > 1000:
        truncated = f"{truncated}\n...[truncated]"

    truncated = truncated.replace("```", "` ` `")
    return f"""

### Raw Response
```text
{truncated}
```"""


def _format_today_focus(today_focus: Any) -> str:
    """Format the deterministic same-day focus section when present."""
    if not isinstance(today_focus, dict):
        return ""

    why_today = _format_bullets(today_focus.get("why_today"))
    warnings = _format_bullets(today_focus.get("warnings"))
    disqualifiers = _format_bullets(today_focus.get("disqualifiers"))

    return f"""

### Today Focus
**Actionability:** {_clean_text(today_focus.get("actionability"), default="Unknown")}
**Today Focus Score:** {_format_score(today_focus.get("today_focus_score"))}
**Trigger Level:** {_clean_text(_format_price(today_focus.get("trigger_level")), default="None")}
**Invalidation Level:** {_clean_text(_format_price(today_focus.get("invalidation_level")), default="None")}
**Do Not Chase Above:** {_clean_text(_format_price(today_focus.get("do_not_chase_above")), default="None")}
**Preferred Entry Style:** {_clean_text(today_focus.get("preferred_entry_style"), default="Unknown")}

**Same-Day Thesis:** {_clean_text(today_focus.get("same_day_thesis"))}

Why Today:
{why_today}

Today Warnings:
{warnings}

Today Disqualifiers:
{disqualifiers}"""


def _format_failed_analysis(analysis: Analysis) -> str:
    """Format a failed AI analysis as an explicit report section."""
    symbol = _clean_text(analysis.get("symbol"), default="UNKNOWN")
    reason = _clean_text(analysis.get("error"), default="unknown AI analysis failure")
    verdict = _clean_text(
        analysis.get("final_verdict"),
        default=f"AI analysis failed: {reason}",
    )
    raw_response_section = _format_raw_response(analysis.get("raw_response"))
    today_focus_section = _format_today_focus(analysis.get("today_focus"))

    return f"""## {symbol} — AI Analysis Failed

### Reason
{verdict}

### Status
This ticker was selected for analysis, but the AI result was incomplete or invalid. It was not scored as a valid setup.{today_focus_section}{raw_response_section}"""


def _format_bool(value: Any) -> str:
    """Format booleans for report display."""
    if isinstance(value, bool):
        return str(value).lower()

    return _clean_text(value, default="N/A")


def _format_visual_review(vision_review: Any) -> str:
    """Format an optional visual chart review section."""
    if not isinstance(vision_review, dict):
        return ""

    if vision_review.get("vision_review_failed") is True:
        reason = _clean_text(
            vision_review.get("error"), default="unknown vision review failure"
        )
        return f"""

### Visual Chart Review
Vision review failed: {reason}"""

    reasons = _format_bullets(vision_review.get("reasons"))
    warnings = _format_bullets(vision_review.get("warnings"))

    return f"""

### Visual Chart Review
**Visual Score:** {_format_score(vision_review.get("visual_score"))}
**Focus List Candidate:** {_format_bool(vision_review.get("focus_list_candidate"))}
**Visual Setup Type:** {_clean_text(vision_review.get("visual_setup_type"), default="Unknown")}
**Visual Quality:** {_clean_text(vision_review.get("visual_quality"), default="Unknown")}
**Consolidation:** {_clean_text(vision_review.get("consolidation_quality"), default="Unknown")}
**EMA Structure:** {_clean_text(vision_review.get("ema_structure"), default="Unknown")}
**Volume Read:** {_clean_text(vision_review.get("volume_read"), default="Unknown")}
**Extension Risk:** {_clean_text(vision_review.get("extension_risk"), default="Unknown")}

Reasons:
{reasons}

Warnings:
{warnings}

Final Visual Verdict:
{_clean_text(vision_review.get("final_visual_verdict"))}"""


def sort_analyses(analyses: Iterable[Analysis]) -> List[Analysis]:
    """
    Sort analyses by overall_score descending.

    Missing or invalid scores are treated as 0 for sorting only.
    """
    return sorted(list(analyses or []), key=_score_for_sort, reverse=True)


def grade_label(score: Any) -> str:
    """
    Convert a numeric score into a blueprint quality label.
    """
    numeric_score = _as_number(score)
    if numeric_score is None:
        return "Unknown"

    if 90 <= numeric_score <= 100:
        return "Elite"
    if 75 <= numeric_score < 90:
        return "Strong"
    if 60 <= numeric_score < 75:
        return "Watchlist"
    if 40 <= numeric_score < 60:
        return "Weak"
    if 0 <= numeric_score < 40:
        return "Avoid"

    return "Unknown"


def format_single_analysis(analysis: Analysis) -> str:
    """
    Format one AI analysis dictionary as a readable Markdown section.
    """
    analysis = analysis if isinstance(analysis, dict) else {}
    if analysis.get("analysis_failed") is True:
        return _format_failed_analysis(analysis)

    symbol = _clean_text(analysis.get("symbol"), default="UNKNOWN")
    score = analysis.get("overall_score")
    score_text = _format_score(score)
    label = grade_label(score)
    key_levels = analysis.get("key_levels") if isinstance(analysis.get("key_levels"), dict) else {}

    support = _format_level_list(key_levels.get("support"))
    resistance = _format_level_list(key_levels.get("resistance"))
    demand_zones = _format_zone_list(key_levels.get("demand_zones"))
    supply_zones = _format_zone_list(key_levels.get("supply_zones"))

    return f"""## {symbol} — Score: {score_text}/100 ({label})

**Bias:** {_clean_text(analysis.get("bias"), default="Unknown")}
**Setup Type:** {_clean_text(analysis.get("setup_type"), default="Unknown")}
**Setup Quality:** {_clean_text(analysis.get("setup_quality"), default="Unknown")}

### Verdict
{_clean_text(analysis.get("final_verdict"))}
{_format_today_focus(analysis.get("today_focus"))}

### Key Levels
- Support: {support}
- Resistance: {resistance}
- Demand Zones: {demand_zones}
- Supply Zones: {supply_zones}

### Trade Plan Ideas
- Entry: {_clean_text(analysis.get("entry_idea"))}
- Stop: {_clean_text(analysis.get("stop_idea"))}
- Target: {_clean_text(analysis.get("target_idea"))}

### Blueprint Assessment
**EMA:** {_clean_text(analysis.get("ema_assessment"))}

**Volume:** {_clean_text(analysis.get("volume_assessment"))}

**Ignition:** {_clean_text(analysis.get("ignition_assessment"))}

**Accumulation/Distribution:** {_clean_text(analysis.get("accumulation_distribution_assessment"))}{_format_visual_review(analysis.get("vision_review"))}

### Warnings
{_format_bullets(analysis.get("warnings"))}

### Disqualifiers
{_format_bullets(analysis.get("disqualifiers"))}"""


def generate_markdown_report(
    analyses: Iterable[Analysis], title: str = "Trading Blueprint Daily Focus List"
) -> str:
    """
    Generate a full Markdown report with a summary table and per-stock details.
    """
    sorted_analyses = sort_analyses(analyses)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {title}",
        "",
        f"Generated: {generated_at}",
        "",
        "## Summary",
        "",
        "| Rank | Symbol | Score | Label | Actionability | Today Focus Score | Bias | Setup Type | Quality |",
        "|---|---|---:|---|---|---:|---|---|---|",
    ]

    for rank, analysis in enumerate(sorted_analyses, start=1):
        analysis = analysis if isinstance(analysis, dict) else {}
        today_focus = (
            analysis.get("today_focus")
            if isinstance(analysis.get("today_focus"), dict)
            else {}
        )
        actionability = _markdown_cell(today_focus.get("actionability"), default="N/A")
        today_score = _format_score(today_focus.get("today_focus_score"))

        if analysis.get("analysis_failed") is True:
            lines.append(
                "| "
                f"{rank} | "
                f"{_markdown_cell(analysis.get('symbol'))} | "
                "Failed | "
                "Failed | "
                f"{actionability} | "
                f"{today_score} | "
                "N/A | "
                "AI error | "
                "N/A |"
            )
            continue

        score = analysis.get("overall_score")
        lines.append(
            "| "
            f"{rank} | "
            f"{_markdown_cell(analysis.get('symbol'))} | "
            f"{_format_score(score)} | "
            f"{grade_label(score)} | "
            f"{actionability} | "
            f"{today_score} | "
            f"{_markdown_cell(analysis.get('bias'))} | "
            f"{_markdown_cell(analysis.get('setup_type'))} | "
            f"{_markdown_cell(analysis.get('setup_quality'))} |"
        )

    detailed_sections = [format_single_analysis(analysis) for analysis in sorted_analyses]
    lines.extend(["", "---", "", "## Detailed Analysis", ""])

    if detailed_sections:
        lines.append("\n\n".join(detailed_sections))
    else:
        lines.append("No analyses provided.")

    return "\n".join(lines).rstrip() + "\n"


def save_report(
    markdown_text: str, output_dir: str = "reports", filename: Optional[str] = None
) -> str:
    """
    Save Markdown report text and return the saved file path.
    """
    reports_dir = Path(output_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"premarket_report_{timestamp}.md"

    filepath = reports_dir / filename
    filepath.write_text(markdown_text, encoding="utf-8")
    return str(filepath)


def generate_and_save_report(analyses: Iterable[Analysis], output_dir: str = "reports") -> str:
    """
    Generate a Markdown report, save it, and return the saved file path.
    """
    markdown_report = generate_markdown_report(analyses)
    return save_report(markdown_report, output_dir=output_dir)


if __name__ == "__main__":
    sample_analyses = [
        {
            "symbol": "NVDA",
            "overall_score": 72,
            "bias": "bullish",
            "setup_type": "high_base_breakout",
            "setup_quality": "C",
            "ema_assessment": "Price is holding above the 8/21/50 EMA stack.",
            "volume_assessment": "Recent volume supports watchlist interest but is not yet decisive.",
            "ignition_assessment": "A prior bullish ignition candle is present, though follow-through still matters.",
            "accumulation_distribution_assessment": "Volume behavior leans toward accumulation.",
            "key_levels": {
                "support": [915.25, 887.4],
                "resistance": [974.0, 1010.5],
                "demand_zones": [{"date": "2026-05-28", "low": 895.0, "high": 910.0}],
                "supply_zones": [],
            },
            "entry_idea": "Watch for confirmation above nearby resistance or a controlled retest into support.",
            "stop_idea": "Use the nearest failed support area as a decision-support reference.",
            "target_idea": "Nearest active resistance above price can frame upside expectations.",
            "warnings": ["Setup quality is watchlist-only until volume confirms."],
            "disqualifiers": [],
            "final_verdict": "Bullish context, but this remains a watchlist setup until confirmation improves.",
            "today_focus": {
                "symbol": "NVDA",
                "today_focus_score": 76,
                "actionability": "breakout_only",
                "trigger_level": 974.0,
                "invalidation_level": 887.4,
                "do_not_chase_above": None,
                "preferred_entry_style": "breakout",
                "same_day_thesis": "Watch today only if price confirms through resistance with volume.",
                "why_today": ["Bullish context.", "Clear trigger level exists."],
                "warnings": ["Do not anticipate the breakout."],
                "disqualifiers": [],
            },
            "vision_review": {
                "symbol": "NVDA",
                "visual_score": 78,
                "focus_list_candidate": True,
                "visual_setup_type": "high_base",
                "visual_quality": "B",
                "impulse_present": True,
                "consolidation_quality": "tight",
                "ema_structure": "strong",
                "volume_read": "supports_setup",
                "extension_risk": "medium",
                "trigger_level": "974.00",
                "invalidation_level": "887.40",
                "reasons": ["Clean EMA structure.", "Constructive consolidation."],
                "warnings": ["Needs confirmation through resistance."],
                "final_visual_verdict": "Visual setup is constructive but not automatic.",
            },
        },
        {
            "symbol": "XYZ",
            "overall_score": 48,
            "bias": "neutral",
            "setup_type": "no_clear_setup",
            "setup_quality": "D",
            "volume_assessment": "Volume is not confirming a clean directional move.",
            "entry_idea": "No clear idea from the provided data.",
            "final_verdict": "The evidence is mixed, so this is weak decision-support context.",
        },
        {
            "symbol": "MOCK_FAIL",
            "analysis_failed": True,
            "error": "missing required field: overall_score",
            "raw_response": "{bad json...",
            "overall_score": None,
            "bias": None,
            "setup_type": None,
            "setup_quality": None,
            "final_verdict": "AI analysis failed: missing required field: overall_score",
            "today_focus": {
                "symbol": "MOCK_FAIL",
                "today_focus_score": 40,
                "actionability": "avoid",
                "trigger_level": None,
                "invalidation_level": None,
                "do_not_chase_above": None,
                "preferred_entry_style": "no_trade",
                "same_day_thesis": "No valid same-day setup path.",
                "why_today": [],
                "warnings": ["Mock failure sample."],
                "disqualifiers": ["AI analysis failed."],
            },
            "warnings": [],
            "disqualifiers": [],
        },
    ]

    report = generate_markdown_report(sample_analyses)
    print(report)

    saved_path = save_report(report)
    print(f"Saved report: {saved_path}")

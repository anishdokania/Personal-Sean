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
DEFAULT_REPORT_DETAIL = "compact"
REPORT_DETAIL_LEVELS = {"compact", "full"}


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

    final_pre_ai_score = _as_number(analysis.get("final_pre_ai_score"))
    if final_pre_ai_score is not None:
        return final_pre_ai_score

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


def _format_pct(value: Any) -> str:
    """Format percentage values for sector performance display."""
    numeric_value = _as_number(value)
    if numeric_value is None:
        return "N/A"

    return f"{numeric_value:.1f}%"


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


def _format_bullets(items: Any, max_items: Optional[int] = None) -> str:
    """Format warnings/disqualifiers as Markdown bullets."""
    bullet_items = [
        _clean_text(item, default="")
        for item in _as_list(items)
        if _clean_text(item, default="")
    ]
    if max_items is not None:
        bullet_items = bullet_items[:max_items]

    if not bullet_items:
        return "- None"

    return "\n".join(f"- {item}" for item in bullet_items)


def _compact_text(value: Any, max_chars: int = 160, default: str = "Not provided.") -> str:
    """Return one compact report line."""
    text = _clean_text(value, default=default).replace("\n", " ")
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text

    return text[: max_chars - 3].rstrip() + "..."


def _short_items(items: Any, max_items: int = 3, max_chars: int = 90) -> List[str]:
    """Return short display strings for compact sections."""
    short_items = []
    for item in _as_list(items):
        text = _compact_text(item, max_chars=max_chars, default="")
        if text:
            short_items.append(text)
        if len(short_items) >= max_items:
            break

    return short_items


def _format_inline_items(items: Any, max_items: int = 3, default: str = "None") -> str:
    """Format a compact inline list."""
    short_items = _short_items(items, max_items=max_items)
    return "; ".join(short_items) if short_items else default


def _format_short_bullets(items: Any, max_items: int = 3) -> str:
    """Format compact bullets with a maximum item count."""
    return _format_bullets(_short_items(items, max_items=max_items), max_items=max_items)


def _display_actionability(analysis: Analysis) -> str:
    """Return the best available actionability label for a compact header."""
    if not isinstance(analysis, dict):
        return "Unknown"

    actionability = analysis.get("actionability")
    if actionability:
        return _clean_text(actionability, default="Unknown")

    today_focus = analysis.get("today_focus")
    if isinstance(today_focus, dict):
        return _clean_text(today_focus.get("actionability"), default="Unknown")

    return "Unknown"


def _analysis_level(analysis: Analysis, field: str) -> str:
    """Use Claude's compact level first, then deterministic Today Focus."""
    if not isinstance(analysis, dict):
        return "None"

    setup_judge = analysis.get("setup_judge")
    if isinstance(setup_judge, dict) and field in setup_judge:
        formatted_level = _format_price(setup_judge.get(field))
        return formatted_level if formatted_level else "None"

    formatted_level = _format_price(analysis.get(field))
    if formatted_level:
        return formatted_level

    today_focus = analysis.get("today_focus")
    if isinstance(today_focus, dict):
        formatted_level = _format_price(today_focus.get(field))
        if formatted_level:
            return formatted_level

    return "None"


def _quick_plan_items(analysis: Analysis) -> List[str]:
    """Return up to three concise plan bullets for quick-read sections."""
    if not isinstance(analysis, dict):
        return []

    plan_items = [
        _clean_text(item, default="")
        for item in _as_list(analysis.get("same_day_plan"))
        if _clean_text(item, default="")
    ]
    if plan_items:
        return plan_items[:3]

    fallback_items = [
        analysis.get("entry_idea"),
        analysis.get("stop_idea"),
        analysis.get("target_idea"),
    ]
    return [
        _clean_text(item, default="")
        for item in fallback_items
        if _clean_text(item, default="")
    ][:3]


def _analysis_verdict(analysis: Analysis, max_chars: int = 90) -> str:
    """Return a compact verdict for the ticker header."""
    if analysis.get("analysis_failed") is True:
        return "AI analysis failed"

    setup_judge = analysis.get("setup_judge")
    if analysis.get("setup_judge_veto") is True and isinstance(setup_judge, dict):
        return "JUDGE VETO: " + _compact_text(
            setup_judge.get("one_line_thesis"),
            max_chars=max_chars,
            default="Setup Judge vetoed this candidate.",
        )

    final_verdict = analysis.get("final_verdict")
    if final_verdict:
        return _compact_text(final_verdict, max_chars=max_chars, default="No verdict")

    setup_type = _clean_text(analysis.get("setup_type"), default="setup unknown")
    return _compact_text(setup_type, max_chars=max_chars, default="No verdict")


def _focus_gate_pass_reason(analysis: Analysis) -> str:
    """Summarize why the candidate survived deterministic focus gates."""
    today_focus = analysis.get("today_focus")
    focus_structure = analysis.get("focus_structure")

    actionability = (
        _clean_text(today_focus.get("actionability"), default="")
        if isinstance(today_focus, dict)
        else ""
    )
    today_score = (
        _format_score(today_focus.get("today_focus_score"))
        if isinstance(today_focus, dict)
        else "N/A"
    )
    structure_type = (
        _clean_text(focus_structure.get("structure_type"), default="")
        if isinstance(focus_structure, dict)
        else ""
    )
    structure_score = (
        _format_score(focus_structure.get("focus_structure_score"))
        if isinstance(focus_structure, dict)
        else "N/A"
    )
    final_pre_ai_score = _format_score(analysis.get("final_pre_ai_score"))

    reason_parts = []
    has_final_score = final_pre_ai_score != "N/A"
    if actionability and structure_type:
        reason_parts.append(f"{actionability} with {structure_type}")
    elif has_final_score and actionability:
        reason_parts.append(actionability)
    elif has_final_score and structure_type:
        reason_parts.append(structure_type)
    if today_score != "N/A" or structure_score != "N/A":
        reason_parts.append(f"Today {today_score}, Structure {structure_score}")
    if has_final_score:
        reason_parts.append(f"Final pre-AI {final_pre_ai_score}")

    if reason_parts:
        return "; ".join(reason_parts)

    return "Selected by deterministic focus gate audit."


def _format_key_levels_compact(key_levels: Any) -> str:
    """Format key levels as one compact line."""
    key_levels = key_levels if isinstance(key_levels, dict) else {}
    support = _format_level_list(key_levels.get("support"))
    resistance = _format_level_list(key_levels.get("resistance"))
    demand_zones = _format_zone_list(key_levels.get("demand_zones"))
    supply_zones = _format_zone_list(key_levels.get("supply_zones"))

    return (
        f"Support {support}; Resistance {resistance}; "
        f"Demand {demand_zones}; Supply {supply_zones}"
    )


def _format_sector_context(analysis: Analysis) -> str:
    """Format candidate sector leadership context."""
    sector = (
        analysis.get("sector_name")
        or analysis.get("Sector")
        or analysis.get("sector")
        or "Unknown"
    )
    etf = analysis.get("sector_etf") or "N/A"
    rank = _format_score(analysis.get("sector_rank"))
    score = _format_score(analysis.get("sector_score"))
    alignment_value = analysis.get("sector_alignment_score")
    if alignment_value is None:
        alignment_value = analysis.get("SectorAlignmentScore")
    relative_1m_value = analysis.get("relative_strength_1m")
    if relative_1m_value is None:
        relative_1m_value = analysis.get("RelativeStrength1M")
    relative_3m_value = analysis.get("relative_strength_3m")
    if relative_3m_value is None:
        relative_3m_value = analysis.get("RelativeStrength3M")
    alignment = _format_score(alignment_value)
    relative_1m = _format_score(relative_1m_value)
    relative_3m = _format_score(relative_3m_value)
    return (
        f"Sector: {_clean_text(sector, default='Unknown')} / {etf} | "
        f"Rank: {rank} | Score: {score} | Alignment: {alignment} | "
        f"RelStr 1M: {relative_1m} | 3M: {relative_3m}"
    )


def _format_blueprint_setup_context(analysis: Analysis) -> str:
    """Format the named Blueprint setup classification."""
    focus_structure = analysis.get("focus_structure")
    if not isinstance(focus_structure, dict):
        focus_structure = {}

    setup_type = (
        analysis.get("blueprint_setup_type")
        or analysis.get("BlueprintSetupType")
        or focus_structure.get("blueprint_setup_type")
        or "no_blueprint_setup"
    )
    setup_score = analysis.get("blueprint_setup_score")
    if setup_score is None:
        setup_score = analysis.get("BlueprintSetupScore")
    if setup_score is None:
        setup_score = focus_structure.get("blueprint_setup_score")
    fit_score = analysis.get("blueprint_fit_score")
    if fit_score is None:
        fit_score = analysis.get("BlueprintFitScore")
    if fit_score is None:
        fit_score = focus_structure.get("blueprint_fit_score")
    setup_match = focus_structure.get("blueprint_setup_match")
    fit_pass = focus_structure.get("blueprint_fit_pass")
    match_text = "match" if setup_match is True else "watch" if setup_match is False else "unknown"
    fit_text = "fit" if fit_pass is True else "fail" if fit_pass is False else "unknown"
    evidence = _format_inline_items(
        focus_structure.get("blueprint_setup_evidence"), max_items=2
    )

    return (
        f"{_clean_text(setup_type, default='no_blueprint_setup')} "
        f"({_format_score(setup_score)}, {match_text}; "
        f"fit {_format_score(fit_score)}, {fit_text})"
        + (f" | {evidence}" if evidence != "None" else "")
    )


def _format_setup_context_compact(analysis: Analysis) -> str:
    """Format compact deterministic setup context."""
    today_focus = analysis.get("today_focus")
    focus_structure = analysis.get("focus_structure")
    key_levels = analysis.get("key_levels")

    today_label = "Unknown"
    if isinstance(today_focus, dict):
        today_label = (
            f"{_clean_text(today_focus.get('actionability'), default='Unknown')} "
            f"({_format_score(today_focus.get('today_focus_score'))})"
        )

    structure_label = "Unknown"
    if isinstance(focus_structure, dict):
        structure_label = (
            f"{_clean_text(focus_structure.get('structure_type'), default='Unknown')} "
            f"({_format_score(focus_structure.get('focus_structure_score'))})"
        )

    return f"""#### Setup Context
- Today Focus: {today_label}
- Focus Structure: {structure_label}
- Blueprint Setup: {_format_blueprint_setup_context(analysis)}
- {_format_sector_context(analysis)}
- Key levels: {_format_key_levels_compact(key_levels)}
- Passed because: {_focus_gate_pass_reason(analysis)}"""


def _format_ai_compact(analysis: Analysis) -> str:
    """Format compact Claude output."""
    if isinstance(analysis, dict) and analysis.get("setup_judge_veto") is True:
        return """#### AI
- Full Claude: skipped by Setup Judge veto"""

    return f"""#### AI
- Claude score: {_format_score(analysis.get("overall_score"))}
- Claude bias: {_clean_text(analysis.get("bias"), default="Unknown")}
- Assessment: {_compact_text(analysis.get("final_verdict"), max_chars=180)}"""


def _format_setup_judge_compact(analysis: Analysis) -> str:
    """Format optional Setup Judge result."""
    setup_judge = analysis.get("setup_judge") if isinstance(analysis, dict) else None
    if not isinstance(setup_judge, dict):
        return ""

    reasons = _format_inline_items(setup_judge.get("top_reasons"), max_items=3)
    veto_reasons = _format_inline_items(setup_judge.get("veto_reasons"), max_items=3)
    if veto_reasons == "None":
        veto_reasons = "None"

    return f"""
#### Setup Judge
- Decision: {_clean_text(setup_judge.get("judge_action"), default="Unknown")}
- Rank score: {_format_score(setup_judge.get("judge_rank_score"))}
- Grade: {_clean_text(setup_judge.get("setup_grade"), default="Unknown")}
- Pattern: {_clean_text(setup_judge.get("blueprint_pattern"), default="Unknown")}
- Level quality: {_clean_text(setup_judge.get("level_quality"), default="Unknown")}
- Chase risk: {_clean_text(setup_judge.get("chase_risk"), default="Unknown")}
- Volume quality: {_clean_text(setup_judge.get("volume_quality"), default="Unknown")}
- Thesis: {_compact_text(setup_judge.get("one_line_thesis"), max_chars=180)}
- Reasons: {reasons}
- Veto reasons: {veto_reasons}"""


def _format_details_compact(analysis: Analysis) -> str:
    """Format compact deterministic and Claude details."""
    today_focus = analysis.get("today_focus")
    focus_structure = analysis.get("focus_structure")

    today_thesis = "None"
    today_reasons = "None"
    today_warnings = "None"
    today_disqualifiers = "None"
    if isinstance(today_focus, dict):
        today_thesis = _compact_text(today_focus.get("same_day_thesis"), max_chars=130)
        today_reasons = _format_inline_items(today_focus.get("why_today"), max_items=3)
        today_warnings = _format_inline_items(today_focus.get("warnings"), max_items=3)
        today_disqualifiers = _format_inline_items(
            today_focus.get("disqualifiers"), max_items=3
        )

    structure_flags = "None"
    structure_reasons = "None"
    structure_warnings = "None"
    structure_disqualifiers = "None"
    if isinstance(focus_structure, dict):
        structure_flags = (
            f"impulse={_format_bool(focus_structure.get('impulse_present'))}, "
            f"digestion={_format_bool(focus_structure.get('controlled_digestion'))}, "
            f"compression={_format_bool(focus_structure.get('compression_present'))}, "
            f"dryup={_format_bool(focus_structure.get('volume_dryup'))}, "
            f"ema={_format_bool(focus_structure.get('holding_ema_structure'))}, "
            f"trigger={_format_bool(focus_structure.get('trigger_nearby'))}, "
            f"invalidation={_format_bool(focus_structure.get('invalidation_nearby'))}, "
            f"extension={_clean_text(focus_structure.get('extension_risk'), default='Unknown')}"
        )
        structure_reasons = _format_inline_items(focus_structure.get("reasons"), max_items=3)
        structure_warnings = _format_inline_items(
            focus_structure.get("warnings"), max_items=3
        )
        structure_disqualifiers = _format_inline_items(
            focus_structure.get("disqualifiers"), max_items=3
        )

    disqualifiers = _format_inline_items(analysis.get("disqualifiers"), max_items=3)

    return f"""#### Details
- Today thesis: {today_thesis}
- Today reasons: {today_reasons}
- Today warnings: {today_warnings}
- Today disqualifiers: {today_disqualifiers}
- Structure flags: {structure_flags}
- Structure reasons: {structure_reasons}
- Structure warnings: {structure_warnings}
- Structure disqualifiers: {structure_disqualifiers}
- Claude EMA: {_compact_text(analysis.get("ema_assessment"), max_chars=130)}
- Claude Volume: {_compact_text(analysis.get("volume_assessment"), max_chars=130)}
- Claude Ignition: {_compact_text(analysis.get("ignition_assessment"), max_chars=130)}
- Claude A/D: {_compact_text(analysis.get("accumulation_distribution_assessment"), max_chars=130)}
- Disqualifiers: {disqualifiers}{_format_visual_review(analysis.get("vision_review"))}"""


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

#### Raw Response
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

#### Today Focus
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


def _format_focus_structure(focus_structure: Any) -> str:
    """Format the deterministic focus-structure section when present."""
    if not isinstance(focus_structure, dict):
        return ""

    reasons = _format_bullets(focus_structure.get("reasons"))
    warnings = _format_bullets(focus_structure.get("warnings"))
    disqualifiers = _format_bullets(focus_structure.get("disqualifiers"))

    return f"""

#### Focus Structure
**Structure Score:** {_format_score(focus_structure.get("focus_structure_score"))}
**Structure Type:** {_clean_text(focus_structure.get("structure_type"), default="Unknown")}
**Impulse Present:** {_format_bool(focus_structure.get("impulse_present"))}
**Controlled Digestion:** {_format_bool(focus_structure.get("controlled_digestion"))}
**Compression Present:** {_format_bool(focus_structure.get("compression_present"))}
**Volume Dry-Up:** {_format_bool(focus_structure.get("volume_dryup"))}
**Holding EMA Structure:** {_format_bool(focus_structure.get("holding_ema_structure"))}
**Trigger Nearby:** {_format_bool(focus_structure.get("trigger_nearby"))}
**Invalidation Nearby:** {_format_bool(focus_structure.get("invalidation_nearby"))}
**Extension Risk:** {_clean_text(focus_structure.get("extension_risk"), default="Unknown")}

**Structure Verdict:** {_clean_text(focus_structure.get("structure_verdict"))}

Reasons:
{reasons}

Warnings:
{warnings}

Disqualifiers:
{disqualifiers}"""


def _format_failed_analysis(
    analysis: Analysis, report_detail: str = DEFAULT_REPORT_DETAIL
) -> str:
    """Format a failed AI analysis as an explicit report section."""
    symbol = _clean_text(analysis.get("symbol"), default="UNKNOWN")
    reason = _clean_text(analysis.get("error"), default="unknown AI analysis failure")
    verdict = _clean_text(
        analysis.get("final_verdict"),
        default=f"AI analysis failed: {reason}",
    )
    raw_response_section = _format_raw_response(analysis.get("raw_response"))

    detail = str(report_detail or DEFAULT_REPORT_DETAIL).strip().lower()
    if detail == "compact":
        return f"""### {symbol} — AI analysis failed | Score N/A | {_display_actionability(analysis)}

#### Quick Read
- Trigger: {_analysis_level(analysis, "trigger_level")}
- Invalidation: {_analysis_level(analysis, "invalidation_level")}
- Do-not-chase: {_analysis_level(analysis, "do_not_chase_above")}

Plan:
- Do not use this AI result.

Warnings:
- {reason}

{_format_setup_context_compact(analysis)}

#### AI
- Claude score: N/A
- Claude bias: N/A
- Assessment: {verdict}

#### Details
- Failure: {reason}{raw_response_section}"""

    today_focus_section = _format_today_focus(analysis.get("today_focus"))
    focus_structure_section = _format_focus_structure(analysis.get("focus_structure"))

    return f"""### {symbol} - AI Analysis Failed

#### Reason
{verdict}

#### Status
This ticker was selected for analysis, but the AI result was incomplete or invalid. It was not scored as a valid setup.{today_focus_section}{focus_structure_section}{raw_response_section}"""


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

#### Visual Chart Review
Vision review failed: {reason}"""

    reasons = _format_bullets(vision_review.get("reasons"))
    warnings = _format_bullets(vision_review.get("warnings"))

    return f"""

#### Visual Chart Review
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


def _sector_records(sector_leadership: Any) -> List[Dict[str, Any]]:
    """Normalize optional sector leadership metadata for report display."""
    if sector_leadership is None:
        return []
    if isinstance(sector_leadership, dict):
        sector_leadership = [sector_leadership]

    records = []
    for item in _as_list(sector_leadership):
        if not isinstance(item, dict):
            continue
        def first_value(*keys: str) -> Any:
            for key in keys:
                value = item.get(key)
                if value is not None:
                    return value
            return None

        sector = item.get("sector") or item.get("Sector")
        etf = item.get("etf") or item.get("ETF")
        if not sector and not etf:
            continue
        records.append(
            {
                "sector": sector,
                "etf": etf,
                "rank": _as_number(first_value("rank", "SectorRank")),
                "score": _as_number(first_value("score", "Score")),
                "perf_1w": _as_number(first_value("perf_1w", "1W_Return")),
                "perf_1m": _as_number(first_value("perf_1m", "1M_Return")),
                "perf_3m": _as_number(first_value("perf_3m", "3M_Return")),
                "perf_6m": _as_number(first_value("perf_6m", "6M_Return")),
                "perf_1y": _as_number(first_value("perf_1y", "1Y_Return")),
            }
        )

    return sorted(
        records,
        key=lambda item: (
            item["rank"] if item.get("rank") is not None else float("inf"),
            -item["score"] if item.get("score") is not None else 0,
        ),
    )


def format_sector_leadership_section(sector_leadership: Any) -> str:
    """Format the ETF proxy sector leadership summary."""
    records = _sector_records(sector_leadership)
    if not records:
        return ""

    top_records = records[:3]
    bottom_records = records[-3:]

    def numbered(items: List[Dict[str, Any]]) -> str:
        lines = []
        for idx, item in enumerate(items, start=1):
            lines.append(
                f"{idx}. {_clean_text(item.get('sector'), default='Unknown')} / "
                f"{_clean_text(item.get('etf'), default='N/A')} - "
                f"score {_format_score(item.get('score'))}"
            )
        return "\n".join(lines)

    table_lines = [
        "| Rank | Sector | ETF | Score | 1W | 1M | 3M | 6M | 1Y |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in records:
        table_lines.append(
            "| "
            f"{_format_score(item.get('rank'))} | "
            f"{_markdown_cell(item.get('sector'))} | "
            f"{_markdown_cell(item.get('etf'), default='N/A')} | "
            f"{_format_score(item.get('score'))} | "
            f"{_format_pct(item.get('perf_1w'))} | "
            f"{_format_pct(item.get('perf_1m'))} | "
            f"{_format_pct(item.get('perf_3m'))} | "
            f"{_format_pct(item.get('perf_6m'))} | "
            f"{_format_pct(item.get('perf_1y'))} |"
        )

    return f"""## Sector Leadership

Top sectors:
{numbered(top_records)}

Weak sectors:
{numbered(bottom_records)}

{chr(10).join(table_lines)}"""


def format_market_context_section(market_context: Any) -> str:
    """Format broad-market EMA context for the daily focus list."""
    if not isinstance(market_context, dict) or not market_context:
        return ""

    indexes = market_context.get("indexes")
    if not isinstance(indexes, list) or not indexes:
        return ""

    market_score = _format_score(market_context.get("market_score"))
    market_bias = _clean_text(market_context.get("market_bias"), default="Unknown")

    table_lines = [
        "| Symbol | Name | Regime | Close | EMA8 | EMA21 | EMA50 | Score |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in indexes:
        if not isinstance(item, dict):
            continue
        table_lines.append(
            "| "
            f"{_markdown_cell(item.get('symbol'))} | "
            f"{_markdown_cell(item.get('name'))} | "
            f"{_markdown_cell(item.get('regime'))} | "
            f"{_format_price(item.get('close')) or 'N/A'} | "
            f"{_format_price(item.get('ema8')) or 'N/A'} | "
            f"{_format_price(item.get('ema21')) or 'N/A'} | "
            f"{_format_price(item.get('ema50')) or 'N/A'} | "
            f"{_format_score(item.get('score'))} |"
        )

    warnings = _format_bullets(market_context.get("warnings"), max_items=3)
    return f"""## Market Context

Bias: {market_bias} | Score: {market_score}

{chr(10).join(table_lines)}

Warnings:
{warnings}"""


def _format_single_analysis_compact(analysis: Analysis) -> str:
    """
    Format one analysis as a fast trader quick-read.
    """
    analysis = analysis if isinstance(analysis, dict) else {}
    if analysis.get("analysis_failed") is True:
        return _format_failed_analysis(analysis, report_detail="compact")

    symbol = _clean_text(analysis.get("symbol"), default="UNKNOWN")
    score = analysis.get("overall_score")
    score_text = _format_score(score)
    label = grade_label(score)
    actionability = _display_actionability(analysis)
    verdict = _analysis_verdict(analysis)
    plan = _format_short_bullets(_quick_plan_items(analysis), max_items=3)
    warnings = _format_short_bullets(analysis.get("warnings"), max_items=3)

    return f"""### {symbol} — {verdict} | Score {score_text}/100 | {actionability}

#### Quick Read
- Trigger: {_analysis_level(analysis, "trigger_level")}
- Invalidation: {_analysis_level(analysis, "invalidation_level")}
- Do-not-chase: {_analysis_level(analysis, "do_not_chase_above")}

Plan:
{plan}

Warnings:
{warnings}

{_format_setup_context_compact(analysis)}

{_format_setup_judge_compact(analysis)}

{_format_ai_compact(analysis)}

{_format_details_compact(analysis)}"""


def _format_single_analysis_full(analysis: Analysis) -> str:
    """
    Format one AI analysis dictionary with full deterministic detail.
    """
    analysis = analysis if isinstance(analysis, dict) else {}
    if analysis.get("analysis_failed") is True:
        return _format_failed_analysis(analysis, report_detail="full")

    symbol = _clean_text(analysis.get("symbol"), default="UNKNOWN")
    score = analysis.get("overall_score")
    score_text = _format_score(score)
    label = grade_label(score)
    actionability = _display_actionability(analysis)
    key_levels = analysis.get("key_levels") if isinstance(analysis.get("key_levels"), dict) else {}

    support = _format_level_list(key_levels.get("support"))
    resistance = _format_level_list(key_levels.get("resistance"))
    demand_zones = _format_zone_list(key_levels.get("demand_zones"))
    supply_zones = _format_zone_list(key_levels.get("supply_zones"))
    plan = _format_bullets(_quick_plan_items(analysis))
    warnings = _format_bullets(analysis.get("warnings"))
    disqualifiers = _format_bullets(analysis.get("disqualifiers"))

    return f"""### {symbol} - Score {score_text}/100 - {actionability}

{_clean_text(analysis.get("final_verdict"))}

- Trigger: {_analysis_level(analysis, "trigger_level")}
- Invalidation: {_analysis_level(analysis, "invalidation_level")}
- Do-not-chase: {_analysis_level(analysis, "do_not_chase_above")}
- Bias: {_clean_text(analysis.get("bias"), default="Unknown")}
- Setup: {_clean_text(analysis.get("setup_type"), default="Unknown")}
- Quality: {_clean_text(analysis.get("setup_quality"), default="Unknown")} ({label})
- Why today: {_clean_text(analysis.get("why_today"), default="Not provided.")}

Plan:
{plan}

Warnings:
{warnings}
{_format_setup_judge_compact(analysis)}
{_format_today_focus(analysis.get("today_focus"))}
{_format_focus_structure(analysis.get("focus_structure"))}

#### Key Levels
- Support: {support}
- Resistance: {resistance}
- Demand Zones: {demand_zones}
- Supply Zones: {supply_zones}

#### Trade Plan Ideas
- Entry: {_clean_text(analysis.get("entry_idea"))}
- Stop: {_clean_text(analysis.get("stop_idea"))}
- Target: {_clean_text(analysis.get("target_idea"))}

#### Claude Assessment
**EMA:** {_clean_text(analysis.get("ema_assessment"))}

**Volume:** {_clean_text(analysis.get("volume_assessment"))}

**Ignition:** {_clean_text(analysis.get("ignition_assessment"))}

**Accumulation/Distribution:** {_clean_text(analysis.get("accumulation_distribution_assessment"))}{_format_visual_review(analysis.get("vision_review"))}

#### Disqualifiers
{disqualifiers}"""


def format_single_analysis(
    analysis: Analysis, report_detail: str = DEFAULT_REPORT_DETAIL
) -> str:
    """
    Format one AI analysis dictionary as Markdown.
    """
    detail = str(report_detail or DEFAULT_REPORT_DETAIL).strip().lower()
    if detail not in REPORT_DETAIL_LEVELS:
        raise ValueError("report_detail must be 'compact' or 'full'.")

    if detail == "full":
        return _format_single_analysis_full(analysis)

    return _format_single_analysis_compact(analysis)


def generate_markdown_report(
    analyses: Iterable[Analysis],
    title: str = "Trading Blueprint Daily Focus List",
    report_detail: str = DEFAULT_REPORT_DETAIL,
    sector_leadership: Any = None,
    market_context: Any = None,
) -> str:
    """
    Generate a full Markdown report with a summary table and per-stock details.
    """
    detail = str(report_detail or DEFAULT_REPORT_DETAIL).strip().lower()
    if detail not in REPORT_DETAIL_LEVELS:
        raise ValueError("report_detail must be 'compact' or 'full'.")

    sorted_analyses = sort_analyses(analyses)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {title}",
        "",
        f"Generated: {generated_at}",
        "",
        "## Summary",
        "",
        "| Rank | Symbol | Score | Label | Actionability | Today | Structure | Blueprint Setup | Blueprint | Fit | Sector Align | Final Pre-AI | Bias | Setup Type | Quality |",
        "|---|---|---:|---|---|---:|---:|---|---:|---:|---:|---:|---|---|---|",
    ]

    for rank, analysis in enumerate(sorted_analyses, start=1):
        analysis = analysis if isinstance(analysis, dict) else {}
        today_focus = (
            analysis.get("today_focus")
            if isinstance(analysis.get("today_focus"), dict)
            else {}
        )
        actionability = _markdown_cell(_display_actionability(analysis), default="N/A")
        today_score = _format_score(today_focus.get("today_focus_score"))
        focus_structure = (
            analysis.get("focus_structure")
            if isinstance(analysis.get("focus_structure"), dict)
            else {}
        )
        focus_score = _format_score(focus_structure.get("focus_structure_score"))
        structure_type = _markdown_cell(focus_structure.get("structure_type"), default="N/A")
        blueprint_setup_type = _markdown_cell(
            analysis.get("blueprint_setup_type")
            or analysis.get("BlueprintSetupType")
            or focus_structure.get("blueprint_setup_type"),
            default="N/A",
        )
        blueprint_setup_score_value = analysis.get("blueprint_setup_score")
        if blueprint_setup_score_value is None:
            blueprint_setup_score_value = analysis.get("BlueprintSetupScore")
        if blueprint_setup_score_value is None:
            blueprint_setup_score_value = focus_structure.get("blueprint_setup_score")
        blueprint_setup_score = _format_score(blueprint_setup_score_value)

        blueprint_fit_score_value = analysis.get("blueprint_fit_score")
        if blueprint_fit_score_value is None:
            blueprint_fit_score_value = analysis.get("BlueprintFitScore")
        if blueprint_fit_score_value is None:
            blueprint_fit_score_value = focus_structure.get("blueprint_fit_score")
        blueprint_fit_score = _format_score(blueprint_fit_score_value)

        sector_alignment_score_value = analysis.get("sector_alignment_score")
        if sector_alignment_score_value is None:
            sector_alignment_score_value = analysis.get("SectorAlignmentScore")
        sector_alignment_score = _format_score(sector_alignment_score_value)
        final_pre_ai_score = _format_score(analysis.get("final_pre_ai_score"))

        if analysis.get("analysis_failed") is True:
            lines.append(
                "| "
                f"{rank} | "
                f"{_markdown_cell(analysis.get('symbol'))} | "
                "Failed | "
                "Failed | "
                f"{actionability} | "
                f"{today_score} | "
                f"{focus_score} | "
                f"{blueprint_setup_type} | "
                f"{blueprint_setup_score} | "
                f"{blueprint_fit_score} | "
                f"{sector_alignment_score} | "
                f"{final_pre_ai_score} | "
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
            f"{focus_score} | "
            f"{blueprint_setup_type} | "
            f"{blueprint_setup_score} | "
            f"{blueprint_fit_score} | "
            f"{sector_alignment_score} | "
            f"{final_pre_ai_score} | "
            f"{_markdown_cell(analysis.get('bias'))} | "
            f"{_markdown_cell(analysis.get('setup_type'))} | "
            f"{_markdown_cell(analysis.get('setup_quality'))} |"
        )

    market_section = format_market_context_section(market_context)
    if market_section:
        lines.extend(["", market_section])

    sector_section = format_sector_leadership_section(sector_leadership)
    if sector_section:
        lines.extend(["", sector_section])

    detailed_sections = [
        format_single_analysis(analysis, report_detail=detail)
        for analysis in sorted_analyses
    ]
    lines.extend(["", "---", "", "## Ticker Reports", ""])

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


def generate_and_save_report(
    analyses: Iterable[Analysis],
    output_dir: str = "reports",
    report_detail: str = DEFAULT_REPORT_DETAIL,
    sector_leadership: Any = None,
    market_context: Any = None,
) -> str:
    """
    Generate a Markdown report, save it, and return the saved file path.
    """
    markdown_report = generate_markdown_report(
        analyses,
        report_detail=report_detail,
        sector_leadership=sector_leadership,
        market_context=market_context,
    )
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

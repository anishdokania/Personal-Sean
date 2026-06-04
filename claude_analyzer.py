"""
Claude analysis layer for the trading_system scanner.

Module 5 evaluates one shortlisted stock at a time using structured technical
features from Module 4. It is decision support only, not an auto-trading module.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, Optional, Tuple

import anthropic
from dotenv import load_dotenv

from technical import analyze_stock_technicals


DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
PRIMARY_MAX_TOKENS = 1000
REPAIR_MAX_TOKENS = 800
SETUP_JUDGE_MAX_TOKENS = 900
SETUP_JUDGE_REPAIR_MAX_TOKENS = 700

ALLOWED_BIASES = {"bullish", "bearish", "neutral"}
ALLOWED_SETUP_QUALITIES = {"A", "B", "C", "D", "F"}
ALLOWED_ACTIONABILITY = {
    "ready_today",
    "breakout_only",
    "pullback_only",
    "needs_more_time",
    "avoid",
}
ALLOWED_SETUP_JUDGE_ACTIONS = {"approve", "downgrade", "veto"}
ALLOWED_SETUP_JUDGE_ACTIONABILITY = {
    "ready_today",
    "breakout_only",
    "pullback_only",
    "watch_only",
    "reject",
}
ALLOWED_BLUEPRINT_PATTERNS = {
    "bullish_power_gap_base",
    "accumulation_base_lows",
    "big_base_highs_breakout",
    "compression_breakout_retest",
    "trendline_compression",
    "high_tight_flag",
    "breakout_retest",
    "ema_reclaim_base",
    "other",
}
ALLOWED_LEVEL_QUALITY = {"clean", "wide", "unclear"}
ALLOWED_CHASE_RISK = {"low", "medium", "high"}
ALLOWED_STRUCTURE_QUALITY = {"clean", "acceptable", "messy"}
ALLOWED_VOLUME_QUALITY = {"confirming", "neutral", "weak"}

COMPACT_AI_SCHEMA = {
    "symbol": "TICKER",
    "overall_score": 0,
    "bias": "bullish|bearish|neutral",
    "setup_type": "string",
    "setup_quality": "A|B|C|D|F",
    "actionability": "ready_today|breakout_only|pullback_only|needs_more_time|avoid",
    "trigger_level": None,
    "invalidation_level": None,
    "do_not_chase_above": None,
    "ema_assessment": "short sentence",
    "volume_assessment": "short sentence",
    "ignition_assessment": "short sentence",
    "accumulation_distribution_assessment": "short sentence",
    "entry_idea": "short sentence",
    "stop_idea": "short sentence",
    "target_idea": "short sentence",
    "warnings": [],
    "disqualifiers": [],
    "same_day_plan": [],
    "why_today": "short sentence",
    "final_verdict": "max two short sentences",
}
REQUIRED_AI_FIELDS = list(COMPACT_AI_SCHEMA.keys())

SETUP_JUDGE_SCHEMA = {
    "symbol": "TICKER",
    "judge_version": "setup_judge_v1",
    "manual_review_pass": True,
    "judge_action": "approve|downgrade|veto",
    "judge_rank_score": 0,
    "setup_grade": "A|B|C|D|F",
    "actionability": "ready_today|breakout_only|pullback_only|watch_only|reject",
    "blueprint_pattern": "bullish_power_gap_base|accumulation_base_lows|big_base_highs_breakout|compression_breakout_retest|trendline_compression|high_tight_flag|breakout_retest|ema_reclaim_base|other",
    "trigger_level": None,
    "invalidation_level": None,
    "do_not_chase_above": None,
    "level_quality": "clean|wide|unclear",
    "chase_risk": "low|medium|high",
    "structure_quality": "clean|acceptable|messy",
    "volume_quality": "confirming|neutral|weak",
    "one_line_thesis": "short sentence",
    "top_reasons": [],
    "veto_reasons": [],
    "watch_plan": [],
}
REQUIRED_SETUP_JUDGE_FIELDS = list(SETUP_JUDGE_SCHEMA.keys())


def _json_default(value: Any) -> Any:
    """Convert uncommon numeric/date objects into JSON-safe values."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def _extract_response_text(message: Any) -> str:
    """Extract text from an Anthropic message response."""
    text_parts = []

    for block in getattr(message, "content", []):
        block_text = getattr(block, "text", None)
        if block_text:
            text_parts.append(block_text)

    return "\n".join(text_parts).strip()


def _strip_json_fences(raw_response: str) -> str:
    """Remove simple Markdown code fences around JSON when present."""
    cleaned = raw_response.strip()
    if not cleaned.startswith("```"):
        return cleaned

    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _schema_text() -> str:
    """Return the compact AI schema as prompt-ready JSON."""
    return json.dumps(COMPACT_AI_SCHEMA, indent=2)


def _setup_judge_schema_text() -> str:
    """Return the setup judge schema as prompt-ready JSON."""
    return json.dumps(SETUP_JUDGE_SCHEMA, indent=2)


def _first_balanced_json_text(text: str) -> Optional[str]:
    """Return the first balanced JSON object substring from text when present."""
    if not isinstance(text, str):
        return None

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(text)):
        char = text[idx]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract a JSON object from raw Claude text.

    Handles raw JSON, fenced JSON, extra wrapper text, and the first balanced
    object. Returns None when no valid object can be parsed.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    candidates = [text.strip()]
    stripped = _strip_json_fences(text)
    if stripped not in candidates:
        candidates.append(stripped)

    balanced = _first_balanced_json_text(stripped)
    if balanced and balanced not in candidates:
        candidates.append(balanced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return parsed

    return None


def _parse_json_response(symbol: str, raw_response: str) -> Dict[str, Any]:
    """
    Parse Claude JSON, with fallbacks for fences and accidental wrapper text.
    """
    parsed = extract_json_object(raw_response)
    if parsed is not None:
        return parsed

    return {
        "symbol": symbol.upper(),
        "error": "Claude response was not valid JSON.",
        "raw_response": raw_response,
    }


def _as_float(value: Any) -> Optional[float]:
    """Return a float when possible, otherwise None."""
    if isinstance(value, bool):
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numeric_value):
        return None

    return numeric_value


def _numeric_from_text(value: Any) -> Optional[float]:
    """Extract the first number from a string-like value when direct parsing fails."""
    if not isinstance(value, str):
        return None

    cleaned = value.strip().replace("$", "").replace(",", "")
    direct_value = _as_float(cleaned)
    if direct_value is not None:
        return direct_value

    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None

    return _as_float(match.group(0))


def _truncate_text(value: Any, max_chars: int = 240, default: str = "") -> str:
    """Return a single-line string capped to a practical report length."""
    if value is None:
        text_value = default
    elif isinstance(value, (list, tuple)):
        text_value = "; ".join(str(item).strip() for item in value if str(item).strip())
    else:
        text_value = str(value).strip()

    text_value = re.sub(r"\s+", " ", text_value).strip()
    if not text_value:
        text_value = default

    if max_chars > 0 and len(text_value) > max_chars:
        return text_value[: max_chars - 3].rstrip() + "..."

    return text_value


def _split_text_items(value: str) -> list[str]:
    """Split a prose field into short list items without over-parsing."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return []

    lines = [
        re.sub(r"^\s*[-*]\s*", "", line).strip()
        for line in cleaned.splitlines()
        if line.strip()
    ]
    if len(lines) > 1:
        return lines

    sentence_items = re.split(r"(?<=[.!?])\s+", cleaned)
    return [item.strip() for item in sentence_items if item.strip()]


def _list_of_strings(value: Any, max_items: int = 3, item_max_chars: int = 120) -> list[str]:
    """Normalize warnings, disqualifiers, and plan fields to short string arrays."""
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        items = _split_text_items(value)
    else:
        items = [value]

    normalized = []
    for item in items:
        text = _truncate_text(item, max_chars=item_max_chars)
        if text:
            normalized.append(text)
        if len(normalized) >= max_items:
            break

    return normalized


def _normalize_score(value: Any) -> int:
    """Coerce overall_score to a 0-100 integer."""
    numeric_value = _as_float(value)
    if numeric_value is None:
        numeric_value = _numeric_from_text(value)
    if numeric_value is None:
        return 0

    return int(round(max(0.0, min(100.0, numeric_value))))


def _normalize_level(value: Any) -> Optional[float]:
    """Return numeric price levels only; prose becomes null."""
    numeric_value = _as_float(value)
    if numeric_value is None:
        numeric_value = _numeric_from_text(value)
    if numeric_value is None:
        return None

    return round(numeric_value, 4)


def _normalize_bias(value: Any) -> str:
    """Normalize Claude bias labels to the compact schema."""
    text = _truncate_text(value).lower().replace(" ", "_").replace("-", "_")
    if text in ALLOWED_BIASES:
        return text
    if "bull" in text:
        return "bullish"
    if "bear" in text:
        return "bearish"
    return "neutral"


def _normalize_actionability(value: Any) -> str:
    """Normalize same-day actionability to the allowed values."""
    text = _truncate_text(value).lower().replace(" ", "_").replace("-", "_")
    if text in ALLOWED_ACTIONABILITY:
        return text
    if "ready" in text:
        return "ready_today"
    if "breakout" in text:
        return "breakout_only"
    if "pullback" in text or "retest" in text:
        return "pullback_only"
    if "avoid" in text or "no_trade" in text:
        return "avoid"
    return "needs_more_time"


def _normalize_setup_quality(value: Any) -> str:
    """Normalize setup_quality to A/B/C/D/F."""
    text = _truncate_text(value).upper().strip()
    if text in ALLOWED_SETUP_QUALITIES:
        return text

    for char in text:
        if char in ALLOWED_SETUP_QUALITIES:
            return char

    return "C"


def normalize_ai_analysis(
    data: Dict[str, Any],
    symbol: Optional[str] = None,
    deterministic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Coerce Claude output into the compact schema and cap verbose fields.
    """
    source = data if isinstance(data, dict) else {}
    symbol_clean = _truncate_text(
        source.get("symbol") or symbol or "UNKNOWN", max_chars=16, default="UNKNOWN"
    ).upper()

    normalized: Dict[str, Any] = {
        "symbol": symbol_clean,
        "overall_score": _normalize_score(source.get("overall_score")),
        "bias": _normalize_bias(source.get("bias")),
        "setup_type": _truncate_text(
            source.get("setup_type"), max_chars=80, default="no_clear_setup"
        ),
        "setup_quality": _normalize_setup_quality(source.get("setup_quality")),
        "actionability": _normalize_actionability(source.get("actionability")),
        "trigger_level": _normalize_level(source.get("trigger_level")),
        "invalidation_level": _normalize_level(source.get("invalidation_level")),
        "do_not_chase_above": _normalize_level(source.get("do_not_chase_above")),
        "ema_assessment": _truncate_text(source.get("ema_assessment")),
        "volume_assessment": _truncate_text(source.get("volume_assessment")),
        "ignition_assessment": _truncate_text(source.get("ignition_assessment")),
        "accumulation_distribution_assessment": _truncate_text(
            source.get("accumulation_distribution_assessment")
        ),
        "entry_idea": _truncate_text(source.get("entry_idea")),
        "stop_idea": _truncate_text(source.get("stop_idea")),
        "target_idea": _truncate_text(source.get("target_idea")),
        "warnings": _list_of_strings(source.get("warnings"), max_items=3),
        "disqualifiers": _list_of_strings(source.get("disqualifiers"), max_items=3),
        "same_day_plan": _list_of_strings(source.get("same_day_plan"), max_items=3),
        "why_today": _truncate_text(source.get("why_today")),
        "final_verdict": _truncate_text(
            source.get("final_verdict"),
            max_chars=300,
            default="No concise verdict provided.",
        ),
    }

    if isinstance(source.get("key_levels"), dict):
        normalized["key_levels"] = source["key_levels"]

    if isinstance(deterministic_context, dict):
        for context_key in ["today_focus", "focus_structure"]:
            if isinstance(source.get(context_key), dict):
                normalized[context_key] = source[context_key]
            elif isinstance(deterministic_context.get(context_key), dict):
                normalized[context_key] = deterministic_context[context_key]

    return normalized


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    """Normalize a string choice into a known allowed value."""
    text = _truncate_text(value).lower().replace(" ", "_").replace("-", "_")
    if text in allowed:
        return text

    return default


def _as_bool_value(value: Any, default: bool = True) -> bool:
    """Interpret common boolean-like values for structured model output."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False

    return bool(value)


def _deterministic_levels_from_context(
    deterministic_context: Optional[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """Read deterministic levels; these override any Claude-created levels."""
    context = deterministic_context if isinstance(deterministic_context, dict) else {}
    levels = context.get("deterministic_levels")
    if not isinstance(levels, dict):
        levels = {}

    today_focus = context.get("today_focus")
    if not isinstance(today_focus, dict):
        today_focus = {}

    return {
        "trigger_level": _normalize_level(
            levels.get("trigger_level", today_focus.get("trigger_level"))
        ),
        "invalidation_level": _normalize_level(
            levels.get("invalidation_level", today_focus.get("invalidation_level"))
        ),
        "do_not_chase_above": _normalize_level(
            levels.get("do_not_chase_above", today_focus.get("do_not_chase_above"))
        ),
    }


def _default_setup_judge_actionability(
    deterministic_context: Optional[Dict[str, Any]],
) -> str:
    """Use deterministic actionability as the setup-judge default when valid."""
    context = deterministic_context if isinstance(deterministic_context, dict) else {}
    today_focus = context.get("today_focus")
    if not isinstance(today_focus, dict):
        return "watch_only"

    actionability = str(today_focus.get("actionability") or "").strip().lower()
    if actionability in ALLOWED_SETUP_JUDGE_ACTIONABILITY:
        return actionability

    return "watch_only"


def normalize_setup_judge(
    data: Dict[str, Any],
    symbol: Optional[str] = None,
    deterministic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Coerce Setup Judge output into the v1 schema.

    Deterministic levels are always the source of truth and overwrite Claude.
    """
    source = data if isinstance(data, dict) else {}
    symbol_clean = _truncate_text(
        source.get("symbol") or symbol or "UNKNOWN", max_chars=16, default="UNKNOWN"
    ).upper()
    deterministic_levels = _deterministic_levels_from_context(deterministic_context)

    judge_action = _normalize_choice(
        source.get("judge_action"),
        ALLOWED_SETUP_JUDGE_ACTIONS,
        "approve",
    )
    manual_review_pass = _as_bool_value(
        source.get("manual_review_pass"), default=judge_action != "veto"
    )
    actionability = _normalize_choice(
        source.get("actionability"),
        ALLOWED_SETUP_JUDGE_ACTIONABILITY,
        _default_setup_judge_actionability(deterministic_context),
    )

    if judge_action == "veto" or actionability == "reject" or not manual_review_pass:
        judge_action = "veto"
        manual_review_pass = False
        actionability = "reject"

    normalized = {
        "symbol": symbol_clean,
        "judge_version": "setup_judge_v1",
        "manual_review_pass": manual_review_pass,
        "judge_action": judge_action,
        "judge_rank_score": _normalize_score(source.get("judge_rank_score")),
        "setup_grade": _normalize_setup_quality(source.get("setup_grade")),
        "actionability": actionability,
        "blueprint_pattern": _normalize_choice(
            source.get("blueprint_pattern"),
            ALLOWED_BLUEPRINT_PATTERNS,
            "other",
        ),
        "trigger_level": deterministic_levels["trigger_level"],
        "invalidation_level": deterministic_levels["invalidation_level"],
        "do_not_chase_above": deterministic_levels["do_not_chase_above"],
        "level_quality": _normalize_choice(
            source.get("level_quality"), ALLOWED_LEVEL_QUALITY, "unclear"
        ),
        "chase_risk": _normalize_choice(
            source.get("chase_risk"), ALLOWED_CHASE_RISK, "medium"
        ),
        "structure_quality": _normalize_choice(
            source.get("structure_quality"),
            ALLOWED_STRUCTURE_QUALITY,
            "acceptable",
        ),
        "volume_quality": _normalize_choice(
            source.get("volume_quality"), ALLOWED_VOLUME_QUALITY, "neutral"
        ),
        "one_line_thesis": _truncate_text(
            source.get("one_line_thesis"),
            max_chars=220,
            default="No setup judge thesis provided.",
        ),
        "top_reasons": _list_of_strings(source.get("top_reasons"), max_items=3),
        "veto_reasons": _list_of_strings(source.get("veto_reasons"), max_items=3),
        "watch_plan": _list_of_strings(source.get("watch_plan"), max_items=3),
    }
    if normalized["judge_action"] == "veto" and not normalized["veto_reasons"]:
        normalized["veto_reasons"] = ["Setup Judge vetoed this candidate."]

    return normalized


def validate_setup_judge(judge: Any) -> Tuple[bool, Optional[str]]:
    """Validate a normalized Setup Judge v1 dictionary."""
    if not isinstance(judge, dict):
        return False, "setup judge is not a dictionary"

    for field in REQUIRED_SETUP_JUDGE_FIELDS:
        if field not in judge:
            return False, f"missing required field: {field}"

    extra_fields = set(judge) - set(REQUIRED_SETUP_JUDGE_FIELDS)
    if extra_fields:
        return False, f"unexpected field: {sorted(extra_fields)[0]}"

    if judge.get("judge_version") != "setup_judge_v1":
        return False, "judge_version is not setup_judge_v1"

    if not isinstance(judge.get("manual_review_pass"), bool):
        return False, "manual_review_pass is not boolean"

    score = _as_float(judge.get("judge_rank_score"))
    if score is None or not 0 <= score <= 100:
        return False, "judge_rank_score is not 0-100"

    if judge.get("judge_action") not in ALLOWED_SETUP_JUDGE_ACTIONS:
        return False, "judge_action is invalid"
    if judge.get("setup_grade") not in ALLOWED_SETUP_QUALITIES:
        return False, "setup_grade is invalid"
    if judge.get("actionability") not in ALLOWED_SETUP_JUDGE_ACTIONABILITY:
        return False, "actionability is invalid"
    if judge.get("blueprint_pattern") not in ALLOWED_BLUEPRINT_PATTERNS:
        return False, "blueprint_pattern is invalid"
    if judge.get("level_quality") not in ALLOWED_LEVEL_QUALITY:
        return False, "level_quality is invalid"
    if judge.get("chase_risk") not in ALLOWED_CHASE_RISK:
        return False, "chase_risk is invalid"
    if judge.get("structure_quality") not in ALLOWED_STRUCTURE_QUALITY:
        return False, "structure_quality is invalid"
    if judge.get("volume_quality") not in ALLOWED_VOLUME_QUALITY:
        return False, "volume_quality is invalid"

    for field in ["trigger_level", "invalidation_level", "do_not_chase_above"]:
        if judge.get(field) is not None and _as_float(judge.get(field)) is None:
            return False, f"{field} is not numeric or null"

    for field in ["top_reasons", "veto_reasons", "watch_plan"]:
        if not isinstance(judge.get(field), list):
            return False, f"{field} is not an array"
        if len(judge[field]) > 3:
            return False, f"{field} has more than 3 items"

    return True, None


def build_failed_setup_judge(
    symbol: str, reason: str, raw_response: Optional[str] = None
) -> Dict[str, Any]:
    """Return an explicit failed Setup Judge result."""
    symbol_clean = str(symbol).strip().upper() if symbol else "UNKNOWN"
    reason_text = str(reason).strip() if reason else "unknown Setup Judge failure"

    failed = normalize_setup_judge(
        {
            "symbol": symbol_clean,
            "manual_review_pass": False,
            "judge_action": "veto",
            "judge_rank_score": 0,
            "setup_grade": "F",
            "actionability": "reject",
            "blueprint_pattern": "other",
            "level_quality": "unclear",
            "chase_risk": "high",
            "structure_quality": "messy",
            "volume_quality": "weak",
            "one_line_thesis": f"Setup Judge failed: {reason_text}",
            "top_reasons": [],
            "veto_reasons": [reason_text],
            "watch_plan": [],
        },
        symbol_clean,
    )
    failed["setup_judge_failed"] = True
    failed["error"] = reason_text
    failed["raw_response"] = raw_response
    return failed


def validate_ai_analysis(analysis: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate that Claude returned the minimum required structured fields.
    """
    if not isinstance(analysis, dict):
        return False, "analysis is not a dictionary"

    for field in REQUIRED_AI_FIELDS:
        if field not in analysis:
            return False, f"missing required field: {field}"

    for field in ["symbol", "bias", "setup_type", "setup_quality", "final_verdict"]:
        value = analysis.get(field)
        if value is None or not str(value).strip():
            return False, f"{field} is empty"

    if _as_float(analysis.get("overall_score")) is None:
        return False, "overall_score is not numeric"

    if not 0 <= float(analysis["overall_score"]) <= 100:
        return False, "overall_score is outside 0-100"

    if analysis.get("bias") not in ALLOWED_BIASES:
        return False, "bias is not one of bullish, bearish, neutral"

    if analysis.get("setup_quality") not in ALLOWED_SETUP_QUALITIES:
        return False, "setup_quality is not one of A, B, C, D, F"

    if analysis.get("actionability") not in ALLOWED_ACTIONABILITY:
        return False, "actionability is not an allowed value"

    for field in ["trigger_level", "invalidation_level", "do_not_chase_above"]:
        if analysis.get(field) is not None and _as_float(analysis.get(field)) is None:
            return False, f"{field} is not numeric or null"

    for field in ["warnings", "disqualifiers", "same_day_plan"]:
        if not isinstance(analysis.get(field), list):
            return False, f"{field} is not an array"

    return True, None


def build_failed_analysis(
    symbol: str, reason: str, raw_response: Optional[str] = None
) -> Dict[str, Any]:
    """
    Return a standardized explicit failure object for report generation.
    """
    symbol_clean = str(symbol).strip().upper() if symbol else "UNKNOWN"
    reason_text = str(reason).strip() if reason else "unknown AI analysis failure"

    return {
        "symbol": symbol_clean,
        "analysis_failed": True,
        "error": reason_text,
        "raw_response": raw_response,
        "overall_score": None,
        "bias": None,
        "setup_type": None,
        "setup_quality": None,
        "actionability": "avoid",
        "trigger_level": None,
        "invalidation_level": None,
        "do_not_chase_above": None,
        "ema_assessment": "",
        "volume_assessment": "",
        "ignition_assessment": "",
        "accumulation_distribution_assessment": "",
        "entry_idea": "",
        "stop_idea": "",
        "target_idea": "",
        "same_day_plan": [],
        "why_today": "",
        "final_verdict": f"AI analysis failed: {reason_text}",
        "warnings": [],
        "disqualifiers": [],
    }


def _level_token(level: float) -> str:
    """Format a level the way Claude usually references it in prose."""
    return f"{level:.2f}"


def _filter_numeric_levels(levels: Any, current_price: float, side: str) -> list[float]:
    """Keep numeric levels on the requested side of current price."""
    filtered_levels = []

    for level in levels or []:
        numeric_level = _as_float(level)
        if numeric_level is None:
            continue

        if side == "below" and numeric_level < current_price:
            filtered_levels.append(numeric_level)
        elif side == "above" and numeric_level > current_price:
            filtered_levels.append(numeric_level)

    reverse_sort = side == "below"
    return sorted(filtered_levels, reverse=reverse_sort)


def _filter_zones(zones: Any, current_price: float, side: str) -> list[Dict[str, Any]]:
    """Keep zones fully below or fully above current price."""
    filtered_zones = []

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
            filtered_zones.append(normalized_zone)
        elif side == "above" and low > current_price:
            filtered_zones.append(normalized_zone)

    if side == "below":
        return sorted(filtered_zones, key=lambda item: item["high"], reverse=True)

    return sorted(filtered_zones, key=lambda item: item["low"])


def _remove_bad_level_sentences(text: Any, invalid_overhead_levels: list[float]) -> str:
    """
    Remove prose that treats reclaimed below-price levels as active overhead.
    """
    if not isinstance(text, str) or not invalid_overhead_levels:
        return text if isinstance(text, str) else ""

    bad_terms = [
        "overhead",
        "resistance",
        "breakout above",
        "target barrier",
        "upside obstacle",
        "cap",
        "caps",
        "capped",
        "limit upside",
        "limits upside",
        "constrain",
        "constrains",
    ]
    safe_terms = ["reclaimed", "below price", "below current", "support", "pullback", "retest"]
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept_sentences = []

    for sentence in sentences:
        sentence_lower = sentence.lower()
        references_invalid_level = any(
            _level_token(level) in sentence for level in invalid_overhead_levels
        )
        treats_as_overhead = any(term in sentence_lower for term in bad_terms)
        explicitly_safe = any(term in sentence_lower for term in safe_terms)

        if references_invalid_level and treats_as_overhead and not explicitly_safe:
            continue

        kept_sentences.append(sentence)

    return " ".join(kept_sentences).strip()


def _enforce_level_interpretation(
    technicals: Dict[str, Any], analysis: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Enforce current-price-relative level interpretation after Claude returns.

    The prompt asks Claude to do this, but this deterministic guard keeps future
    modules from receiving active overhead levels that are actually below price.
    """
    if "error" in analysis:
        return analysis

    current_price = _as_float(technicals.get("ema_regime", {}).get("close"))
    if current_price is None:
        return analysis

    support_resistance = technicals.get("support_resistance", {})
    supply_demand = technicals.get("supply_demand_zones", {})

    support_below = _filter_numeric_levels(
        support_resistance.get("support_levels"), current_price, "below"
    )
    resistance_above = _filter_numeric_levels(
        support_resistance.get("resistance_levels"), current_price, "above"
    )
    reclaimed_resistance = _filter_numeric_levels(
        support_resistance.get("resistance_levels"), current_price, "below"
    )
    demand_below = _filter_zones(supply_demand.get("demand_zones"), current_price, "below")
    supply_above = _filter_zones(supply_demand.get("supply_zones"), current_price, "above")
    reclaimed_supply = _filter_zones(supply_demand.get("supply_zones"), current_price, "below")

    corrected = dict(analysis)
    corrected["key_levels"] = {
        "support": support_below[:5],
        "resistance": resistance_above[:5],
        "demand_zones": demand_below[:3],
        "supply_zones": supply_above[:3],
    }

    invalid_overhead_levels = reclaimed_resistance[:]
    for zone in reclaimed_supply:
        invalid_overhead_levels.extend([zone["low"], zone["high"]])

    for field in ["entry_idea", "target_idea", "final_verdict"]:
        corrected[field] = _remove_bad_level_sentences(
            corrected.get(field, ""), invalid_overhead_levels
        )

    if not corrected.get("entry_idea"):
        entry_references = []
        if support_below:
            entry_references.append(f"nearest support below price at {_level_token(support_below[0])}")
        if resistance_above:
            entry_references.append(
                f"breakout confirmation above active resistance at {_level_token(resistance_above[0])}"
            )

        corrected["entry_idea"] = (
            "No clear entry idea from the provided data."
            if not entry_references
            else "Use current-price-relative levels only: "
            + "; ".join(entry_references)
            + "."
        )

    if not corrected.get("target_idea"):
        corrected["target_idea"] = (
            f"Nearest active resistance above current price is {_level_token(resistance_above[0])}."
            if resistance_above
            else "No clear upside target from active resistance above current price."
        )

    warnings = corrected.get("warnings", [])
    if isinstance(warnings, list):
        corrected["warnings"] = [
            cleaned_warning
            for warning in warnings
            if (
                cleaned_warning := _remove_bad_level_sentences(
                    warning, invalid_overhead_levels
                )
            )
        ]

    return corrected


def _call_claude(client: anthropic.Anthropic, prompt: str, model: str, max_tokens: int) -> str:
    """Call Claude and return extracted text content."""
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )
    return _extract_response_text(message)


def _build_retry_prompt(symbol: str, invalid_response: str, validation_error: str) -> str:
    """Build the one-shot repair prompt for invalid Claude JSON."""
    invalid_excerpt = _truncate_text(invalid_response, max_chars=4000)
    return f"""
You returned invalid JSON.
Return only a valid JSON object matching this exact schema.
Do not include markdown.
Do not include comments.
Do not include explanations.
Preserve the same analysis intent but make it concise.

Symbol: {symbol}
Issue: {validation_error}

Schema:
{_schema_text()}

Invalid response:
{invalid_excerpt}
""".strip()


def build_setup_judge_prompt(symbol: str, judge_context: Dict[str, Any]) -> str:
    """Build the JSON-only Setup Judge v1 prompt."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string.")
    if not isinstance(judge_context, dict):
        raise ValueError("judge_context must be a dictionary.")

    symbol_clean = symbol.strip().upper()
    context_json = json.dumps(judge_context, separators=(",", ":"), default=_json_default)

    return f"""
You are Claude Setup Judge v1, a manual chart-review layer for The Options
Cartel Blueprint. Review only the deterministic pass candidate below.

Return JSON only. No Markdown. No code fences. No extra keys.

Decision rules:
- You may approve, downgrade, or veto this deterministic pass candidate.
- You must not create, change, or optimize trigger/invalidation/do-not-chase
  levels.
- Echo the deterministic levels exactly as provided, or null if provided null.
- Judge level quality, structure quality, chase risk, and volume quality.
- Treat blueprint_fit_score and blueprint_fit_fail_reasons as the deterministic
  read on whether this actually resembles the focus-list examples.
- Use primary_gate and sector_leadership as context only; deterministic levels
  remain the source of truth.
- Veto only when the setup should not receive full Claude analysis today.
- Keep all text short. Arrays must have at most 3 strings.

Required JSON schema:
{_setup_judge_schema_text()}

Symbol:
{symbol_clean}

Deterministic candidate context:
{context_json}
""".strip()


def _build_setup_judge_retry_prompt(
    symbol: str, invalid_response: str, validation_error: str
) -> str:
    """Build the one-shot Setup Judge repair prompt."""
    invalid_excerpt = _truncate_text(invalid_response, max_chars=3000)
    return f"""
You returned invalid Setup Judge JSON.
Return only one valid JSON object matching this exact schema.
Do not include Markdown, comments, explanations, or extra keys.
Preserve the same review intent.

Symbol: {symbol}
Issue: {validation_error}

Schema:
{_setup_judge_schema_text()}

Invalid response:
{invalid_excerpt}
""".strip()


def _apply_deterministic_levels_to_setup_judge(
    judge: Dict[str, Any], judge_context: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Overwrite Setup Judge levels from deterministic context."""
    corrected = dict(judge)
    corrected.update(_deterministic_levels_from_context(judge_context))
    return corrected


def _parse_validate_setup_judge(
    symbol: str, judge_context: Dict[str, Any], raw_response: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Parse, normalize, and validate one Setup Judge response."""
    if not raw_response:
        reason = "Claude Setup Judge returned an empty response."
        failed = build_failed_setup_judge(symbol, reason, raw_response)
        return _apply_deterministic_levels_to_setup_judge(failed, judge_context), reason

    parsed = _parse_json_response(symbol, raw_response)
    if isinstance(parsed, dict) and parsed.get("error") == "Claude response was not valid JSON.":
        failed = build_failed_setup_judge(symbol, parsed["error"], raw_response)
        return (
            _apply_deterministic_levels_to_setup_judge(failed, judge_context),
            parsed["error"],
        )
    if not isinstance(parsed, dict):
        reason = "setup judge is not a dictionary"
        failed = build_failed_setup_judge(symbol, reason, raw_response)
        return _apply_deterministic_levels_to_setup_judge(failed, judge_context), reason

    if "symbol" not in parsed:
        parsed["symbol"] = symbol

    normalized = normalize_setup_judge(parsed, symbol, deterministic_context=judge_context)
    is_valid, validation_error = validate_setup_judge(normalized)
    if not is_valid:
        failed = build_failed_setup_judge(symbol, validation_error, raw_response)
        return (
            _apply_deterministic_levels_to_setup_judge(failed, judge_context),
            validation_error,
        )

    return normalized, None


def analyze_setup_with_claude(
    symbol: str,
    judge_context: Dict[str, Any],
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = SETUP_JUDGE_MAX_TOKENS,
) -> Dict[str, Any]:
    """Ask Claude Setup Judge v1 to review one deterministic pass candidate."""
    symbol_clean = symbol.strip().upper()
    prompt = build_setup_judge_prompt(symbol_clean, judge_context)

    try:
        client = load_anthropic_client()
    except Exception as exc:
        failed = build_failed_setup_judge(
            symbol_clean, f"Claude Setup Judge API call failed: {exc}"
        )
        return _apply_deterministic_levels_to_setup_judge(failed, judge_context)

    try:
        raw_response = _call_claude(client, prompt, model, max_tokens)
    except Exception as exc:
        failed = build_failed_setup_judge(
            symbol_clean, f"Claude Setup Judge API call failed: {exc}"
        )
        return _apply_deterministic_levels_to_setup_judge(failed, judge_context)

    judge, validation_error = _parse_validate_setup_judge(
        symbol_clean, judge_context, raw_response
    )
    if validation_error is None:
        return judge

    print(
        f"Retrying Setup Judge for {symbol_clean} because: {validation_error}",
        flush=True,
    )
    retry_prompt = _build_setup_judge_retry_prompt(
        symbol_clean, raw_response, validation_error
    )

    try:
        retry_raw_response = _call_claude(
            client, retry_prompt, model, SETUP_JUDGE_REPAIR_MAX_TOKENS
        )
    except Exception as exc:
        failed = build_failed_setup_judge(
            symbol_clean,
            f"Claude Setup Judge retry API call failed: {exc}",
            raw_response,
        )
        return _apply_deterministic_levels_to_setup_judge(failed, judge_context)

    retry_judge, retry_error = _parse_validate_setup_judge(
        symbol_clean, judge_context, retry_raw_response
    )
    if retry_error is None:
        return retry_judge

    failed = build_failed_setup_judge(symbol_clean, retry_error, retry_raw_response)
    return _apply_deterministic_levels_to_setup_judge(failed, judge_context)


def _parse_validate_and_enforce(
    symbol: str, technicals: Dict[str, Any], raw_response: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Parse, level-correct, and validate one Claude response."""
    if not raw_response:
        reason = "Claude returned an empty response."
        return build_failed_analysis(symbol, reason, raw_response), reason

    parsed = _parse_json_response(symbol, raw_response)
    if isinstance(parsed, dict) and parsed.get("error") == "Claude response was not valid JSON.":
        return (
            build_failed_analysis(symbol, parsed["error"], raw_response),
            parsed["error"],
        )
    if not isinstance(parsed, dict):
        reason = "analysis is not a dictionary"
        return build_failed_analysis(symbol, reason, raw_response), reason

    if "symbol" not in parsed:
        parsed["symbol"] = symbol

    normalized = normalize_ai_analysis(parsed, symbol, deterministic_context=technicals)
    corrected = _enforce_level_interpretation(technicals, normalized)
    corrected = normalize_ai_analysis(corrected, symbol, deterministic_context=technicals)
    is_valid, validation_error = validate_ai_analysis(corrected)
    if not is_valid:
        return build_failed_analysis(symbol, validation_error, raw_response), validation_error

    return corrected, None


def load_anthropic_client() -> anthropic.Anthropic:
    """
    Load environment variables and return an Anthropic client.
    """
    load_dotenv()
    api_key = os.getenv(ANTHROPIC_API_KEY_ENV)

    if not api_key or api_key.strip() in {"", "your_key_here"}:
        raise ValueError(
            f"{ANTHROPIC_API_KEY_ENV} is missing. Add it to .env before running Claude analysis."
        )

    return anthropic.Anthropic(api_key=api_key.strip())


def build_blueprint_prompt(symbol: str, technicals: Dict[str, Any]) -> str:
    """
    Build the JSON-only blueprint evaluation prompt for Claude.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string.")
    if not isinstance(technicals, dict):
        raise ValueError("technicals must be a dictionary returned by analyze_stock_technicals().")

    symbol_clean = symbol.strip().upper()
    technicals_json = json.dumps(technicals, separators=(",", ":"), default=_json_default)

    return f"""
You are a concise trading setup evaluator. Use only the structured technical
data below. This is decision support only, not buy/sell advice.

Return JSON only. No Markdown. No code fences. No explanations outside JSON.

Goal: decide whether {symbol_clean} is a clean same-day focus-list candidate.
Respect deterministic today_focus and focus_structure heavily. Penalize sloppy
structure, high extension risk, weak volume, missing trigger, wide
invalidation, or unclear actionability. Do not loosen those deterministic
signals just because the chart is generally strong.
If focus_structure includes blueprint_setup_type, blueprint_setup_score, and
blueprint_fit_score, use them as the named Blueprint daily setup context and
the fit-quality check against the examples.
Use primary_gate and sector_leadership as context only; do not override
deterministic gates or levels.

Length rules:
- final_verdict: max 2 short sentences.
- ema_assessment, volume_assessment, ignition_assessment,
  accumulation_distribution_assessment, entry_idea, stop_idea, target_idea,
  why_today: max 1 short sentence each.
- warnings: max 3 strings, each under 12 words.
- disqualifiers: max 3 strings, each under 12 words.
- same_day_plan: max 3 short bullet-like strings.

Schema rules:
- Return exactly the fields in the schema.
- overall_score must be an integer from 0 to 100.
- bias must be bullish, bearish, or neutral.
- setup_quality must be A, B, C, D, or F.
- actionability must be ready_today, breakout_only, pullback_only,
  needs_more_time, or avoid.
- trigger_level, invalidation_level, and do_not_chase_above must be numbers or
  null. Do not put prose in numeric fields.
- warnings, disqualifiers, and same_day_plan must be arrays of strings.
- Do not include key_levels; the app reconstructs deterministic levels after
  parsing.

Level rules:
- Use ema_regime.close as current price when available.
- Only above-price levels are overhead resistance or supply.
- Below-price supply is reclaimed/pullback context, not overhead.
- Below-price resistance is reclaimed, not active resistance.

Required JSON schema:
{_schema_text()}

Symbol:
{symbol_clean}

Structured technical data:
{technicals_json}
""".strip()


def analyze_with_claude(
    symbol: str,
    technicals: Dict[str, Any],
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = PRIMARY_MAX_TOKENS,
) -> Dict[str, Any]:
    """
    Ask Claude to evaluate one stock and return parsed JSON.
    """
    symbol_clean = symbol.strip().upper()
    prompt = build_blueprint_prompt(symbol_clean, technicals)

    try:
        client = load_anthropic_client()
    except Exception as exc:
        return build_failed_analysis(symbol_clean, f"Claude API call failed: {exc}")

    try:
        raw_response = _call_claude(client, prompt, model, max_tokens)
    except Exception as exc:
        return build_failed_analysis(symbol_clean, f"Claude API call failed: {exc}")

    analysis, validation_error = _parse_validate_and_enforce(
        symbol_clean, technicals, raw_response
    )
    if validation_error is None:
        return analysis

    print(
        f"Retrying Claude analysis for {symbol_clean} because: {validation_error}",
        flush=True,
    )
    retry_prompt = _build_retry_prompt(symbol_clean, raw_response, validation_error)

    try:
        retry_raw_response = _call_claude(
            client, retry_prompt, model, REPAIR_MAX_TOKENS
        )
    except Exception as exc:
        return build_failed_analysis(
            symbol_clean,
            f"Claude retry API call failed: {exc}",
            raw_response,
        )

    retry_analysis, retry_error = _parse_validate_and_enforce(
        symbol_clean, technicals, retry_raw_response
    )
    if retry_error is None:
        return retry_analysis

    return build_failed_analysis(symbol_clean, retry_error, retry_raw_response)


def analyze_stock_with_ai(symbol: str, df: Any) -> Dict[str, Any]:
    """
    Run Module 4 technical analysis, then evaluate the setup with Claude.
    """
    technicals = analyze_stock_technicals(symbol, df)
    return analyze_with_claude(symbol, technicals)


if __name__ == "__main__":
    from data_fetcher import fetch_stock_data

    symbol = "NVDA"

    try:
        stock_data = fetch_stock_data(symbol, period="6mo", interval="1d")
        technical_data = analyze_stock_technicals(symbol, stock_data)
        analysis = analyze_with_claude(symbol, technical_data)
    except Exception as exc:
        analysis = {
            "symbol": symbol,
            "error": str(exc),
            "raw_response": None,
        }

    print(json.dumps(analysis, indent=2, default=_json_default))

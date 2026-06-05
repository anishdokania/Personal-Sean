"""
Claude Haiku visual chart triage for Sean / The Options Cartel setups.

This stage is intentionally separate from deterministic detectors and the
existing final Claude analysis path. Detectors provide metadata/hints; Haiku
judges one standardized 6M daily chart visually.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv

from chart_generator import generate_haiku_triage_chart
from claude_analyzer import (
    DEFAULT_CLAUDE_MODEL,
    extract_json_object,
    load_anthropic_client,
)
from data_fetcher import fetch_stock_data
from vision_reviewer import encode_image_base64


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_STRATEGY_SOURCE_DIR = PROJECT_ROOT / "strategy_sources"
DEFAULT_HAIKU_CHART_DIR = PROJECT_ROOT / "charts" / "haiku_triage"
DEFAULT_HAIKU_WORKERS = 1
DEFAULT_HAIKU_CHART_TIMEFRAME = "6M"
DEFAULT_HAIKU_MAX_TOKENS = 500

ALLOWED_DECISIONS = {"KEEP", "MAYBE", "REJECT"}
ALLOWED_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_SETUP_TYPES = {
    "big_base_near_highs",
    "right_side_base",
    "inside_day",
    "bull_flag_wedge",
    "breakout_retest",
    "post_gap_flag",
    "possible_accumulation",
    "undercut_reclaim",
    "failed_breakdown_reclaim",
    "none",
}

TRIAGE_CSV_COLUMNS = [
    "ticker",
    "company",
    "sector",
    "close",
    "rel_volume",
    "detector_tags",
    "high_value_tags",
    "warning_tags",
    "chart_path",
    "decision",
    "confidence",
    "setup_type",
    "visual_quality_1_to_10",
    "trigger_level",
    "invalidation_level",
    "reason_short",
    "warning",
    "raw_response",
    "parse_error",
    "elapsed_seconds",
]


@dataclass
class HaikuTriageCandidate:
    """One ticker selected for Haiku visual chart triage."""

    ticker: str
    company: str = ""
    sector: str = ""
    close: Optional[float] = None
    rel_volume: Optional[float] = None
    detector_tags: str = ""
    high_value_tags: str = ""
    warning_tags: str = ""
    trigger_level: Optional[float] = None
    stop_reference: Optional[float] = None
    setup_family: str = ""
    source_kind: str = ""
    source_rank: float = 0.0
    chart_path: str = ""
    source_row: dict[str, Any] = field(default_factory=dict)


def _json_default(value: Any) -> Any:
    """Convert uncommon numeric/date objects into JSON-safe values."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def _as_float(value: Any) -> Optional[float]:
    """Return a finite float when possible, otherwise None."""
    if isinstance(value, bool):
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numeric):
        return None

    return numeric


def _as_int_1_to_10(value: Any) -> int:
    """Return a visual quality integer clamped to 1-10."""
    numeric = _as_float(value)
    if numeric is None:
        return 1

    return int(max(1, min(10, round(numeric))))


def _as_bool(value: Any) -> bool:
    """Interpret common boolean-like values safely."""
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}

    return bool(value)


def _clean_value(value: Any) -> Any:
    """Return CSV/JSON values as plain Python values, converting NaN to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(value, "item"):
        return value.item()

    return value


def _clean_text(value: Any, max_chars: int = 240) -> str:
    """Return a single-line text field capped to a practical length."""
    cleaned = _clean_value(value)
    if cleaned is None:
        return ""

    if isinstance(cleaned, (list, tuple, set)):
        text = "; ".join(str(item).strip() for item in cleaned if str(item).strip())
    else:
        text = str(cleaned).strip()

    text = re.sub(r"\s+", " ", text).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."

    return text


def _short_text(value: Any, max_words: int, max_chars: int) -> str:
    """Return short model text bounded by words and characters."""
    text = _clean_text(value, max_chars=max_chars)
    if not text:
        return ""

    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])

    return text[:max_chars].rstrip()


def _join_tags(value: Any) -> str:
    """Normalize detector/audit tag strings or arrays to semicolon text."""
    if value is None:
        return ""
    if isinstance(value, str):
        pieces = re.split(r"[;,]", value)
    elif isinstance(value, (list, tuple, set)):
        pieces = [str(item) for item in value]
    else:
        pieces = [str(value)]

    output: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        text = piece.strip()
        if not text or text in seen or text.lower() in {"nan", "none"}:
            continue
        seen.add(text)
        output.append(text)

    return "; ".join(output)


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    """Read the first non-empty row value from possible column names."""
    for key in keys:
        value = row.get(key)
        if _clean_value(value) is not None and _clean_text(value) != "":
            return value

    return None


def _load_prompt_file(prompts_dir: Path, filename: str) -> str:
    """Load one prompt file from the configured prompt directory."""
    path = prompts_dir / filename
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")

    return text


def load_haiku_prompts(prompts_dir: str | Path = DEFAULT_PROMPTS_DIR) -> dict[str, str]:
    """Load static prompt files used for Haiku chart triage."""
    prompt_path = Path(prompts_dir).expanduser()
    return {
        "strategy_master": _load_prompt_file(prompt_path, "STRATEGY_MASTER.md"),
        "triage_prompt": _load_prompt_file(prompt_path, "HAIKU_CHART_TRIAGE_PROMPT.md"),
        "output_schema": _load_prompt_file(prompt_path, "OUTPUT_SCHEMA.md"),
    }


def default_haiku_model() -> str:
    """Return the configured Haiku triage model."""
    load_dotenv()
    return os.getenv("ANTHROPIC_HAIKU_TRIAGE_MODEL", DEFAULT_CLAUDE_MODEL).strip()


def _extract_response_text(message: Any) -> str:
    """Extract text from an Anthropic message response."""
    text_parts = []

    for block in getattr(message, "content", []):
        block_text = getattr(block, "text", None)
        if block_text:
            text_parts.append(block_text)

    return "\n".join(text_parts).strip()


def _read_json_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Read detector/audit records from a JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
        return [dict(item) for item in payload["candidates"] if isinstance(item, dict)], "detector"
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [dict(item) for item in payload["results"] if isinstance(item, dict)], "haiku_triage"
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)], "generic"

    raise ValueError(f"Unsupported JSON source shape: {path}")


def _read_source_records(source_path: str | Path) -> tuple[list[dict[str, Any]], str]:
    """Read candidate rows and infer source kind from CSV/JSON shape."""
    path = Path(source_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Haiku triage source file not found: {source_path}")
    if not path.is_file():
        raise ValueError(f"Haiku triage source path is not a file: {source_path}")

    if path.suffix.lower() == ".json":
        records, source_kind = _read_json_records(path)
    else:
        frame = pd.read_csv(path)
        records = [
            {key: _clean_value(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
        columns = set(frame.columns)
        if {"detector_tags", "chart_needed", "interest_rank"} & columns:
            source_kind = "detector"
        elif {"PassedFocusGate", "primary_gate_pass", "FinalPreAIScore"} & columns:
            source_kind = "audit"
        else:
            source_kind = "generic"

    return records, source_kind


def _sort_key(value: Any, default: float = 0.0) -> float:
    """Return a numeric sort key."""
    numeric = _as_float(value)
    return numeric if numeric is not None else default


def _select_detector_records(
    records: list[dict[str, Any]], review_source: str
) -> list[dict[str, Any]]:
    """Select detector-source rows for triage."""
    source = review_source.strip().lower()
    rows = list(records)

    if source == "priority":
        selected = [row for row in rows if _as_bool(row.get("chart_needed"))]
        if not selected:
            selected = [
                row
                for row in rows
                if _sort_key(row.get("detector_count")) > 0
                and not _clean_text(row.get("reject_reason"))
            ]
        if not selected:
            selected = rows
    elif source == "watch":
        selected = [
            row
            for row in rows
            if _as_bool(row.get("chart_needed"))
            or _join_tags(row.get("high_value_tags"))
            or (
                _sort_key(row.get("detector_count")) > 0
                and not _clean_text(row.get("reject_reason"))
            )
        ]
    else:
        selected = rows

    selected.sort(
        key=lambda row: (
            _as_bool(row.get("chart_needed")),
            _sort_key(row.get("interest_rank")),
            _sort_key(row.get("detector_count")),
            _sort_key(row.get("rel_volume")),
        ),
        reverse=True,
    )
    return selected


def _select_audit_records(
    records: list[dict[str, Any]], review_source: str
) -> list[dict[str, Any]]:
    """Select focus-audit rows for triage."""
    source = review_source.strip().lower()
    rows = list(records)
    primary_rows = (
        [row for row in rows if _as_bool(row.get("primary_gate_pass"))]
        if any("primary_gate_pass" in row for row in rows)
        else rows
    )

    if source == "priority":
        selected = [row for row in rows if _as_bool(row.get("PassedFocusGate"))]
        if not selected:
            selected = primary_rows
    elif source == "watch":
        selected = [
            row
            for row in primary_rows
            if _as_bool(row.get("PassedFocusGate"))
            or _sort_key(row.get("FinalPreAIScore")) > 0
            or _sort_key(row.get("FocusStructureScore")) > 0
        ]
    elif source == "all":
        selected = rows
    else:
        selected = primary_rows

    selected.sort(
        key=lambda row: (
            _as_bool(row.get("PassedFocusGate")),
            _sort_key(row.get("FinalPreAIScore")),
            _sort_key(row.get("BlueprintFitScore")),
            _sort_key(row.get("FocusStructureScore")),
            _sort_key(row.get("TodayFocusScore")),
            _sort_key(row.get("avg_volume")),
            _sort_key(row.get("price")),
        ),
        reverse=True,
    )
    return selected


def _select_source_records(
    records: list[dict[str, Any]],
    source_kind: str,
    review_source: str,
) -> list[dict[str, Any]]:
    """Select raw records by source kind and review source option."""
    source = review_source.strip().lower()
    if source not in {"primary", "priority", "watch", "all"}:
        raise ValueError("haiku_review_source must be primary, priority, watch, or all")

    if source_kind == "detector":
        return _select_detector_records(records, source)
    if source_kind == "audit":
        return _select_audit_records(records, source)

    return list(records)


def _candidate_from_record(row: dict[str, Any], source_kind: str) -> Optional[HaikuTriageCandidate]:
    """Build a normalized triage candidate from a detector/audit row."""
    ticker = _clean_text(_row_value(row, "ticker", "Symbol", "symbol"), max_chars=16).upper()
    if not ticker:
        return None

    if source_kind == "detector":
        trigger = _as_float(_row_value(row, "trigger_level", "TriggerLevel"))
        stop = _as_float(_row_value(row, "stop_reference", "InvalidationLevel"))
        source_rank = _sort_key(row.get("interest_rank"))
    else:
        trigger = _as_float(_row_value(row, "TriggerLevel", "trigger_level"))
        stop = _as_float(
            _row_value(row, "InvalidationLevel", "stop_reference", "StopReference")
        )
        source_rank = _sort_key(row.get("FinalPreAIScore"))

    return HaikuTriageCandidate(
        ticker=ticker,
        company=_clean_text(_row_value(row, "company", "Company", "Security")),
        sector=_clean_text(_row_value(row, "sector", "Sector", "sector_name", "Industry")),
        close=_as_float(_row_value(row, "close", "price", "Close")),
        rel_volume=_as_float(_row_value(row, "rel_volume", "RelVolume")),
        detector_tags=_join_tags(row.get("detector_tags")),
        high_value_tags=_join_tags(row.get("high_value_tags")),
        warning_tags=_join_tags(row.get("warning_tags")),
        trigger_level=trigger,
        stop_reference=stop,
        setup_family=_clean_text(_row_value(row, "setup_family", "StructureType")),
        source_kind=source_kind,
        source_rank=source_rank,
        source_row=dict(row),
    )


def load_haiku_triage_candidates(
    source_path: str | Path,
    review_source: str = "priority",
    limit: Optional[int] = None,
) -> tuple[list[HaikuTriageCandidate], str]:
    """Load and select candidates from a detector CSV/JSON or focus audit CSV."""
    records, source_kind = _read_source_records(source_path)
    selected_records = _select_source_records(records, source_kind, review_source)
    if limit is not None:
        if limit <= 0:
            raise ValueError("haiku_triage_limit must be a positive integer when set")
        selected_records = selected_records[:limit]

    candidates = [
        candidate
        for row in selected_records
        if (candidate := _candidate_from_record(row, source_kind)) is not None
    ]
    return candidates, source_kind


def _latest_rel_volume(df: pd.DataFrame) -> Optional[float]:
    """Return latest volume divided by 20-day average volume when calculable."""
    try:
        volumes = pd.to_numeric(df["Volume"], errors="coerce").dropna()
    except Exception:
        return None

    if len(volumes) < 20:
        return None

    latest = _as_float(volumes.iloc[-1])
    avg_volume = _as_float(volumes.tail(20).mean())
    if latest is None or avg_volume is None or avg_volume <= 0:
        return None

    return latest / avg_volume


def _latest_close(df: pd.DataFrame) -> Optional[float]:
    """Return latest close from an OHLCV DataFrame."""
    try:
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    except Exception:
        return None

    if closes.empty:
        return None

    return _as_float(closes.iloc[-1])


def prepare_candidate_chart(
    candidate: HaikuTriageCandidate,
    chart_output_dir: str | Path = DEFAULT_HAIKU_CHART_DIR,
) -> HaikuTriageCandidate:
    """Fetch 6M daily OHLCV and generate the one Haiku triage chart."""
    df = fetch_stock_data(candidate.ticker, period="6mo", interval="1d")
    candidate.close = candidate.close if candidate.close is not None else _latest_close(df)
    candidate.rel_volume = (
        candidate.rel_volume
        if candidate.rel_volume is not None
        else _latest_rel_volume(df)
    )
    candidate.chart_path = generate_haiku_triage_chart(
        candidate.ticker,
        df,
        output_dir=str(chart_output_dir),
        trigger_level=candidate.trigger_level,
        stop_reference=candidate.stop_reference,
    )
    return candidate


def _ticker_metadata(candidate: HaikuTriageCandidate) -> dict[str, Any]:
    """Build compact ticker metadata for the Haiku prompt."""
    return {
        "ticker": candidate.ticker,
        "company": candidate.company,
        "sector": candidate.sector,
        "close": candidate.close,
        "rel_volume": candidate.rel_volume,
        "detector_tags": candidate.detector_tags,
        "high_value_tags": candidate.high_value_tags,
        "warning_tags": candidate.warning_tags,
        "setup_family_or_structure": candidate.setup_family,
        "trigger_level_hint": candidate.trigger_level,
        "stop_or_invalidation_hint": candidate.stop_reference,
        "source_kind": candidate.source_kind,
        "source_rank": candidate.source_rank,
        "source_context": {
            "actionability": candidate.source_row.get("Actionability"),
            "structure_type": candidate.source_row.get("StructureType"),
            "blueprint_setup_type": candidate.source_row.get("BlueprintSetupType"),
            "final_pre_ai_score": candidate.source_row.get("FinalPreAIScore"),
            "focus_structure_score": candidate.source_row.get("FocusStructureScore"),
            "today_focus_score": candidate.source_row.get("TodayFocusScore"),
            "detector_count": candidate.source_row.get("detector_count"),
            "detector_confidence": candidate.source_row.get("detector_confidence"),
            "reject_reason": candidate.source_row.get("reject_reason"),
        },
    }


def _build_content_blocks(
    prompts: dict[str, str],
    candidate: HaikuTriageCandidate,
    image_base64: str,
    mime_type: str,
    use_prompt_cache: bool,
) -> list[dict[str, Any]]:
    """Build Anthropic multimodal content blocks for one chart."""
    strategy_block = {
        "type": "text",
        "text": f"<strategy_master>\n{prompts['strategy_master']}\n</strategy_master>",
    }
    triage_block = {
        "type": "text",
        "text": f"<haiku_chart_triage_prompt>\n{prompts['triage_prompt']}\n</haiku_chart_triage_prompt>",
    }
    schema_block = {
        "type": "text",
        "text": f"<output_schema>\n{prompts['output_schema']}\n</output_schema>",
    }
    if use_prompt_cache:
        schema_block["cache_control"] = {"type": "ephemeral"}

    metadata_block = {
        "type": "text",
        "text": (
            "Ticker metadata and detector hints. Use as context only; judge the chart visually.\n"
            + json.dumps(_ticker_metadata(candidate), separators=(",", ":"), default=_json_default)
        ),
    }
    image_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime_type,
            "data": image_base64,
        },
    }
    return [strategy_block, triage_block, schema_block, metadata_block, image_block]


def _call_haiku_chart_triage(
    candidate: HaikuTriageCandidate,
    prompts: dict[str, str],
    model: str,
    use_prompt_cache: bool,
) -> tuple[str, bool]:
    """Call Anthropic for one chart and return raw text plus cache-used flag."""
    image_base64, mime_type = encode_image_base64(candidate.chart_path)
    client = load_anthropic_client()

    def call(cache_enabled: bool) -> str:
        message = client.messages.create(
            model=model,
            max_tokens=DEFAULT_HAIKU_MAX_TOKENS,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": _build_content_blocks(
                        prompts,
                        candidate,
                        image_base64,
                        mime_type,
                        use_prompt_cache=cache_enabled,
                    ),
                }
            ],
        )
        return _extract_response_text(message)

    try:
        return call(use_prompt_cache), use_prompt_cache
    except Exception as exc:
        if use_prompt_cache and "cache" in str(exc).lower():
            return call(False), False
        raise


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    """Normalize model output choice values."""
    text = _clean_text(value, max_chars=80).upper().replace(" ", "_").replace("-", "_")
    if text in allowed:
        return text
    if allowed == ALLOWED_DECISIONS:
        if "KEEP" in text:
            return "KEEP"
        if "MAYBE" in text or "WATCH" in text:
            return "MAYBE"
        if "REJECT" in text or "AVOID" in text:
            return "REJECT"

    return default


def _normalize_setup_type(value: Any) -> str:
    """Normalize setup_type to the requested enum."""
    text = _clean_text(value, max_chars=120).lower().replace(" ", "_").replace("-", "_")
    if text in ALLOWED_SETUP_TYPES:
        return text

    aliases = {
        "big_base": "big_base_near_highs",
        "high_base": "big_base_near_highs",
        "right_side": "right_side_base",
        "right_side_of_base": "right_side_base",
        "inside": "inside_day",
        "bull_flag": "bull_flag_wedge",
        "wedge": "bull_flag_wedge",
        "breakout": "breakout_retest",
        "retest": "breakout_retest",
        "post_gap": "post_gap_flag",
        "gap_flag": "post_gap_flag",
        "accumulation": "possible_accumulation",
        "emerging_reclaim": "possible_accumulation",
        "unr": "undercut_reclaim",
        "undercut": "undercut_reclaim",
        "undercut_and_rally": "undercut_reclaim",
        "failed_breakdown": "failed_breakdown_reclaim",
        "hammer_reversal": "failed_breakdown_reclaim",
        "no_clear_setup": "none",
    }
    if text in aliases:
        return aliases[text]

    for key, replacement in aliases.items():
        if key in text:
            return replacement

    return "none"


def normalize_haiku_response(
    ticker: str, raw_response: str
) -> tuple[dict[str, Any], str]:
    """Parse and normalize one Haiku JSON response."""
    parsed = extract_json_object(raw_response)
    if not isinstance(parsed, dict):
        return (
            {
                "ticker": ticker,
                "decision": "REJECT",
                "confidence": "LOW",
                "setup_type": "none",
                "visual_quality_1_to_10": 1,
                "trigger_level": None,
                "invalidation_level": None,
                "reason_short": "Invalid JSON response.",
                "warning": "parse failed",
            },
            "Claude response was not valid JSON.",
        )

    errors: list[str] = []
    extra_keys = set(parsed) - {
        "ticker",
        "decision",
        "confidence",
        "setup_type",
        "visual_quality_1_to_10",
        "trigger_level",
        "invalidation_level",
        "reason_short",
        "warning",
    }
    if extra_keys:
        errors.append(f"unexpected_keys: {', '.join(sorted(extra_keys))}")

    raw_decision = parsed.get("decision")
    decision = _normalize_choice(raw_decision, ALLOWED_DECISIONS, "REJECT")
    if _clean_text(raw_decision).upper().replace(" ", "_") not in ALLOWED_DECISIONS:
        errors.append("invalid_or_missing_decision")

    raw_confidence = parsed.get("confidence")
    confidence = _normalize_choice(raw_confidence, ALLOWED_CONFIDENCE, "LOW")
    if _clean_text(raw_confidence).upper().replace(" ", "_") not in ALLOWED_CONFIDENCE:
        errors.append("invalid_or_missing_confidence")

    setup_type = _normalize_setup_type(parsed.get("setup_type"))
    if setup_type == "none" and _clean_text(parsed.get("setup_type")).lower() not in {
        "none",
        "",
    }:
        errors.append("invalid_setup_type")

    trigger = _as_float(parsed.get("trigger_level"))
    invalidation = _as_float(parsed.get("invalidation_level"))
    if parsed.get("trigger_level") is not None and trigger is None:
        errors.append("invalid_trigger_level")
    if parsed.get("invalidation_level") is not None and invalidation is None:
        errors.append("invalid_invalidation_level")

    reason = _short_text(parsed.get("reason_short"), max_words=20, max_chars=160)
    warning = _short_text(parsed.get("warning"), max_words=15, max_chars=120)

    normalized = {
        "ticker": _clean_text(parsed.get("ticker") or ticker, max_chars=16).upper(),
        "decision": decision,
        "confidence": confidence,
        "setup_type": setup_type,
        "visual_quality_1_to_10": _as_int_1_to_10(parsed.get("visual_quality_1_to_10")),
        "trigger_level": round(trigger, 4) if trigger is not None else None,
        "invalidation_level": round(invalidation, 4) if invalidation is not None else None,
        "reason_short": reason or "No concise reason returned.",
        "warning": warning,
    }
    return normalized, "; ".join(dict.fromkeys(errors))


def _base_output_row(candidate: HaikuTriageCandidate) -> dict[str, Any]:
    """Build shared output row fields."""
    return {
        "ticker": candidate.ticker,
        "company": candidate.company,
        "sector": candidate.sector,
        "close": candidate.close,
        "rel_volume": candidate.rel_volume,
        "detector_tags": candidate.detector_tags,
        "high_value_tags": candidate.high_value_tags,
        "warning_tags": candidate.warning_tags,
        "chart_path": candidate.chart_path,
    }


def build_dry_run_result(
    candidate: HaikuTriageCandidate, elapsed_seconds: float
) -> dict[str, Any]:
    """Return a result row for dry-run mode without calling Claude."""
    return {
        **_base_output_row(candidate),
        "decision": "DRY_RUN",
        "confidence": "",
        "setup_type": "",
        "visual_quality_1_to_10": None,
        "trigger_level": candidate.trigger_level,
        "invalidation_level": candidate.stop_reference,
        "reason_short": "Dry run: Claude was not called.",
        "warning": "",
        "raw_response": "",
        "parse_error": "",
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def build_failed_result(
    candidate: HaikuTriageCandidate,
    reason: str,
    elapsed_seconds: float,
    raw_response: str = "",
) -> dict[str, Any]:
    """Return an explicit failed triage row."""
    return {
        **_base_output_row(candidate),
        "decision": "REJECT",
        "confidence": "LOW",
        "setup_type": "none",
        "visual_quality_1_to_10": 1,
        "trigger_level": candidate.trigger_level,
        "invalidation_level": candidate.stop_reference,
        "reason_short": _short_text(reason, max_words=20, max_chars=160),
        "warning": "triage failed",
        "raw_response": raw_response,
        "parse_error": reason,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def review_prepared_candidate(
    candidate: HaikuTriageCandidate,
    prompts: dict[str, str],
    model: str,
    use_prompt_cache: bool,
) -> dict[str, Any]:
    """Run one prepared chart through Haiku and return an output row."""
    start = time.perf_counter()
    try:
        raw_response, cache_used = _call_haiku_chart_triage(
            candidate,
            prompts,
            model=model,
            use_prompt_cache=use_prompt_cache,
        )
        if not raw_response:
            return build_failed_result(
                candidate,
                "Claude returned an empty response.",
                time.perf_counter() - start,
                raw_response,
            )
        normalized, parse_error = normalize_haiku_response(candidate.ticker, raw_response)
        return {
            **_base_output_row(candidate),
            **normalized,
            "raw_response": raw_response,
            "parse_error": parse_error,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "_cache_used": cache_used,
        }
    except Exception as exc:
        return build_failed_result(candidate, str(exc), time.perf_counter() - start)


def _pricing_value(*names: str) -> Optional[float]:
    """Read the first configured pricing value from environment variables."""
    load_dotenv()
    for name in names:
        value = os.getenv(name)
        numeric = _as_float(value)
        if numeric is not None and numeric >= 0:
            return numeric

    return None


def estimate_haiku_runtime_and_cost(
    chart_count: int,
    workers: int,
) -> dict[str, Any]:
    """Estimate wall time and rough token/cost range for triage."""
    worker_count = max(1, int(workers or 1))
    sequential_seconds = chart_count * 5
    parallel_seconds = sequential_seconds / worker_count
    input_tokens_min = chart_count * 1500
    input_tokens_max = chart_count * 3000
    output_tokens_min = chart_count * 150
    output_tokens_max = chart_count * 300

    input_price = _pricing_value(
        "ANTHROPIC_HAIKU_INPUT_PRICE_PER_MILLION",
        "HAIKU_INPUT_PRICE_PER_MILLION",
    )
    output_price = _pricing_value(
        "ANTHROPIC_HAIKU_OUTPUT_PRICE_PER_MILLION",
        "HAIKU_OUTPUT_PRICE_PER_MILLION",
    )
    if input_price is not None and output_price is not None:
        min_cost = (input_tokens_min / 1_000_000 * input_price) + (
            output_tokens_min / 1_000_000 * output_price
        )
        max_cost = (input_tokens_max / 1_000_000 * input_price) + (
            output_tokens_max / 1_000_000 * output_price
        )
        cost_text = f"${min_cost:.4f}-${max_cost:.4f}"
    else:
        cost_text = (
            "pricing not configured; set "
            "ANTHROPIC_HAIKU_INPUT_PRICE_PER_MILLION and "
            "ANTHROPIC_HAIKU_OUTPUT_PRICE_PER_MILLION"
        )

    return {
        "chart_count": chart_count,
        "workers": worker_count,
        "sequential_minutes": sequential_seconds / 60,
        "parallel_minutes": parallel_seconds / 60,
        "input_tokens_min": input_tokens_min,
        "input_tokens_max": input_tokens_max,
        "output_tokens_min": output_tokens_min,
        "output_tokens_max": output_tokens_max,
        "estimated_cost": cost_text,
    }


def print_haiku_estimate(estimate: dict[str, Any]) -> None:
    """Print the required pre-call estimate."""
    print("Haiku chart triage estimate:", flush=True)
    print(f"- Charts: {estimate['chart_count']}", flush=True)
    print(f"- Workers: {estimate['workers']}", flush=True)
    print(
        f"- Estimated sequential runtime: {estimate['sequential_minutes']:.2f} minutes "
        "(5 sec/request)",
        flush=True,
    )
    print(
        f"- Estimated parallel runtime: {estimate['parallel_minutes']:.2f} minutes",
        flush=True,
    )
    print(
        "- Estimated tokens: "
        f"{estimate['input_tokens_min']:,}-{estimate['input_tokens_max']:,} input, "
        f"{estimate['output_tokens_min']:,}-{estimate['output_tokens_max']:,} output",
        flush=True,
    )
    print(f"- Estimated cost: {estimate['estimated_cost']}", flush=True)


def _decision_count(results: list[dict[str, Any]], decision: str) -> int:
    """Count output decisions."""
    return sum(1 for result in results if result.get("decision") == decision)


def _group_by_setup(results: list[dict[str, Any]], decision: str) -> dict[str, list[dict[str, Any]]]:
    """Group results by setup type for a decision."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.get("decision") != decision:
            continue
        setup_type = str(result.get("setup_type") or "none")
        grouped.setdefault(setup_type, []).append(result)

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                str(item.get("ticker") or ""),
            ),
        )
    return dict(sorted(grouped.items()))


def _format_level(value: Any) -> str:
    """Format an optional price level for Markdown."""
    numeric = _as_float(value)
    return f"{numeric:.2f}" if numeric is not None else "N/A"


def _format_candidate_line(result: dict[str, Any]) -> str:
    """Format one concise Markdown candidate row."""
    warning = f" | warn: {result.get('warning')}" if result.get("warning") else ""
    return (
        f"- {result.get('ticker')} | Q{result.get('visual_quality_1_to_10')} | "
        f"{result.get('confidence')} | trigger {_format_level(result.get('trigger_level'))} | "
        f"invalid {_format_level(result.get('invalidation_level'))} | "
        f"{result.get('reason_short')}{warning}"
    )


def build_haiku_triage_markdown(
    results: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> str:
    """Build the Markdown report for one Haiku chart triage run."""
    generated_at = metadata["generated_at"]
    elapsed_seconds = metadata.get("actual_elapsed_seconds", 0.0)
    estimate = metadata.get("estimate") or {}
    keep_count = _decision_count(results, "KEEP")
    maybe_count = _decision_count(results, "MAYBE")
    reject_count = _decision_count(results, "REJECT")
    parse_error_count = sum(1 for result in results if result.get("parse_error"))

    lines = [
        "# Haiku Chart Triage Report",
        "",
        f"Run timestamp: {generated_at}",
        f"Model: {metadata.get('model')}",
        f"Prompt caching: {metadata.get('prompt_cache_status')}",
        f"Source: {metadata.get('source_path')}",
        f"Source kind: {metadata.get('source_kind')}",
        f"Review source: {metadata.get('review_source')}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Number reviewed | {len(results)} |",
        f"| KEEP | {keep_count} |",
        f"| MAYBE | {maybe_count} |",
        f"| REJECT | {reject_count} |",
        f"| Parse/error count | {parse_error_count} |",
        f"| Estimated cost | {estimate.get('estimated_cost', 'N/A')} |",
        f"| Actual elapsed time | {elapsed_seconds:.1f}s |",
        "",
    ]

    if metadata.get("dry_run"):
        lines.extend(
            [
                "Dry run was enabled, so Claude Haiku was not called. Charts were generated when data was available.",
                "",
            ]
        )

    for decision in ["KEEP", "MAYBE"]:
        lines.extend([f"## {decision} By Setup Type", ""])
        grouped = _group_by_setup(results, decision)
        if not grouped:
            lines.extend(["- None", ""])
            continue
        for setup_type, items in grouped.items():
            lines.extend([f"### {setup_type}", ""])
            for result in items:
                lines.append(_format_candidate_line(result))
            lines.append("")

    lines.extend(["## REJECT Summary", ""])
    rejected = [result for result in results if result.get("decision") == "REJECT"]
    if not rejected:
        lines.extend(["- None", ""])
    else:
        by_setup: dict[str, int] = {}
        by_warning: dict[str, int] = {}
        for result in rejected:
            by_setup[str(result.get("setup_type") or "none")] = (
                by_setup.get(str(result.get("setup_type") or "none"), 0) + 1
            )
            warning = str(result.get("warning") or "no_warning")
            by_warning[warning] = by_warning.get(warning, 0) + 1
        lines.append(
            "- By setup type: "
            + ", ".join(f"{key}={value}" for key, value in sorted(by_setup.items()))
        )
        lines.append(
            "- Top warnings: "
            + ", ".join(
                f"{key}={value}"
                for key, value in sorted(
                    by_warning.items(), key=lambda item: item[1], reverse=True
                )[:8]
            )
        )
        lines.append("")

    if parse_error_count:
        lines.extend(["", "## Parse Or Runtime Errors", ""])
        for result in [item for item in results if item.get("parse_error")][:25]:
            lines.append(f"- {result.get('ticker')}: {result.get('parse_error')}")

    return "\n".join(lines) + "\n"


def save_haiku_triage_outputs(
    results: list[dict[str, Any]],
    output_dir: str | Path,
    metadata: dict[str, Any],
    include_json: bool = True,
    include_markdown: bool = True,
) -> dict[str, str]:
    """Save CSV and optional JSON/Markdown triage outputs."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = metadata["timestamp"]
    csv_path = output_path / f"haiku_chart_triage_{timestamp}.csv"
    json_path = output_path / f"haiku_chart_triage_{timestamp}.json"
    markdown_path = output_path / f"haiku_chart_triage_report_{timestamp}.md"

    frame = pd.DataFrame(results)
    for column in TRIAGE_CSV_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame.loc[:, TRIAGE_CSV_COLUMNS].to_csv(csv_path, index=False)

    paths = {"csv": str(csv_path)}

    if include_json:
        json_payload = {
            "metadata": metadata,
            "results": results,
        }
        json_path.write_text(
            json.dumps(json_payload, indent=2, default=_json_default),
            encoding="utf-8",
        )
        paths["json"] = str(json_path)

    if include_markdown:
        markdown_path.write_text(
            build_haiku_triage_markdown(results, metadata),
            encoding="utf-8",
        )
        paths["report"] = str(markdown_path)

    return paths


def run_haiku_chart_triage(
    source_path: str | Path,
    output_dir: str = "reports",
    review_source: str = "priority",
    limit: Optional[int] = None,
    workers: int = DEFAULT_HAIKU_WORKERS,
    chart_timeframe: str = DEFAULT_HAIKU_CHART_TIMEFRAME,
    chart_only_6m: bool = True,
    dry_run: bool = False,
    skip_prompt_cache: bool = False,
    prompts_dir: str | Path = DEFAULT_PROMPTS_DIR,
    strategy_source_dir: str | Path = DEFAULT_STRATEGY_SOURCE_DIR,
    chart_output_dir: str | Path = DEFAULT_HAIKU_CHART_DIR,
    model: Optional[str] = None,
    save_json: bool = True,
    save_markdown: bool = True,
) -> dict[str, str]:
    """
    Run Haiku chart triage from a saved detector candidate or focus-audit file.
    """
    start_time = time.perf_counter()
    run_started_at = datetime.now()
    timeframe = str(chart_timeframe or DEFAULT_HAIKU_CHART_TIMEFRAME).upper()
    if timeframe != "6M":
        raise ValueError("Only --haiku-chart-timeframe 6M is currently supported")
    if not chart_only_6m:
        print("Haiku triage currently uses one 6M chart only; ignoring multi-chart mode.", flush=True)

    worker_count = max(1, int(workers or DEFAULT_HAIKU_WORKERS))
    selected_model = model or default_haiku_model()
    prompt_cache_enabled = not skip_prompt_cache
    prompts = load_haiku_prompts(prompts_dir)
    strategy_dir = Path(strategy_source_dir).expanduser()
    chart_run_output_dir = (
        Path(chart_output_dir).expanduser()
        / run_started_at.strftime("%Y-%m-%d_%H%M%S")
    )

    print("Starting Haiku chart triage...", flush=True)
    print("Configuration:", flush=True)
    print(f"- Source path: {source_path}", flush=True)
    print(f"- Review source: {review_source}", flush=True)
    print(f"- Limit: {limit if limit is not None else 'all selected'}", flush=True)
    print(f"- Workers: {worker_count}", flush=True)
    print(f"- Chart timeframe: 6M daily", flush=True)
    print(f"- Dry run: {dry_run}", flush=True)
    print(f"- Model: {selected_model}", flush=True)
    print(f"- Prompt cache: {'disabled' if skip_prompt_cache else 'enabled best-effort'}", flush=True)
    print(f"- Prompts dir: {Path(prompts_dir).expanduser()}", flush=True)
    print(f"- Strategy source dir: {strategy_dir}", flush=True)
    print(f"- Chart output dir: {chart_run_output_dir}", flush=True)
    if dry_run:
        print("Dry run enabled: Claude Haiku will not be called.", flush=True)

    candidates, source_kind = load_haiku_triage_candidates(
        source_path,
        review_source=review_source,
        limit=limit,
    )
    print(f"Haiku triage source kind: {source_kind}", flush=True)
    print(f"Haiku triage candidates selected: {len(candidates)}", flush=True)
    if candidates:
        print(
            "Haiku triage tickers: "
            + ", ".join(candidate.ticker for candidate in candidates),
            flush=True,
        )

    estimate = estimate_haiku_runtime_and_cost(len(candidates), worker_count)
    print_haiku_estimate(estimate)

    prepared_candidates: list[HaikuTriageCandidate] = []
    results: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates, start=1):
        chart_start = time.perf_counter()
        print(f"Preparing Haiku chart {idx}/{len(candidates)} {candidate.ticker}...", flush=True)
        try:
            prepared_candidates.append(
                prepare_candidate_chart(candidate, chart_output_dir=chart_run_output_dir)
            )
        except Exception as exc:
            results.append(
                build_failed_result(
                    candidate,
                    f"chart_generation_failed: {exc}",
                    time.perf_counter() - chart_start,
                )
            )

    if dry_run:
        for candidate in prepared_candidates:
            results.append(build_dry_run_result(candidate, 0.0))
    elif prepared_candidates:
        print(
            f"Starting Haiku calls for {len(prepared_candidates)} charts with {worker_count} workers...",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    review_prepared_candidate,
                    candidate,
                    prompts,
                    selected_model,
                    prompt_cache_enabled,
                ): candidate
                for candidate in prepared_candidates
            }
            completed = 0
            for future in as_completed(futures):
                completed += 1
                candidate = futures[future]
                result = future.result()
                results.append(result)
                print(
                    f"Haiku triage {completed}/{len(prepared_candidates)} "
                    f"{candidate.ticker}: {result.get('decision')} "
                    f"{result.get('setup_type')}",
                    flush=True,
                )

    results.sort(
        key=lambda item: (
            -{"KEEP": 3, "MAYBE": 2, "REJECT": 1, "DRY_RUN": 0}.get(
                str(item.get("decision")), -1
            ),
            str(item.get("ticker") or ""),
        )
    )

    elapsed_seconds = time.perf_counter() - start_time
    generated_at = datetime.now()
    timestamp = generated_at.strftime("%Y-%m-%d_%H%M")
    cache_request_count = sum(1 for result in results if result.get("_cache_used") is True)
    cache_status = (
        "disabled"
        if skip_prompt_cache
        else f"enabled best-effort ({cache_request_count} requests sent with cache_control)"
    )
    metadata = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M"),
        "timestamp": timestamp,
        "model": selected_model,
        "source_path": str(source_path),
        "source_kind": source_kind,
        "review_source": review_source,
        "limit": limit,
        "workers": worker_count,
        "chart_timeframe": "6M",
        "chart_only_6m": True,
        "dry_run": dry_run,
        "prompt_cache_enabled": prompt_cache_enabled,
        "prompt_cache_status": cache_status,
        "prompts_dir": str(Path(prompts_dir).expanduser()),
        "strategy_source_dir": str(strategy_dir),
        "chart_output_dir": str(chart_run_output_dir),
        "estimate": estimate,
        "actual_elapsed_seconds": round(elapsed_seconds, 3),
    }
    for result in results:
        result.pop("_cache_used", None)

    paths = save_haiku_triage_outputs(
        results,
        output_dir=output_dir,
        metadata=metadata,
        include_json=save_json,
        include_markdown=save_markdown,
    )
    print(f"Haiku triage CSV saved: {paths.get('csv')}", flush=True)
    if paths.get("json"):
        print(f"Haiku triage JSON saved: {paths.get('json')}", flush=True)
    if paths.get("report"):
        print(f"Haiku triage report saved: {paths.get('report')}", flush=True)
    print(f"Haiku triage runtime: {elapsed_seconds:.1f}s", flush=True)
    return paths

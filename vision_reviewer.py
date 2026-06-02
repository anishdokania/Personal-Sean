"""
Vision LLM review for chart-quality decision support.

The current implementation uses Anthropic because the project already has an
Anthropic client setup. Image encoding and review prompt construction are kept
provider-neutral enough to support another vision provider later.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from claude_analyzer import DEFAULT_CLAUDE_MODEL, load_anthropic_client


DEFAULT_VISION_MODEL = DEFAULT_CLAUDE_MODEL
REQUIRED_VISION_FIELDS = [
    "symbol",
    "visual_score",
    "focus_list_candidate",
    "visual_setup_type",
    "visual_quality",
    "impulse_present",
    "consolidation_quality",
    "ema_structure",
    "volume_read",
    "extension_risk",
    "reasons",
    "warnings",
    "final_visual_verdict",
]


def _json_default(value: Any) -> Any:
    """Convert uncommon numeric/date objects into JSON-safe values."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def encode_image_base64(image_path: str) -> tuple[str, str]:
    """
    Encode an image file for vision-model input.

    Returns:
        Tuple of (base64_data, mime_type)
    """
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        suffix = path.suffix.lower()
        if suffix == ".png":
            mime_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            mime_type = "image/jpeg"
        elif suffix == ".webp":
            mime_type = "image/webp"
        elif suffix == ".gif":
            mime_type = "image/gif"
        else:
            raise ValueError(f"Unsupported image type for vision review: {path.suffix}")

    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return encoded, mime_type


def build_vision_review_failure(
    symbol: str, reason: str, raw_response: Optional[str] = None
) -> Dict[str, Any]:
    """Return a standardized explicit vision-review failure dictionary."""
    symbol_clean = str(symbol).strip().upper() if symbol else "UNKNOWN"
    reason_text = str(reason).strip() if reason else "unknown vision review failure"

    return {
        "symbol": symbol_clean,
        "vision_review_failed": True,
        "error": reason_text,
        "raw_response": raw_response,
    }


def _vision_schema(symbol_clean: str) -> str:
    """Return the exact required JSON schema for the prompt."""
    return f"""
{{
  "symbol": "{symbol_clean}",
  "visual_score": 0,
  "focus_list_candidate": true,
  "visual_setup_type": "bull_flag | high_base | accumulation_base | breakout_retest | extended_momentum | no_clean_setup",
  "visual_quality": "A | B | C | D | F",
  "impulse_present": true,
  "consolidation_quality": "tight | acceptable | sloppy | none",
  "ema_structure": "strong | acceptable | mixed | weak",
  "volume_read": "supports_setup | neutral | contradicts_setup | unclear",
  "extension_risk": "low | medium | high",
  "trigger_level": "",
  "invalidation_level": "",
  "reasons": [],
  "warnings": [],
  "final_visual_verdict": ""
}}
""".strip()


def _technical_context(technicals: Optional[Dict[str, Any]]) -> str:
    """Return optional compact technical context for the vision prompt."""
    if not technicals:
        return "No structured technical context supplied."

    return json.dumps(technicals, indent=2, default=_json_default)


def build_vision_review_prompt(
    symbol: str, technicals: Optional[Dict[str, Any]] = None
) -> str:
    """
    Build the chart-review prompt for a vision-capable model.
    """
    symbol_clean = str(symbol).strip().upper()

    return f"""
You are a strict visual chart reviewer for a Sean-style / Blueprint-style trading focus list.

Review the attached daily candlestick chart visually. This is educational
decision support only. Do not recommend buying or selling. Your job is to judge
chart quality and whether the setup deserves focus-list attention.

Be strict:
- Reject sloppy, wide, choppy charts.
- Penalize extension away from clean entry areas.
- Reward tight consolidation after a prior impulse.
- Reward clean EMA8/EMA21/EMA50 structure.
- Reward declining volume during pullbacks and stronger volume on impulse or breakout.
- Prefer clean trigger and invalidation levels.
- If the chart is not clean, say so clearly.

Evaluate:
- Does this chart resemble a valid focus-list setup?
- Is there a prior impulse move?
- Is there controlled consolidation after impulse?
- Is there a bull flag, high base, clean compression, or breakout/retest?
- Is price holding EMA8/EMA21/EMA50 structure?
- Is the setup too extended?
- Does volume visually support the move?
- Is there a clean trigger level?
- Is risk/reward visually clean?
- Should this be included in a top focus list?

Scoring guide:
- 90-100: elite focus-list chart
- 75-89: strong focus-list candidate
- 60-74: watchlist only
- 40-59: weak / needs more structure
- 0-39: reject

Return ONLY valid JSON. Do not include Markdown, commentary, code fences, or
extra text outside the JSON object. Use this exact schema:

{_vision_schema(symbol_clean)}

Structured technical context, if useful:
{_technical_context(technicals)}
""".strip()


def _extract_response_text(message: Any) -> str:
    """Extract text from an Anthropic message response."""
    text_parts = []

    for block in getattr(message, "content", []):
        block_text = getattr(block, "text", None)
        if block_text:
            text_parts.append(block_text)

    return "\n".join(text_parts).strip()


def _parse_json_response(symbol: str, raw_response: str) -> Dict[str, Any]:
    """Parse vision JSON, tolerating simple wrapper text or code fences."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return build_vision_review_failure(
                symbol, "Vision response was not valid JSON.", raw_response
            )

        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return build_vision_review_failure(
                symbol, "Vision response was not valid JSON.", raw_response
            )

    if not isinstance(parsed, dict):
        return build_vision_review_failure(
            symbol, "Vision response JSON was not an object.", raw_response
        )

    if "symbol" not in parsed:
        parsed["symbol"] = str(symbol).strip().upper()

    return parsed


def _validate_vision_review(review: Any) -> tuple[bool, Optional[str]]:
    """Validate minimum structure of a vision review response."""
    if not isinstance(review, dict):
        return False, "vision review is not a dictionary"
    if review.get("vision_review_failed") is True:
        return False, str(review.get("error", "vision review failed"))

    for field in REQUIRED_VISION_FIELDS:
        if field not in review:
            return False, f"missing required field: {field}"

    try:
        float(review.get("visual_score"))
    except (TypeError, ValueError):
        return False, "visual_score is not numeric"

    if not str(review.get("final_visual_verdict", "")).strip():
        return False, "final_visual_verdict is empty"

    return True, None


def _build_anthropic_content(image_base64: str, mime_type: str, prompt: str) -> list[Dict[str, Any]]:
    """Build Anthropic-compatible multimodal message content."""
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": image_base64,
            },
        },
        {
            "type": "text",
            "text": prompt,
        },
    ]


def review_chart_with_claude_vision(
    symbol: str,
    image_path: str,
    technicals: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Review a generated chart image with Anthropic vision and return structured JSON.
    """
    symbol_clean = str(symbol).strip().upper()
    selected_model = model or DEFAULT_VISION_MODEL

    try:
        image_base64, mime_type = encode_image_base64(image_path)
        prompt = build_vision_review_prompt(symbol_clean, technicals)
        client = load_anthropic_client()
        message = client.messages.create(
            model=selected_model,
            max_tokens=1200,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": _build_anthropic_content(image_base64, mime_type, prompt),
                }
            ],
        )
    except Exception as exc:
        return build_vision_review_failure(symbol_clean, f"Vision API call failed: {exc}")

    raw_response = _extract_response_text(message)
    if not raw_response:
        return build_vision_review_failure(
            symbol_clean, "Vision model returned an empty response.", raw_response
        )

    parsed = _parse_json_response(symbol_clean, raw_response)
    is_valid, validation_error = _validate_vision_review(parsed)
    if not is_valid:
        return build_vision_review_failure(symbol_clean, validation_error, raw_response)

    return parsed


if __name__ == "__main__":
    from chart_generator import generate_chart_image
    from data_fetcher import fetch_stock_data
    from technical import analyze_stock_technicals

    test_symbol = "MSFT"
    stock_df = fetch_stock_data(test_symbol)
    technical_data = analyze_stock_technicals(test_symbol, stock_df)
    chart_path = generate_chart_image(test_symbol, stock_df)
    result = review_chart_with_claude_vision(test_symbol, chart_path, technical_data)
    print(json.dumps(result, indent=2, default=_json_default))

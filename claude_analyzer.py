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
REQUIRED_AI_FIELDS = [
    "symbol",
    "overall_score",
    "bias",
    "setup_type",
    "setup_quality",
    "final_verdict",
]


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


def _parse_json_response(symbol: str, raw_response: str) -> Dict[str, Any]:
    """
    Parse Claude JSON, with a small fallback for accidental wrapper text.
    """
    cleaned_response = _strip_json_fences(raw_response)

    try:
        return json.loads(cleaned_response)
    except json.JSONDecodeError:
        start = cleaned_response.find("{")
        end = cleaned_response.rfind("}")

        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned_response[start : end + 1])
            except json.JSONDecodeError:
                pass

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

    level_notes = []
    if resistance_above:
        level_notes.append(
            f"Nearest active overhead resistance is {_level_token(resistance_above[0])}."
        )
    if reclaimed_supply:
        nearest_reclaimed_supply = reclaimed_supply[0]
        level_notes.append(
            "Supply zone "
            f"{_level_token(nearest_reclaimed_supply['low'])}-"
            f"{_level_token(nearest_reclaimed_supply['high'])} is below current price "
            "and should be treated as reclaimed supply or possible support on retest, "
            "not overhead resistance."
        )

    if level_notes:
        level_note_text = " ".join(level_notes)
        corrected["final_verdict"] = (
            f"{corrected.get('final_verdict', '').strip()} {level_note_text}"
        ).strip()

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


def _build_retry_prompt(original_prompt: str, validation_error: str) -> str:
    """Build the one-shot repair prompt for invalid Claude JSON."""
    return f"""
Your previous response was invalid or incomplete.
Return ONLY valid JSON matching the required schema.
Do not include markdown.
Do not include commentary.
Do not omit required fields.

The previous issue was: {validation_error}

Use the same stock and same technical data. Return a complete analysis now.

Original request with the same stock and technical data:
{original_prompt}
""".strip()


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

    corrected = _enforce_level_interpretation(technicals, parsed)
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
    technicals_json = json.dumps(technicals, indent=2, default=_json_default)

    return f"""
You are a blueprint-based trading setup evaluator. Your job is to evaluate one
stock using only the structured technical evidence provided below.

This is educational decision support only. Do not tell the user to buy or sell.
Do not claim certainty. If there is no clear setup, say so clearly. Penalize
weak volume, mixed EMA regimes, nearby overhead supply, unclear invalidation,
and overhead resistance.

Important same-day focus-list objective:
- Evaluate whether this stock belongs on TODAY's focus list, not whether it is
  generally bullish sometime this week.
- The user wants names worth watching today for discretionary intraday
  decisions.
- A technically strong stock can still be a poor today-focus candidate if it is
  extended, lacks a same-day trigger, lacks nearby invalidation, has sloppy
  structure, has no volume confirmation, or needs more consolidation.
- Do not recommend buying or selling. Describe decision-support conditions,
  trigger areas, invalidation areas, and reasons to wait or avoid today.

Blueprint concepts to apply:

1. EMA regime:
- 8 EMA represents short-term momentum.
- 21 EMA represents trend health.
- 50 EMA represents bigger trend support/resistance.
- Price above the 8/21/50 EMA stack is the strongest bullish context.
- Price below all three is bearish context.

2. Volume:
- Volume validates price.
- High-volume directional moves can suggest institutional participation.
- Low-volume moves are less trustworthy.
- Wide candle plus low volume is a warning.
- Narrow candle plus high volume can suggest absorption, distribution, or
  stopping volume depending on context.

3. Ignition candle:
- Wide-range candle.
- Opens near one end and closes near the other.
- Relative volume around 200% or higher.
- Indicates possible institutional participation, but it still needs context.

4. Accumulation/distribution:
- Accumulation means stronger volume on up days and OBV rising.
- Distribution means stronger volume on down days and OBV falling.

5. Support/resistance:
- Long ideas should have nearby support or a clear invalidation level.
- Resistance overhead should reduce setup quality or affect target quality.

6. Supply/demand:
- Demand zones can act as support.
- Supply zones can act as resistance only when they are above current price or
  not yet reclaimed.
- Broken zones can flip roles.

7. Level Interpretation Rules:
- First identify current price from the technical data, usually
  ema_regime.close.
- Always compare every support level, resistance level, supply zone, and demand
  zone against current price before drawing conclusions.
- Only levels ABOVE current price can be called overhead resistance, upside
  obstacles, supply overhead, or target barriers.
- Only levels BELOW current price can be called support, downside protection,
  possible stop references, pullback references, or demand references.
- A supply zone BELOW current price must NOT be called overhead resistance.
  Treat it as reclaimed supply, prior supply now below price, possible support
  only if price retests and holds it, or ignore it if it is not relevant.
- A demand zone ABOVE current price must NOT be called immediate support.
  Treat it as not currently relevant until price reaches it.
- Resistance levels BELOW current price must NOT be treated as current
  resistance. They are already reclaimed levels.
- Support levels ABOVE current price must NOT be treated as current support.
- Identify the nearest support below current price, nearest resistance above
  current price, nearest demand zone below current price if any, and nearest
  supply zone above current price if any.
- In warnings, mention nearby overhead supply or resistance only if it is
  actually ABOVE current price.
- If all detected supply zones are below current price, do not penalize upside
  for supply overhead.
- When populating key_levels, include only currently relevant levels: support
  and demand zones BELOW price, resistance and supply zones ABOVE price.
- Do not put resistance levels below current price inside key_levels.resistance;
  those are reclaimed resistance levels.
- Do not put support levels above current price inside key_levels.support; those
  are not current support.
- Do not put supply zones below current price inside key_levels.supply_zones as
  overhead supply. If worth mentioning, describe them as reclaimed supply in an
  assessment or final_verdict.
- Do not put demand zones above current price inside key_levels.demand_zones as
  immediate support. If worth mentioning, describe them as not currently
  relevant until reached.
- Do not say a below-price supply zone limits upside, caps upside, creates
  overhead supply, or acts as a target barrier. A below-price supply zone can
  only be a reclaimed zone, a possible pullback/retest reference, or irrelevant.

8. Setup types:
- high_base_breakout
- accumulation_base
- peg_base
- bull_flag
- bearish_breakdown
- no_clear_setup

9. Same-day focus-list actionability:
- ready_today = clean enough to watch today with a realistic setup path.
- breakout_only = only actionable if it breaks a specific trigger with volume.
- pullback_only = strong but extended; only attractive on controlled pullback or
  retest.
- needs_more_time = constructive but not ready today.
- avoid = no clean same-day trade path, bearish/distribution/sloppy/extended.

Explicitly evaluate:
- Is there a same-day trigger?
- Is this actionable today or does it need more time?
- Is it only valid on breakout?
- Is it only valid on pullback?
- Is current entry late or extended?
- Is there a clear invalidation level?
- Should this be avoided today despite bullish context?

Scoring guide:
- 90-100 = elite setup
- 75-89 = strong setup
- 60-74 = watchlist only
- 40-59 = weak / unclear
- 0-39 = avoid

Return ONLY valid JSON. Do not include Markdown, commentary, code fences, or
extra text outside the JSON object.

Hard JSON filtering rule:
Before returning JSON, filter all detected levels relative to current price.
Use ema_regime.close as current price unless another current close is more
explicitly provided.
- key_levels.support must contain only numeric support levels below current
  price, sorted nearest first when possible.
- key_levels.resistance must contain only numeric resistance levels above
  current price, sorted nearest first when possible.
- key_levels.demand_zones must contain only demand zones below current price.
- key_levels.supply_zones must contain only supply zones above current price.
- If a detected supply zone is below current price, do not put it in
  key_levels.supply_zones. Mention it only as reclaimed supply or a pullback
  retest reference if relevant.
- If a detected resistance level is below current price, do not put it in
  key_levels.resistance. Mention it only as reclaimed resistance if relevant.
- If no supply zones are above current price, key_levels.supply_zones must be
  an empty array and warnings must not penalize the setup for overhead supply.

Required JSON schema:
{{
  "symbol": "{symbol_clean}",
  "overall_score": 0,
  "bias": "bullish | bearish | neutral",
  "setup_type": "high_base_breakout | accumulation_base | peg_base | bull_flag | bearish_breakdown | no_clear_setup",
  "setup_quality": "A | B | C | D | F",
  "actionability": "ready_today | breakout_only | pullback_only | needs_more_time | avoid",
  "trigger_level": "",
  "invalidation_level": "",
  "do_not_chase_above": "",
  "same_day_plan": "",
  "why_today": [],
  "ema_assessment": "",
  "volume_assessment": "",
  "ignition_assessment": "",
  "accumulation_distribution_assessment": "",
  "key_levels": {{
    "support": [],
    "resistance": [],
    "demand_zones": [],
    "supply_zones": []
  }},
  "entry_idea": "",
  "stop_idea": "",
  "target_idea": "",
  "warnings": [],
  "disqualifiers": [],
  "final_verdict": ""
}}

Use numeric overall_score as an integer from 0 to 100. Use arrays for warnings
and disqualifiers. Use the provided support/resistance and supply/demand data
inside key_levels. If entry, stop, or target is unclear, say "No clear idea from
the provided data." Do not invent earnings facts for peg_base; only use that
setup type if the supplied technicals clearly support a post-gap base context.

Symbol:
{symbol_clean}

Structured technical data:
{technicals_json}
""".strip()


def analyze_with_claude(
    symbol: str,
    technicals: Dict[str, Any],
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = 1600,
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
    retry_prompt = _build_retry_prompt(prompt, validation_error)

    try:
        retry_raw_response = _call_claude(client, retry_prompt, model, max_tokens)
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

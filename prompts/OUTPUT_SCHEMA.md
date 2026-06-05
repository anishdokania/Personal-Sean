Return exactly one JSON object matching this schema:

{
  "ticker": "AAPL",
  "decision": "KEEP | MAYBE | REJECT",
  "confidence": "LOW | MEDIUM | HIGH",
  "setup_type": "big_base_near_highs | right_side_base | inside_day | bull_flag_wedge | breakout_retest | post_gap_flag | possible_accumulation | undercut_reclaim | failed_breakdown_reclaim | none",
  "visual_quality_1_to_10": 1,
  "trigger_level": null,
  "invalidation_level": null,
  "reason_short": "max 20 words",
  "warning": "max 15 words or empty string"
}

Do not add extra keys.

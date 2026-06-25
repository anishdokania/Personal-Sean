Return exactly one raw JSON object matching this schema.
No markdown. No code fences. No extra keys.

{
  "ticker": "AAPL",
  "decision": "KEEP | MAYBE | REJECT",
  "confidence": "LOW | MEDIUM | HIGH",
  "setup_type": "ema8_snipe_setup | undercut_reclaim | breakout_retest | big_base_near_highs | right_side_base | inside_day | bull_flag_wedge | post_gap_flag | failed_breakdown_reclaim | possible_accumulation | none",
  "entry_model": "ema8_snipe | undercut_reclaim | breakout | breakout_retest | inside_day_breakout | no_trade",
  "entry_status": "active | near_active | forming | none",
  "visual_quality_1_to_10": 1,
  "trigger_level": null,
  "invalidation_level": null,
  "trigger_distance_pct": null,
  "invalidation_distance_pct": null,
  "distance_to_8ema_pct": null,
  "reason_short": "max 20 words",
  "warning": "max 15 words or empty string"
}

Allowed values:
- decision: KEEP, MAYBE, REJECT
- confidence: LOW, MEDIUM, HIGH
- setup_type: exactly one listed schema value
- entry_model: exactly one listed schema value
- entry_status: active, near_active, forming, none
- visual_quality_1_to_10: integer 1-10
- trigger_level: number or null
- invalidation_level: number or null
- trigger_distance_pct: number or null
- invalidation_distance_pct: number or null
- distance_to_8ema_pct: number or null
- reason_short: maximum 20 words
- warning: maximum 15 words or empty string

Decision constraints:
- KEEP is allowed only when entry_status is active or near_active.
- KEEP requires a nearby usable entry and nearby invalidation.
- KEEP should normally have visual_quality_1_to_10 >= 8.
- MAYBE is for good charts that are not actionable yet, need confirmation, have slightly wide risk, or need one more day.
- REJECT is for no entry model, sloppy/choppy charts, stale setups, excessive extension, wide risk, failed breakout, or unclear levels.
- If entry_status is forming, decision should usually be MAYBE.
- If entry_status is none, decision should usually be REJECT.
- If both trigger_level and invalidation_level are null, decision should be REJECT unless the chart is an obvious forming MAYBE.
- Do not mark KEEP only because the chart is clean. KEEP means active/near-active daily entry.

Setup selection:
- ema8_snipe_setup: trending stock pulling into/touching/slightly undercutting/reclaiming rising 8 EMA with tight risk.
- undercut_reclaim: clear undercut of 8 EMA, PDL, prior low, or support, followed by reclaim/rally.
- breakout_retest: breakout level being cleanly retested or held as support.
- big_base_near_highs: larger base near highs with compression below resistance.
- right_side_base: right side rebuilding after decline, but only KEEP if entry is active/near-active.
- inside_day: inside day in useful location with tight high/low trigger-risk.
- bull_flag_wedge: tight flag/wedge after impulse, trigger nearby.
- post_gap_flag: gap-up followed by tight digestion, not chase.
- failed_breakdown_reclaim: support breakdown reclaimed with tight failure level.
- possible_accumulation: accumulation near lows/reclaim area, usually MAYBE unless entry is active.
- none: no clean actionable setup.

# STRATEGY_MASTER_REFINED.md
## Sean / The Options Cartel Visual Triage Framework

This document is the compact strategy memory for the LLM. It should be loaded with every visual chart triage call. The model does **not** self-learn between API calls, so this document must contain the reusable judgment rules, examples distilled into principles, and strict decision boundaries.

---

## 1. Core Objective

The system is not trying to predict every stock. It is trying to reduce a scanned universe into a small focus list of charts that visually match the Sean / The Options Cartel style:

- strong stock, preferably in a strong sector;
- clear daily trend or reclaim structure;
- base, compression, flag, retest, reclaim, inside day, UnR/undercut-and-rally, or accumulation look;
- nearby actionable trigger;
- nearby invalidation level;
- volume supporting the move;
- chart not extended or sloppy.

The model should act like a strict visual reviewer. It should reject most charts.

---

## 2. Non-Learning API Constraint

The LLM does not remember past examples after the API call ends. Do not rely on “learning over time.” Every call must include:

1. this strategy document or a compact version of it;
2. the triage prompt;
3. the chart image;
4. any detector tags or metadata as weak hints only;
5. the JSON schema.

Focus list examples should be converted into reusable rules. Do not require the model to repeatedly compare against every historical example. The examples teach the following visual preference:

- clean bases near highs are preferred;
- constructive right-side charts are preferred;
- compression after a strong move is preferred;
- controlled pullbacks into rising EMAs are preferred;
- breakout retests are preferred over extended breakouts;
- gap-up names are only attractive after tight post-gap digestion;
- UnR structures are priority daily entries: undercut the 8 EMA/PDL/key level, reclaim it, then rally with tight invalidation;
- failed breakdown/reclaim structures can be attractive when price reclaims a key level with volume;
- loose, choppy, far-from-trigger, or overextended charts are rejected.

---

## 3. Market and Trend Context

Use market trend as a filter. Do not overrule chart weakness just because a detector fires.

### Bullish regime
Long setups are preferred when:
- price is above or reclaiming the 8/21/50 EMAs;
- EMAs are stacked or beginning to curl higher;
- price is making higher highs and higher lows;
- pullbacks hold above the 21 or 50 EMA;
- the broader market is above key EMAs.

### Bearish or weak regime
Long setups are rejected or downgraded when:
- price is below the 50 EMA with no reclaim;
- price is making lower highs and lower lows;
- rallies are rejected at declining EMAs;
- the broader market is below key EMAs.

### EMA interpretation
- Above 8 EMA: strong momentum, but reject if too extended.
- Between 8 and 21 EMA: trend intact, acceptable digestion area.
- Between 21 and 50 EMA: possible dip/retest zone, needs evidence of buyers.
- Below 50 EMA: avoid longs unless there is a clean failed breakdown reclaim or base reclaim.

---

## 4. Volume Principles

Volume is the main confirmation tool. Price without volume is less trustworthy.

### Valid action
- Wide spread up candle + high relative volume = strong buying interest.
- Wide spread down candle + high relative volume = strong selling interest.
- Tight candles + declining/low volume after a move = constructive digestion.
- Breakout candle with noticeably higher volume than nearby bars = valid breakout attempt.

### Bullish accumulation look
Prefer charts showing:
- high volume at the low of a base or reclaim area;
- lower volume during ordinary pullbacks;
- higher volume when price moves up from lows;
- no major high-volume sell candle after the initial move;
- volume dries up as price tightens.

### Bearish distribution / warning look
Reject or downgrade charts showing:
- high-volume rejection at highs;
- failed breakout above prior highs;
- upper wick into resistance;
- high-volume red candles during pullbacks;
- narrow spread with high volume near highs, suggesting selling into strength;
- wide spread on low volume, suggesting lack of institutional participation.

---

## 5. Primary Setup Types

Only classify a chart into one of the schema setup types. If the chart does not clearly fit, use `none`.

### `big_base_near_highs`
A larger range or base near recent highs. Preferred when price has spent multiple weeks/months consolidating below resistance and is now tightening near the top of the base.

KEEP only if:
- resistance/trigger is obvious;
- support/invalidation is obvious;
- base is not too wide for reasonable risk;
- price is near the top third of the base;
- volume is quiet during the base or rising on right-side progress.

MAYBE if the base is real but price is still mid-range, support is too far, or trigger is not close.

REJECT if the range is chaotic, price is near the bottom, or no clean trigger exists.

### `right_side_base`
A stock has sold off or based, then rebuilt the right side with higher lows and improving volume.

KEEP only if:
- price is approaching a clear reclaim/breakout level;
- EMAs are flattening or curling upward;
- pullbacks are controlled;
- volume supports upside movement.

MAYBE if the right side is forming but still needs one more reclaim or tighter consolidation.

REJECT if the chart is still in a downtrend or has no clear right-side structure.

### `inside_day`
A tight inside candle after a constructive move, often useful as a clean trigger setup.

KEEP only if:
- inside day forms near highs, above support, or after a clean reclaim;
- trigger is the inside day high;
- invalidation is the inside day low or nearby support;
- volume is quiet/tight, not distribution.

MAYBE if location is acceptable but trend or volume is mixed.

REJECT if inside day occurs in the middle of chop or after an extended move with no support.

### `bull_flag_wedge`
A strong prior move followed by controlled downward/sideways compression.

KEEP only if:
- flag/wedge is tight and obvious;
- pullback volume is lower than impulse volume;
- price is holding above key EMAs or support;
- breakout trigger is close.

MAYBE if flag is forming but not tight enough or needs one more day.

REJECT if the pullback is deep, high-volume, or has broken the flag support.

### `breakout_retest`
Price broke a clear level, then returned to test it from the other side.

KEEP only if:
- original breakout was supported by volume;
- retest holds the breakout level cleanly;
- risk is tight below the retest level;
- price is not already far above the retest.

MAYBE if retest is happening but has not held yet.

REJECT if retest failed, price closed back inside the old range, or breakout was only a wick.

### `post_gap_flag`
A gap-up or earnings-type move followed by tight digestion.

KEEP only if:
- the gap holds;
- price forms a tight flag/base after the gap;
- volume decreases during digestion;
- trigger and invalidation are nearby.

MAYBE if the gap is strong but digestion is incomplete.

REJECT if the chart is chasing the gap, has no base, or is extended far from support.

### `possible_accumulation`
A base or range showing evidence of institutional buying.

KEEP only if:
- high volume appears near lows or reclaim points;
- pullbacks are lower volume;
- price is forming higher lows or reclaiming a key level;
- the chart is near an actionable trigger.

MAYBE if accumulation signs exist but the trigger is not clean.

REJECT if volume is random, selling volume dominates, or price remains weak.


### `undercut_reclaim` / UnR Entry Model
UnR means **undercut and rally**. This is a priority daily entry model from the focus-list examples. The stock briefly undercuts the 8 EMA, previous day low (PDL), prior pivot low, or another obvious support level, then reclaims that level and rallies. The setup matters because it can create a same-day actionable entry with tight invalidation.

What the model is looking for visually:
- stock is already in a constructive trend, base, flag, right-side build, or strong sector context;
- price flushes below the 8 EMA, PDL, prior low, or obvious support;
- sellers fail to hold price below that level;
- price reclaims PDL / 8 EMA / key support and starts moving back up;
- reclaim candle shows buyer response, preferably with relative volume;
- invalidation is tight: loss of reclaimed level, day low, or undercut low.

KEEP only if:
- the undercut is clean and not part of a broad breakdown;
- reclaim is visible and close to the current price;
- trigger is the reclaim level, PDL reclaim, 8 EMA reclaim, or break over the reclaim candle high;
- invalidation is tight below the undercut low or reclaimed level;
- chart has broader constructive structure, not random chop.

MAYBE if the undercut happened but reclaim is incomplete, volume is unclear, or the stock needs one more candle/day above the reclaimed level.

REJECT if price is still below the 8 EMA/PDL/key level, the undercut becomes a breakdown, the stock is in a clear downtrend, or reclaim is only a wick.

### `failed_breakdown_reclaim`
Price broke below support, trapped sellers, then reclaimed the level.

KEEP only if:
- reclaim is clean and visible;
- reclaim occurs on strong volume or with strong follow-through;
- reclaimed level is now nearby support;
- invalidation is tight below the reclaim level.

MAYBE if reclaim started but confirmation is incomplete.

REJECT if price failed to reclaim, reclaimed only by wick, or remains below declining EMAs.

### `none`
Use when no valid setup family is visible. Most rejected charts should use `none`.

---

## 6. Daily Actionability Filter

This system runs every trading day, so the model must judge whether the chart is useful **for the day it is scanned**, not merely whether the stock is generally strong.

A chart is more valuable today when it has one of two active entry paths:

1. **UnR / undercut-and-rally path**: price undercuts the 8 EMA, PDL, prior low, or key support, then reclaims that level and gives a tight risk point.
2. **Breakout path**: price is compressing under a clear resistance/base/flag/inside-day high and can trigger through that level with volume.

Daily KEEP candidates should usually have:
- a trigger close enough to matter today or next session;
- a tight invalidation level;
- a reason to enter on reclaim or breakout, not just “nice chart”;
- no major chase condition.

Downgrade to MAYBE when the setup is attractive but the entry is not active yet. Reject if the stock is good long-term but has no useful daily entry model.

---

## 8. Keep / Maybe / Reject Standards

### KEEP
Use KEEP rarely. A KEEP chart must be visually clean and actionable.

Required characteristics:
- clear setup type from the schema;
- clear trigger level nearby;
- clear invalidation level nearby;
- trend is supportive or reclaim structure is convincing;
- volume supports the setup;
- price is not too extended;
- chart is readable without forcing lines;
- worth final human review or focus list consideration.

KEEP examples include:
- tight bull flag after ignition candle;
- big base near highs with compression under resistance;
- breakout retest holding former resistance as support;
- tight inside day above rising EMAs;
- gap-up followed by tight flag, not immediate chase;
- failed breakdown reclaim with clear support below.

### MAYBE
Use MAYBE for charts that are interesting but not ready.

Common MAYBE cases:
- setup forming but trigger not close;
- needs one more day of tight action;
- needs retest confirmation;
- needs reclaim above resistance or EMA;
- volume is acceptable but not decisive;
- chart is constructive but risk is slightly wide;
- price is near the right area but not yet actionable.

MAYBE is a watchlist candidate, not a focus-list-quality setup.

### REJECT
Use REJECT aggressively.

Reject when:
- no clear trigger;
- no clean support/invalidation;
- price is in no-man’s-land;
- chart is too extended from support/EMAs;
- breakout already happened and there is no retest/base;
- large upper wick or failed breakout appears;
- high-volume selling appears near highs;
- volume does not validate the move;
- price is below 50 EMA with no reclaim;
- chart is loose, wide, random, or sloppy;
- stop would be too wide;
- detector tags are present but visual setup is weak.

When uncertain, choose REJECT.

---

## 8. Trigger and Invalidation Rules

The trigger and invalidation must be visual, nearby, and practical.

### Trigger level
Use the most obvious nearby level:
- resistance high of base;
- flag/wedge breakout line;
- inside day high;
- reclaim level;
- prior breakout/retest level;
- post-gap flag high.

Return `null` if no clean trigger exists.

### Invalidation level
Use the most obvious nearby failure level:
- base support;
- flag/wedge low;
- inside day low;
- retest low;
- reclaim level failure;
- demand/support zone low;
- key EMA area if visually obvious.

Return `null` if no clean invalidation exists.

A setup with no trigger and no invalidation is usually REJECT.

---

## 8. Visual Quality Scale

Score the visual quality from 1 to 10.

- 1-3: reject; messy, weak, no setup.
- 4-5: low-quality maybe/reject; some structure but not actionable.
- 6: borderline maybe; interesting but needs work.
- 7: good maybe or weak keep; mostly clean.
- 8: strong keep; clean trigger, clean risk, supportive volume.
- 9: excellent keep; textbook setup.
- 10: rare; extremely obvious, clean, tight, high-quality focus-list chart.

KEEP should usually be 7-10. MAYBE should usually be 5-7. REJECT should usually be 1-5.

---

## 9. Detector Tags and Metadata

Detector tags are hints only. Never let a detector decide the output.

Downgrade or reject if:
- detector says flag/base but the chart is visually loose;
- detector says breakout but price is already extended;
- detector says accumulation but volume looks random;
- detector says reclaim but price is still below major resistance;
- detector says inside day but location is poor.

The chart image is the primary source of truth.

---

## 10. Output Behavior

The model must output strict JSON only and match the schema exactly. No markdown, no prose, no extra keys.

Keep `reason_short` under 20 words. It should explain the visual reason, not provide trading advice.

Good reasons:
- "Tight flag above rising EMAs with clear nearby breakout."
- "Base near highs, but needs reclaim confirmation."
- "Extended breakout with no nearby support or retest."
- "Failed breakout and heavy upper wick near resistance."

Warnings should be short and only note the main issue:
- "Too extended"
- "Needs retest"
- "Wide stop"
- "Weak volume"
- "Upper wick"
- "No clear trigger"

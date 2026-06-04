# Strategy Handoff

Last updated: 2026-06-03

## Current Build Goal

The scanner should use deterministic filters to narrow the broad tradable
universe into a small focus list that resembles The Options Cartel Blueprint
examples before any full AI analysis is spent.

ChatGPT is expected to act as the strategy-review brain. Codex is expected to
implement measurable scanner changes and preserve enough diagnostics for
ChatGPT to decide the next iteration.

## Current Pipeline

1. Load the selected universe.
2. Apply the primary hard universe gate.
3. Score hard-gate survivors through technical, Today Focus, Focus Structure,
   Blueprint Fit, and sector-alignment layers.
4. Apply the focus-quality gate.
5. Save a focus-gate audit CSV.
6. Send only passing candidates to Claude analysis, unless running dry-run.

## Primary Hard Gate

The primary gate keeps only symbols with:

- price above 5
- price above 21 SMA
- price above 50 SMA
- market cap above 300M
- ATR14 above 1.5
- 20-day average volume above 1M

This gate removes symbols that are unlikely to be usable options-trading
candidates before deeper chart work starts.

## New Strategy Improvements

### Blueprint Fit Layer

`focus_structure.py` now calculates `blueprint_fit_score`,
`blueprint_fit_pass`, and `blueprint_fit_fail_reasons`.

The fit layer checks whether the chart has the ingredients emphasized by the
blueprint examples:

- named blueprint setup, not only generic watchlist structure
- recent bullish impulse or valid accumulation-base location
- controlled digestion where required
- compression or a tight base
- bullish volume behavior or volume dry-up
- no hostile red-volume expansion
- EMA structure holding
- nearby trigger or retest reference
- nearby invalidation
- no high extension without a clean base

The focus-quality gate now rejects candidates with `BlueprintFitScore < 65` or
hard blueprint-fit failures.

### Sector Relative Strength

`main.py` now calculates:

- `stock_perf_1w`
- `stock_perf_1m`
- `stock_perf_3m`
- `relative_strength_1m`
- `relative_strength_3m`

`SectorAlignmentScore` now includes sector rank, sector momentum, and stock
outperformance or underperformance versus the sector ETF.

The focus-quality gate rejects known weak sector alignment below 45.

### Final Pre-AI Score

The final deterministic ranking score is now:

```text
0.15 * TechnicalPreAIScore
+ 0.25 * TodayFocusScore
+ 0.25 * FocusStructureScore
+ 0.20 * BlueprintFitScore
+ 0.15 * SectorAlignmentScore
```

This shifts ranking toward blueprint fidelity and leadership instead of raw
technical activity.

## Audit Fields To Review

The focus-gate audit CSV now includes the most important review fields:

- `BlueprintFitScore`
- `BlueprintFitPass`
- `BlueprintFitFailReasons`
- `stock_perf_1w`, `stock_perf_1m`, `stock_perf_3m`
- `relative_strength_1m`, `relative_strength_3m`
- `GateFailureReasons`
- `StructureType`
- `BlueprintSetupType`
- `FocusStructureScore`
- `TodayFocusScore`
- `SectorAlignmentScore`
- `FinalPreAIScore`

ChatGPT should use these fields to identify false positives, false negatives,
and threshold adjustments.

## Recommended Next Decisions For ChatGPT

1. Review the latest audit CSV and decide whether `BlueprintFitScore >= 65` is
   too strict, too loose, or reasonable.
2. Review false negatives from known good focus-list examples and identify
   which `BlueprintFitFailReasons` are over-penalizing.
3. Decide whether the next upgrade should be a labeled validation set or a
   chart-image review layer.
4. Decide whether accumulation-base setups should be allowed to pass with less
   recent impulse evidence than high-base/flag setups.

## Known Limitations

- The system is still using deterministic OHLCV proxies, not true human visual
  pattern recognition.
- Earnings events are not verified for PEG setups; `detect_power_gap_proxy()`
  only detects the chart behavior.
- Volume profile shelves are not yet implemented. Current support/demand zones
  are proxy levels derived from price and volume candles.
- The `.pages` focus-list examples need explicit labels before we can measure
  precision and recall against the human examples.

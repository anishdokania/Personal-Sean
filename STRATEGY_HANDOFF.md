# Strategy Handoff

Last updated: 2026-07-10

## 2026-07-10 — UnR Edge Study (read this first)

The backtest engine was rebuilt around an honesty model (exact / hourly-resolved
/ bounded outcomes; see `backtest/README.md`) and run over 106 high-ADR movers,
2021-01-01 → 2026-07-09. Full numbers: `backtest/results/EDGE_REPORT.md`.

**Finding 1 — the entry approximation was hiding the edge.** A next-day limit
back at the reclaimed level is adversely selected (the best reclaims never pull
back; the ones that do are often failing). Measured expectancy of that model is
**negative** (-0.21R on the assumption-free subset). This, plus the old
engine's stop-first scoring of every ambiguous bar, is why the system "showed
no edge".

**Finding 2 — the edge is measurable when the entry matches how UnR is actually
traded.** Entering at the UnR bar's own close (live: ~15:30-15:45 ET scan +
market-on-close; 86.9% of signals are already detectable at 15:30) and exiting
on a daily close below the 8 EMA (tight stop under the undercut low until then):

| Model | Trades | Expectancy | 95% CI | PF |
|---|---|---|---|---|
| Close entry → 8EMA trail (F3) | 1,362 | **+0.266R** | [+0.08, +0.50] | 1.32 |
| F3 + risk-on + no-chase + ADR band (G5) | 711 | **+0.529R** | [+0.20, +0.95] | 1.67 |

Every trade in both rows resolves exactly on daily bars — zero assumed
outcomes. G5 is positive in both split-sample halves (2021-23: +0.15R;
2024-26: +0.74R) and in the hourly-measured window (+0.53R over 396 trades).

**The tradable recipe (all three filters have monotonic dose-response):**

1. UnR signal: constructive regime (close > 50 EMA, 21 EMA rising), bar closes
   in upper 60% of range, low undercut PDL / 8 EMA / 21 EMA and price reclaimed it.
2. Only in **risk-on tape** (SPY above its 50-day SMA). Risk-off expectancy ≈ 0.
3. **No chasing**: close no more than 0.5 ADR above the reclaimed level
   (<0.25 ADR is the best bucket at +0.48R).
4. **ADR 5-10%** names. Below 5% the range doesn't pay; above 10% the tight
   stop gets gapped through (10%+ bucket is negative).
5. Enter near the close of the signal bar; stop = undercut low − 0.05 ADR.
6. Exit: hard stop until a daily close below the 8 EMA; no profit target
   (the edge lives in the +3R to +10R right tail; capping it kills it).
7. Time stop after 15 sessions.

**Honest caveats:** universe is curated (not survivorship-free); expectancy is
era-dependent (2022-style tape ≈ breakeven with filters); avg win is tail-driven
(28% win rate — expect losing streaks; 1% risk per trade sizing produced -37%
max DD in simulation).

**Independent check available:** `tools/pine/unr_snipe_strategy.pine` runs the
same rules on TradingView Premium intraday history.

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

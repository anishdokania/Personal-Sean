# UnR Backtest & Edge Study

Backtests the **undercut-and-reclaim (UnR) snipe** — the primary entry family
of the blueprint strategy — against the high-ADR movers universe, and reports
where (and whether) the edge is measurable, without faking precision the data
does not have.

## Why this design

The dev sandbox has no route to market-data hosts, and daily OHLC alone cannot
order events inside a bar. Both problems are solved structurally:

1. **Data comes through git.** `.github/workflows/backtest-data.yml` runs
   `backtest/fetch_data.py` on a GitHub Actions runner (open internet) and
   commits a reproducible snapshot to `backtest/marketdata/`:
   - daily OHLCV 2021 → today for ~120 movers + SPY/QQQ + sector ETFs,
   - hourly OHLCV for the trailing ~2 years (Yahoo's 1h history limit).
   Push a change to `backtest/marketdata/.trigger` (or dispatch the workflow)
   to refresh, then `git pull`.

2. **Intrabar ambiguity is resolved, bracketed, and labeled — never guessed.**
   - Most daily bars are **exact**: gap exits print at the open, and on the
     entry day the stop sits strictly *below* the limit entry, so a daily low
     at/under the stop proves both the fill and the stop-out.
   - A bar that touches both stop and target is resolved from that day's
     **hourly bars** when coverage exists.
   - Whatever remains is scored under both a **pessimistic** and an
     **optimistic** bound; every trade carries its provenance in
     `Trade.resolution` (`exact` / `hourly` / `pessimistic` / `optimistic`), so
     reports separate measured edge from assumed edge.

## Models

Entry (`--entry-model`):

| model | what it approximates | fill ambiguity |
|---|---|---|
| `limit_reclaim` | next-day limit back at the reclaimed level | adversely selected: the best reclaims never pull back |
| `next_open` | confirmation entry at the next open | none, but pays the overnight gap |
| `signal_close` | buying the reclaim intraday (late-session scan + MOC order) | none — close entries are exact |

Exit (`--exit-model`):

| model | mechanics | ambiguity |
|---|---|---|
| `swing_target` | intrabar limit at nearest overhead swing high + hard stop | stop-vs-target ordering on wide bars |
| `trail_ema8` | hard stop + exit on daily close below the 8 EMA | **none — fully exact on daily bars** |
| `hybrid` | partial at the swing target, stop → breakeven, trail the rest | only on the partial day |

## Running

```bash
python -m backtest.run --start 2021-01-01 --end 2026-07-09          # one variant
python -m backtest.run --entry-model signal_close --exit-model trail_ema8
python -m backtest.edge_report --start 2021-01-01 --end 2026-07-09  # full study
```

`edge_report` writes `backtest/output/EDGE_REPORT.md` with the variant matrix
(pessimistic/optimistic brackets + hourly-resolved), the measured-only subset,
slices (setup type, ADR%, planned RR, chase distance, year, SPY regime, symbol
concentration), and a 2021-23 vs 2024-26 split-sample check on the filter set.

## TradingView cross-validation

`tools/pine/unr_snipe_strategy.pine` is the same rule set in Pine v6. Load it
on TradingView Premium (20k-bar lookback) on a 30m/65m chart to test the true
intraday snipe against intraday history this stack cannot reach — an
independent check on the same strategy.

## Honesty caveats (read before trusting any number)

- The universe is a curated list of the style's kind of names; it is **not
  survivorship-free**. Delisted movers (e.g. BITF, SAVA in this snapshot) drop
  out silently. Treat absolute expectancy as upper-bounded by this.
- Daily bars are dividend+split adjusted (yfinance `auto_adjust`); hourly bars
  are split-adjusted. The movers universe pays almost no dividends, so the
  basis mismatch is negligible.
- `signal_close` assumes the setup is detectable minutes before the close
  (true for a ~15:45 ET scan; the signal bar's indicators are then ~99%
  formed) and fills at the close with slippage.
- Earnings dates are not modeled; a UnR the day before earnings is taken like
  any other. The live system can veto these manually.

# Validation Notes — 2026-07-10 Edge Study

## Close-entry detectability (live-tradability of `signal_close`)

Re-evaluated every hourly-covered signal (2024-07-11 → 2026-07-09) using the
15:30 ET price in place of the close (`python -m backtest.validate_close_entry`):

- Signals checked: **1,483**
- Already detectable at 15:30 ET: **1,288 (86.9%)**
- Lost-signal reasons: close_position 110, no_unr 96, regime 20
- Drift 15:30 → close (ADRs): mean **+0.021**, median +0.014, p10 -0.136, p90 +0.182
  (positive = the backtested close fill is *worse* than the scan price, so the
  backtest is conservative on entry price)

## Look-ahead spot audit

Three randomly sampled G5 trades (CRDO 2025-08-14, HOOD 2024-12-17,
NTLA 2023-05-22) recomputed from raw daily bars: regime, close-position, UnR
reference, ADR band, chase, entry (close + 5 bps), stop (undercut − 0.05 ADR),
and exit path all match the engine's recorded values exactly.

## Cross-source data check

NVDA 2021-01-04 daily bar: yfinance snapshot vs Robinhood historicals differ by
a uniform 0.286% across O/H/L/C (dividend adjustment; geometry-preserving) and
volume matches to rounding.

## Provenance discipline

The recommended variants (F3/G5) contain **zero assumed outcomes** — every
trade resolves exactly on daily bars (close entries + close-based trail + gap
logic + the stop-below-entry lemma). Ambiguity bracketing only affects the
swing-target variants, where residual unknowns are labeled and bounded.

# Trading Blueprint Daily Focus List

Generated: 2026-06-03 18:01

## Summary

No high-quality same-day focus-list setups passed the filters today.

| Metric | Count |
|---|---:|
| Symbols loaded for hard gate | 10 |
| Hard gate survivors | 5 |
| Hard gate rejected | 5 |
| Candidates technically scored | 5 |
| Candidates evaluated by quality gates | 5 |
| Qualified after focus gates | 0 |
| Selected for Claude | 0 |

## Market Context

Bias: risk_on | Score: 91.7

| Symbol | Name | Regime | Close | EMA8 | EMA21 | EMA50 | Score |
|---|---|---|---:|---:|---:|---:|---:|
| SPY | S&P 500 | strong_bullish | 754.24 | 753.20 | 742.10 | 721.35 | 100 |
| QQQ | Nasdaq 100 | strong_bullish | 744.21 | 735.57 | 715.05 | 679.90 | 100 |
| IWM | Russell 2000 | bullish | 287.67 | 288.33 | 284.12 | 276.09 | 75 |

Warnings:
- None

## Sector Leadership

Top sectors:
1. Technology / XLK - score 30.3
2. Energy / XLE - score 8.7
3. Materials / XLB - score 4.1

Weak sectors:
1. Consumer Staples / XLP - score -2.5
2. Communication Services / XLC - score -2.7
3. Utilities / XLU - score -4.1

| Rank | Sector | ETF | Score | 1W | 1M | 3M | 6M | 1Y |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Technology | XLK | 30.3 | 6.4% | 21.1% | 40.3% | 37.1% | 68.5% |
| 2 | Energy | XLE | 8.7 | 3.0% | -1.1% | 4.5% | 28.6% | 42.2% |
| 3 | Materials | XLB | 4.1 | 0.9% | 1.9% | -0.6% | 15.8% | 18.9% |
| 4 | Industrials | XLI | 3.9 | -0.1% | 1.8% | -1.1% | 14.9% | 22.0% |
| 5 | Real Estate | XLRE | -0.1 | -2.5% | -1.3% | -0.6% | 5.9% | 4.1% |
| 6 | Consumer Discretionary | XLY | -0.2 | -4.0% | -0.8% | 0.3% | -1.3% | 8.9% |
| 7 | Healthcare | XLV | -0.8 | -0.8% | 1.9% | -6.0% | -5.0% | 11.2% |
| 8 | Financials | XLF | -1.4 | -1.1% | -1.4% | -1.2% | -3.8% | -0.2% |
| 9 | Consumer Staples | XLP | -2.5 | -2.9% | -1.7% | -5.7% | 3.6% | -0.9% |
| 10 | Communication Services | XLC | -2.7 | -3.6% | -3.5% | -5.6% | -2.4% | 10.0% |
| 11 | Utilities | XLU | -4.1 | -3.2% | -5.7% | -7.5% | -1.2% | 6.4% |

---

## Detailed Analysis

Claude analysis was not run because the focus-quality gates filtered out every
candidate. This means no ticker met the required combination of same-day
actionability, focus-list structure, nearby trigger/retest path, and nearby
invalidation.
Focus gate audit saved separately:
reports/focus_gate_audit_2026-06-03_1801.csv


## Focus Structure

The Focus Structure Layer requires the Sean-style shape before spending Claude
calls: recent impulse, controlled digestion, compression/base behavior, EMA
hold or reclaim, nearby trigger or retest path, nearby invalidation, and no
severe extension without digestion. Candidates with `extended_no_base`,
`sloppy_chop`, or `no_clear_structure` are excluded from AI review. The
Blueprint Fit layer also requires a named blueprint setup, volume confirmation,
compression or a tight base, EMA support, and a clear trigger/retest reference.

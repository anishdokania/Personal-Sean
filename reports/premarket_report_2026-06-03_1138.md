# Trading Blueprint Daily Focus List

Generated: 2026-06-03 11:38

## Summary

No high-quality same-day focus-list setups passed the filters today.

| Metric | Count |
|---|---:|
| Symbols loaded for hard gate | 10 |
| Hard gate survivors | 2 |
| Hard gate rejected | 8 |
| Candidates technically scored | 2 |
| Candidates evaluated by quality gates | 2 |
| Qualified after focus gates | 0 |
| Selected for Claude | 0 |

## Market Context

Bias: risk_on | Score: 91.7

| Symbol | Name | Regime | Close | EMA8 | EMA21 | EMA50 | Score |
|---|---|---|---:|---:|---:|---:|---:|
| SPY | S&P 500 | strong_bullish | 754.03 | 753.15 | 742.08 | 721.34 | 100 |
| QQQ | Nasdaq 100 | strong_bullish | 742.06 | 735.09 | 714.85 | 679.82 | 100 |
| IWM | Russell 2000 | bullish | 287.17 | 288.22 | 284.08 | 276.05 | 75 |

Warnings:
- None

## Sector Leadership

Top sectors:
1. Technology / XLK - score 29.8
2. Energy / XLE - score 9.7
3. Materials / XLB - score 4.4

Weak sectors:
1. Consumer Staples / XLP - score -2.1
2. Communication Services / XLC - score -2.7
3. Utilities / XLU - score -3.0

| Rank | Sector | ETF | Score | 1W | 1M | 3M | 6M | 1Y |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Technology | XLK | 29.8 | 6.0% | 20.6% | 39.8% | 36.5% | 67.8% |
| 2 | Energy | XLE | 9.7 | 4.0% | -0.2% | 5.4% | 29.8% | 43.5% |
| 3 | Materials | XLB | 4.4 | 1.2% | 2.3% | -0.2% | 16.2% | 19.3% |
| 4 | Industrials | XLI | 4.4 | 0.3% | 2.3% | -0.6% | 15.5% | 22.6% |
| 5 | Real Estate | XLRE | 0.7 | -1.8% | -0.6% | 0.1% | 6.6% | 4.9% |
| 6 | Consumer Discretionary | XLY | -0.5 | -4.2% | -1.1% | 0.0% | -1.6% | 8.7% |
| 7 | Healthcare | XLV | -0.6 | -0.6% | 2.2% | -5.8% | -4.8% | 11.4% |
| 8 | Financials | XLF | -1.5 | -1.1% | -1.4% | -1.3% | -3.9% | -0.3% |
| 9 | Consumer Staples | XLP | -2.1 | -2.4% | -1.2% | -5.3% | 4.1% | -0.4% |
| 10 | Communication Services | XLC | -2.7 | -3.6% | -3.5% | -5.7% | -2.4% | 10.0% |
| 11 | Utilities | XLU | -3.0 | -2.0% | -4.6% | -6.4% | -0.1% | 7.6% |

---

## Detailed Analysis

Claude analysis was not run because the focus-quality gates filtered out every
candidate. This means no ticker met the required combination of same-day
actionability, focus-list structure, nearby trigger/retest path, and nearby
invalidation.
Focus gate audit saved separately:
reports/focus_gate_audit_2026-06-03_1138.csv


## Focus Structure

The Focus Structure Layer requires the Sean-style shape before spending Claude
calls: recent impulse, controlled digestion, compression/base behavior, EMA
hold or reclaim, nearby trigger or retest path, nearby invalidation, and no
severe extension without digestion. Candidates with `extended_no_base`,
`sloppy_chop`, or `no_clear_structure` are excluded from AI review.

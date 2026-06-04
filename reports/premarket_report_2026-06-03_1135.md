# Trading Blueprint Daily Focus List

Generated: 2026-06-03 11:35

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
| SPY | S&P 500 | strong_bullish | 754.22 | 753.19 | 742.09 | 721.34 | 100 |
| QQQ | Nasdaq 100 | strong_bullish | 742.29 | 735.15 | 714.88 | 679.83 | 100 |
| IWM | Russell 2000 | bullish | 287.39 | 288.27 | 284.10 | 276.06 | 75 |

Warnings:
- None

## Sector Leadership

Top sectors:
1. Technology / XLK - score 29.9
2. Energy / XLE - score 9.6
3. Materials / XLB - score 4.4

Weak sectors:
1. Consumer Staples / XLP - score -2.1
2. Communication Services / XLC - score -2.7
3. Utilities / XLU - score -3.0

| Rank | Sector | ETF | Score | 1W | 1M | 3M | 6M | 1Y |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Technology | XLK | 29.9 | 6.1% | 20.7% | 39.9% | 36.7% | 68.0% |
| 2 | Energy | XLE | 9.6 | 3.9% | -0.3% | 5.4% | 29.7% | 43.3% |
| 3 | Materials | XLB | 4.4 | 1.2% | 2.3% | -0.2% | 16.2% | 19.3% |
| 4 | Industrials | XLI | 4.4 | 0.4% | 2.3% | -0.6% | 15.5% | 22.7% |
| 5 | Real Estate | XLRE | 0.7 | -1.8% | -0.6% | 0.1% | 6.7% | 4.9% |
| 6 | Consumer Discretionary | XLY | -0.4 | -4.2% | -1.1% | 0.1% | -1.5% | 8.7% |
| 7 | Healthcare | XLV | -0.6 | -0.7% | 2.1% | -5.9% | -4.8% | 11.4% |
| 8 | Financials | XLF | -1.5 | -1.2% | -1.5% | -1.3% | -3.9% | -0.3% |
| 9 | Consumer Staples | XLP | -2.1 | -2.4% | -1.2% | -5.3% | 4.1% | -0.4% |
| 10 | Communication Services | XLC | -2.7 | -3.6% | -3.5% | -5.7% | -2.4% | 10.0% |
| 11 | Utilities | XLU | -3.0 | -2.1% | -4.7% | -6.5% | -0.1% | 7.6% |

---

## Detailed Analysis

Claude analysis was not run because the focus-quality gates filtered out every
candidate. This means no ticker met the required combination of same-day
actionability, focus-list structure, nearby trigger/retest path, and nearby
invalidation.
Focus gate audit saved separately:
reports/focus_gate_audit_2026-06-03_1135.csv


## Focus Structure

The Focus Structure Layer requires the Sean-style shape before spending Claude
calls: recent impulse, controlled digestion, compression/base behavior, EMA
hold or reclaim, nearby trigger or retest path, nearby invalidation, and no
severe extension without digestion. Candidates with `extended_no_base`,
`sloppy_chop`, or `no_clear_structure` are excluded from AI review.

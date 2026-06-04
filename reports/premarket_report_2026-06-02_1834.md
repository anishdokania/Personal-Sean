# Trading Blueprint Daily Focus List

Generated: 2026-06-02 18:34

## Summary

No high-quality same-day focus-list setups passed the filters today.

| Metric | Count |
|---|---:|
| Candidates scanned | 149 |
| Candidates technically scored | 50 |
| Candidates evaluated by quality gates | 50 |
| Qualified after focus gates | 0 |
| Selected for Claude | 0 |

---

## Detailed Analysis

Claude analysis was not run because the focus-quality gates filtered out every
candidate. This means no ticker met the required combination of same-day
actionability, focus-list structure, nearby trigger/retest path, and nearby
invalidation.
Focus gate audit saved separately:
reports/focus_gate_audit_2026-06-02_1834.csv


## Focus Structure

The Focus Structure Layer requires the Sean-style shape before spending Claude
calls: recent impulse, controlled digestion, compression/base behavior, EMA
hold or reclaim, nearby trigger or retest path, nearby invalidation, and no
severe extension without digestion. Candidates with `extended_no_base`,
`sloppy_chop`, or `no_clear_structure` are excluded from AI review.

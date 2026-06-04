# Post-Primary Setup Detector Report

Generated: 2026-06-03 19:40

## Summary

| Metric | Count |
|---|---:|
| Primary-gated stocks | 8 |
| Stocks evaluated by detectors | 8 |
| Stocks with detector hits | 8 |
| Chart review candidates | 8 |
| Rejected by obvious reject conditions | 0 |
| Detector data failures | 0 |

The detector layer is a high-recall retrieval stage. It keeps names for visual chart review when any high-value detector fires or when multiple medium-value detectors cluster. Interest rank is used only for sorting.

## Top Candidates By Setup Family

### Leaders near highs

- None

### Right-side/base setups

- None

### Inside-day compression

- None

### Breakout/retest

- ABBV | rank 66 | tags: LEADING_NAME_NEAR_TRIGGER, RIGHT_SIDE_OF_BASE | trigger: 220.01 | stop/ref: 212.42 | why: ABBV: LEADING_NAME_NEAR_TRIGGER, RIGHT_SIDE_OF_BASE. Trigger 220.01; stop/ref 212.42.
- ARE | rank 63 | tags: BREAKOUT_RETEST, FAILED_BREAKDOWN_RECLAIM, INSIDE_DAY_NEAR_HIGHS, POSSIBLE_ACCUMULATION_BASE | trigger: 52.60 | stop/ref: 51.29 | Warnings: NO_CLEAR_TRIGGER, RESISTANCE_TOO_CLOSE | why: ARE: BREAKOUT_RETEST, FAILED_BREAKDOWN_RECLAIM, INSIDE_DAY_NEAR_HIGHS, POSSIBLE_ACCUMULATION_BASE. Trigger 52.60; stop/ref 51.29. Warning: NO_CLEAR_TRIGGER, RESISTANCE_TOO_CLOSE.
- AFL | rank 53 | tags: BIG_BASE_NEAR_HIGHS | trigger: 118.66 | stop/ref: 110.54 | why: AFL: BIG_BASE_NEAR_HIGHS. Trigger 118.66; stop/ref 110.54.
- MMM | rank 48 | tags: FAILED_BREAKDOWN_RECLAIM, POSSIBLE_ACCUMULATION_BASE | trigger: 156.69 | stop/ref: 150.19 | Warnings: BREAKOUT_FAILURE, DO_NOT_CHASE, FAILED_BREAKOUT | why: MMM: FAILED_BREAKDOWN_RECLAIM, POSSIBLE_ACCUMULATION_BASE. Trigger 156.69; stop/ref 150.19. Warning: BREAKOUT_FAILURE, DO_NOT_CHASE, FAILED_BREAKOUT.

### Possible accumulation/emerging reclaim

- ADBE | rank 29 | tags: FAILED_BREAKDOWN_RECLAIM, POSSIBLE_ACCUMULATION_BASE | trigger: 275.44 | stop/ref: 236.66 | Warnings: BREAKOUT_FAILURE, DO_NOT_CHASE, FAILED_BREAKOUT | why: ADBE: FAILED_BREAKDOWN_RECLAIM, POSSIBLE_ACCUMULATION_BASE. Trigger 275.44; stop/ref 236.66. Warning: BREAKOUT_FAILURE, DO_NOT_CHASE, FAILED_BREAKOUT.

### Power gap/catalyst gap

- A | rank 79 | tags: BIG_BASE_NEAR_HIGHS, CATALYST_GAP, FAILED_BREAKDOWN_RECLAIM, LEADING_NAME_NEAR_TRIGGER, POSSIBLE_ACCUMULATION_BASE, POST_GAP_FLAG | trigger: 139.35 | stop/ref: 122.63 | Warnings: MILD_EXTENSION, RESISTANCE_TOO_CLOSE, STOP_TOO_WIDE | why: A: BIG_BASE_NEAR_HIGHS, CATALYST_GAP, FAILED_BREAKDOWN_RECLAIM, LEADING_NAME_NEAR_TRIGGER, POSSIBLE_ACCUMULATION_BASE. Trigger 139.35; stop/ref 122.63. Warning: MILD_EXTENSION, RESISTANCE_TOO_CLOSE, STOP_TOO_WIDE.
- AKAM | rank 79 | tags: CATALYST_GAP, FAILED_BREAKDOWN_RECLAIM, LEADING_NAME_NEAR_TRIGGER, POSSIBLE_ACCUMULATION_BASE, POST_GAP_FLAG, RIGHT_SIDE_OF_BASE | trigger: 165.45 | stop/ref: 138.10 | Warnings: MILD_EXTENSION, RESISTANCE_TOO_CLOSE, STOP_TOO_WIDE | why: AKAM: CATALYST_GAP, FAILED_BREAKDOWN_RECLAIM, LEADING_NAME_NEAR_TRIGGER, POSSIBLE_ACCUMULATION_BASE, POST_GAP_FLAG. Trigger 165.45; stop/ref 138.10. Warning: MILD_EXTENSION, RESISTANCE_TOO_CLOSE, STOP_TOO_WIDE.
- AMD | rank 52 | tags: CATALYST_GAP, RIGHT_SIDE_OF_BASE | trigger: 546.44 | stop/ref: 426.05 | Warnings: MILD_EXTENSION, NO_CLEAR_TRIGGER, STOP_TOO_WIDE | why: AMD: CATALYST_GAP, RIGHT_SIDE_OF_BASE. Trigger 546.44; stop/ref 426.05. Warning: MILD_EXTENSION, NO_CLEAR_TRIGGER, STOP_TOO_WIDE.

### High RVOL unusual activity

- None

### Trend/reclaim

- None

### Unclassified

- None

## Rejected Or Deferred

- None

## Notes

- `chart_needed = true` means the ticker should be considered for visual chart review.
- `reject_reason` is limited to obvious issues such as no meaningful setup tags, major failed breakout behavior, severe extension without a fresh catalyst, or no actionable trigger cluster.
- `interest_rank` is not a strict setup score and should not be used as a standalone rejection rule.

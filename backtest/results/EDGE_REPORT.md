# UnR Snipe — Edge Report

Window: **2021-01-01 → 2026-07-09** | Universe: 106 high-ADR movers | Primary ADR floor: 5.0%

Every number below is point-in-time (no look-ahead). Where daily bars
cannot order stop-vs-target inside one bar, the outcome is resolved with
hourly data when it exists; what remains is *bracketed* between a
pessimistic and an optimistic bound rather than guessed.

## Variant matrix

| Variant | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R | PF | MaxDD | exact/hourly/assumed |
|---|---|---|---|---|---|---|---|---|---|
| A1 snipe→swing target (pessimistic, hourly) | 1231 | 19% | +4.10R | -1.13R | **-0.163R** [-0.30, -0.01] | -201.2R | 0.77 | -86.0% | 941/92/198 |
| A2 snipe→swing target (optimistic, hourly) | 1253 | 31% | +3.70R | -1.14R | **+0.362R** [+0.20, +0.53] | +453.7R | 1.19 | -35.2% | 948/94/211 |
| A3 snipe→swing target (pessimistic, daily-only) | 1222 | 18% | +4.19R | -1.13R | **-0.198R** [-0.34, -0.05] | -241.9R | 0.75 | -88.2% | 936/0/286 |
| A4 snipe→swing target (optimistic, daily-only) | 1263 | 35% | +3.74R | -1.12R | **+0.593R** [+0.42, +0.77] | +749.4R | 1.65 | -25.2% | 955/0/308 |
| B1 snipe→8EMA trail (exact) | 1142 | 16% | +6.76R | -1.07R | **+0.181R** [-0.18, +0.64] | +206.2R | 0.98 | -71.2% | 1142/0/0 |
| C1 snipe→hybrid partial+trail (pessimistic, hourly) | 1219 | 21% | +4.22R | -1.07R | **+0.024R** [-0.20, +0.27] | +29.4R | 0.92 | -64.8% | 942/86/191 |
| C2 snipe→hybrid partial+trail (optimistic, hourly) | 1227 | 32% | +3.09R | -1.06R | **+0.257R** [+0.05, +0.51] | +315.1R | 1.14 | -51.7% | 944/89/194 |
| D1 next-open→swing target (pessimistic, hourly) | 1928 | 34% | +1.25R | -0.82R | **-0.129R** [-0.19, -0.06] | -247.9R | 0.75 | -93.0% | 1832/25/71 |
| D2 next-open→8EMA trail (exact) | 1445 | 26% | +2.78R | -0.87R | **+0.080R** [-0.06, +0.23] | +115.8R | 1.07 | -57.8% | 1445/0/0 |
| F1 close-entry→swing target (pessimistic, hourly) | 1863 | 48% | +1.10R | -0.92R | **+0.040R** [-0.03, +0.11] | +74.7R | 1.05 | -44.9% | 1812/24/27 |
| F2 close-entry→swing target (optimistic, hourly) | 1865 | 49% | +1.09R | -0.90R | **+0.065R** [-0.00, +0.13] | +120.3R | 1.10 | -43.2% | 1813/25/27 |
| F3 close-entry→8EMA trail (exact) | 1362 | 28% | +3.14R | -0.85R | **+0.266R** [+0.08, +0.50] | +362.8R | 1.32 | -52.6% | 1362/0/0 |
| F4 close-entry→hybrid (pessimistic, hourly) | 1605 | 39% | +1.55R | -0.76R | **+0.139R** [+0.04, +0.25] | +223.3R | 1.19 | -44.6% | 1574/17/14 |
| F5 close-entry→hybrid (optimistic, hourly) | 1598 | 39% | +1.54R | -0.75R | **+0.148R** [+0.05, +0.26] | +236.5R | 1.18 | -43.3% | 1567/16/15 |
| E ADR>=3.5% snipe→swing (pessimistic, hourly) | 1860 | 19% | +4.04R | -1.20R | **-0.216R** [-0.35, -0.09] | -402.1R | 0.79 | -91.3% | 1414/120/326 |
| E ADR>=7.0% snipe→swing (pessimistic, hourly) | 643 | 18% | +3.88R | -1.15R | **-0.241R** [-0.43, -0.05] | -155.0R | 0.74 | -76.3% | 494/50/99 |
| G1 F3 + risk-on regime | 1134 | 28% | +3.37R | -0.86R | **+0.317R** [+0.10, +0.58] | +359.6R | 1.47 | -43.9% | 1134/0/0 |
| G2 F3 + no-chase (close ≤0.5 ADR above level) | 1026 | 26% | +3.70R | -0.91R | **+0.306R** [+0.06, +0.61] | +314.1R | 1.40 | -50.9% | 1026/0/0 |
| G3 F3 + ADR 5–10% band | 1192 | 28% | +3.39R | -0.84R | **+0.359R** [+0.15, +0.61] | +428.0R | 1.49 | -41.4% | 1192/0/0 |
| G5 F3 + all three filters | 711 | 28% | +4.26R | -0.91R | **+0.529R** [+0.20, +0.95] | +376.2R | 1.67 | -37.4% | 711/0/0 |

## Ground-truth window (hourly coverage, 2024-07-11 onward)

Inside this window ambiguous days are *measured* from hourly bars, so the
pessimistic/optimistic bracket nearly collapses — this is the closest thing
to the strategy's true daily-approximated edge:

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| A1 snipe→swing target (pessimistic, hourly) | 677 | 20% | +3.93R | -1.15R | **-0.113R** [-0.31, +0.09] | -76.2R |
| A2 snipe→swing target (optimistic, hourly) | 687 | 30% | +3.42R | -1.16R | **+0.226R** [+0.02, +0.44] | +155.2R |
| B1 snipe→8EMA trail (exact) | 590 | 16% | +6.41R | -1.10R | **+0.087R** [-0.26, +0.49] | +51.5R |
| C1 snipe→hybrid partial+trail (pessimistic, hourly) | 658 | 21% | +3.96R | -1.10R | **-0.012R** [-0.25, +0.24] | -8.0R |
| C2 snipe→hybrid partial+trail (optimistic, hourly) | 666 | 30% | +2.92R | -1.09R | **+0.106R** [-0.13, +0.35] | +70.3R |
| F1 close-entry→swing target (pessimistic, hourly) | 1010 | 48% | +1.14R | -0.92R | **+0.077R** [-0.01, +0.17] | +78.1R |
| F2 close-entry→swing target (optimistic, hourly) | 1010 | 49% | +1.13R | -0.90R | **+0.103R** [+0.02, +0.19] | +104.0R |
| F3 close-entry→8EMA trail (exact) | 692 | 28% | +3.51R | -0.88R | **+0.352R** [+0.13, +0.59] | +243.5R |
| F4 close-entry→hybrid (pessimistic, hourly) | 833 | 39% | +1.64R | -0.77R | **+0.179R** [+0.05, +0.31] | +148.8R |
| F5 close-entry→hybrid (optimistic, hourly) | 829 | 39% | +1.63R | -0.76R | **+0.181R** [+0.06, +0.31] | +150.0R |
| G5 F3 + all three filters | 396 | 28% | +4.19R | -0.91R | **+0.533R** [+0.19, +0.91] | +211.1R |

Measured-only subset (trades resolved exactly or by hourly data — zero
assumption content), core variant:

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| measured (exact+hourly) | 1033 | 17% | +4.35R | -1.14R | **-0.207R** [-0.36, -0.04] | -213.7R |
| assumed (bounds applied) | 198 | 27% | +3.31R | -1.12R | **+0.063R** [-0.27, +0.42] | +12.6R |

## Where the edge lives (core variant, pessimistic + hourly)

### By setup type

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| unr_ema21 | 78 | 19% | +3.90R | -1.15R | **-0.180R** [-0.67, +0.38] | -14.0R |
| unr_ema8 | 550 | 19% | +3.93R | -1.11R | **-0.127R** [-0.33, +0.09] | -70.0R |
| unr_pdl | 603 | 18% | +4.31R | -1.15R | **-0.194R** [-0.40, +0.02] | -117.1R |

### By ADR% at signal

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| ADR 10%+ | 228 | 15% | +4.39R | -1.11R | **-0.268R** [-0.59, +0.11] | -61.0R |
| ADR 5–7% | 592 | 19% | +4.33R | -1.12R | **-0.077R** [-0.29, +0.15] | -45.7R |
| ADR 7–10% | 411 | 19% | +3.66R | -1.17R | **-0.230R** [-0.45, -0.00] | -94.4R |

### By planned reward:risk

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| RR 1.0–1.5 | 140 | 35% | +1.77R | -1.04R | **-0.059R** [-0.30, +0.21] | -8.2R |
| RR 1.5–2.5 | 233 | 25% | +2.47R | -1.10R | **-0.210R** [-0.42, +0.01] | -49.0R |
| RR 2.5–4 | 264 | 23% | +3.57R | -1.15R | **-0.062R** [-0.33, +0.21] | -16.5R |
| RR 4+ | 594 | 10% | +8.13R | -1.15R | **-0.214R** [-0.46, +0.05] | -127.4R |

### By year

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| 2021 | 149 | 13% | +3.98R | -1.07R | **-0.391R** [-0.70, -0.04] | -58.3R |
| 2022 | 195 | 16% | +3.53R | -1.16R | **-0.412R** [-0.69, -0.12] | -80.4R |
| 2023 | 125 | 21% | +4.80R | -1.13R | **+0.102R** [-0.38, +0.65] | +12.7R |
| 2024 | 218 | 19% | +4.28R | -1.12R | **-0.080R** [-0.43, +0.33] | -17.4R |
| 2025 | 302 | 20% | +4.19R | -1.22R | **-0.146R** [-0.44, +0.17] | -44.0R |
| 2026 | 242 | 20% | +3.89R | -1.06R | **-0.057R** [-0.36, +0.28] | -13.7R |

### By market regime at signal

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| SPY < 50SMA (risk-off) | 201 | 19% | +3.57R | -1.15R | **-0.262R** [-0.58, +0.09] | -52.7R |
| SPY > 50SMA (risk-on) | 1030 | 18% | +4.21R | -1.13R | **-0.144R** [-0.30, +0.02] | -148.4R |

### Symbol concentration (top 10 by |total R|, core variant)

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| SMCI | 24 | 29% | +9.99R | -1.07R | **+2.155R** [-0.18, +5.16] | +51.7R |
| NTLA | 22 | 18% | +1.75R | -1.79R | **-1.144R** [-2.64, -0.14] | -25.2R |
| MRNA | 33 | 15% | +1.52R | -1.12R | **-0.721R** [-1.02, -0.36] | -23.8R |
| LCID | 22 | 5% | +2.22R | -1.21R | **-1.059R** [-1.42, -0.65] | -23.3R |
| GME | 27 | 4% | +4.07R | -1.05R | **-0.859R** [-1.07, -0.47] | -23.2R |
| QBTS | 15 | 13% | +1.50R | -1.77R | **-1.335R** [-2.80, -0.34] | -20.0R |
| RUN | 19 | 5% | +0.99R | -1.09R | **-0.984R** [-1.14, -0.74] | -18.7R |
| PLTR | 6 | 33% | +10.96R | -1.06R | **+2.950R** [-1.07, +8.02] | +17.7R |
| DDOG | 13 | 31% | +6.85R | -1.09R | **+1.350R** [-0.59, +3.62] | +17.6R |
| NET | 13 | 31% | +6.82R | -1.12R | **+1.322R** [-0.71, +3.79] | +17.2R |

## Trail-exit variant slices (B1, exact on daily bars)

### By setup type

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| unr_ema21 | 73 | 18% | +9.23R | -1.10R | **+0.738R** [-0.72, +3.27] | +53.9R |
| unr_ema8 | 503 | 14% | +7.26R | -1.01R | **+0.177R** [-0.41, +1.07] | +89.0R |
| unr_pdl | 566 | 17% | +6.13R | -1.12R | **+0.112R** [-0.25, +0.51] | +63.4R |

### By ADR% at signal

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| ADR 10%+ | 208 | 17% | +2.19R | -1.04R | **-0.480R** [-0.72, -0.22] | -99.9R |
| ADR 5–7% | 559 | 16% | +8.24R | -1.06R | **+0.424R** [-0.21, +1.29] | +237.1R |
| ADR 7–10% | 375 | 15% | +7.48R | -1.10R | **+0.184R** [-0.28, +0.72] | +69.1R |

### By year

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| 2021 | 150 | 18% | +3.27R | -1.01R | **-0.242R** [-0.62, +0.24] | -36.4R |
| 2022 | 187 | 12% | +5.77R | -1.07R | **-0.224R** [-0.67, +0.34] | -41.9R |
| 2023 | 128 | 18% | +4.40R | -1.03R | **-0.056R** [-0.67, +0.87] | -7.2R |
| 2024 | 197 | 18% | +11.64R | -1.06R | **+1.197R** [-0.36, +3.52] | +235.8R |
| 2025 | 290 | 16% | +6.22R | -1.16R | **-0.013R** [-0.46, +0.49] | -3.8R |
| 2026 | 190 | 15% | +7.91R | -1.00R | **+0.315R** [-0.37, +1.20] | +59.8R |

## Close-entry + trail model (F3, exact on daily bars)

### By setup type

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| unr_ema21 | 127 | 31% | +2.51R | -0.75R | **+0.256R** [-0.16, +0.74] | +32.5R |
| unr_ema8 | 533 | 28% | +2.98R | -0.88R | **+0.203R** [-0.02, +0.44] | +108.4R |
| unr_pdl | 702 | 27% | +3.41R | -0.84R | **+0.316R** [+0.02, +0.71] | +221.6R |

### By ADR% at signal

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| ADR 10%+ | 229 | 26% | +2.24R | -0.85R | **-0.041R** [-0.31, +0.29] | -9.3R |
| ADR 5–7% | 691 | 30% | +3.29R | -0.85R | **+0.390R** [+0.09, +0.79] | +269.1R |
| ADR 7–10% | 442 | 26% | +3.38R | -0.85R | **+0.232R** [-0.03, +0.52] | +102.7R |

### By chase (close vs level, ADRs)

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| chase 0.25–0.5 ADR | 448 | 26% | +3.50R | -0.89R | **+0.249R** [-0.03, +0.55] | +111.6R |
| chase 0.5–1.0 ADR | 408 | 29% | +2.46R | -0.77R | **+0.171R** [-0.04, +0.40] | +69.8R |
| chase 1.0+ ADR | 147 | 27% | +2.21R | -0.75R | **+0.054R** [-0.27, +0.44] | +7.9R |
| chase < 0.25 ADR | 359 | 29% | +3.89R | -0.92R | **+0.483R** [-0.02, +1.23] | +173.3R |

### By year

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| 2021 | 180 | 30% | +2.72R | -0.80R | **+0.258R** [-0.11, +0.68] | +46.5R |
| 2022 | 197 | 26% | +1.88R | -0.83R | **-0.114R** [-0.35, +0.16] | -22.4R |
| 2023 | 171 | 33% | +1.82R | -0.88R | **+0.005R** [-0.25, +0.28] | +0.9R |
| 2024 | 258 | 27% | +4.73R | -0.79R | **+0.709R** [+0.03, +1.71] | +183.0R |
| 2025 | 348 | 27% | +3.91R | -0.93R | **+0.360R** [+0.04, +0.71] | +125.1R |
| 2026 | 208 | 26% | +2.80R | -0.82R | **+0.141R** [-0.19, +0.53] | +29.4R |

### By market regime at signal

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| SPY < 50SMA (risk-off) | 255 | 26% | +2.36R | -0.81R | **+0.021R** [-0.22, +0.29] | +5.4R |
| SPY > 50SMA (risk-on) | 1107 | 28% | +3.31R | -0.86R | **+0.323R** [+0.10, +0.59] | +357.1R |

## Split-sample check (filters must hold out-of-window)

Filters were chosen for trading-logic reasons; this table checks they
are not an artifact of one era. 2021–2023 has no hourly data (bounded),
2024–2026 is the measured window:

| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |
|---|---|---|---|---|---|---|
| F3 close-entry→8EMA trail (exact) · 2021–2023 | 548 | 30% | +2.14R | -0.83R | **+0.046R** [-0.13, +0.24] | +25.0R |
| F3 close-entry→8EMA trail (exact) · 2024–2026 | 814 | 27% | +3.89R | -0.86R | **+0.415R** [+0.13, +0.78] | +337.5R |
| G5 F3 + all three filters · 2021–2023 | 259 | 27% | +3.00R | -0.92R | **+0.154R** [-0.18, +0.52] | +39.8R |
| G5 F3 + all three filters · 2024–2026 | 452 | 28% | +4.96R | -0.90R | **+0.744R** [+0.27, +1.38] | +336.4R |


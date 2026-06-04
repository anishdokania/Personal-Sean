# trading_system Project State

Last updated: 2026-06-03

## Architecture Status

- Module 1: Data Fetcher - complete and verified
- Module 2: Sector Scanner - complete and verified
- Module 3: Stock Filter - complete and verified
- Module 4: Technical Analyzer - complete and verified
- Module 5: Claude Analysis Layer - complete and verified
- Module 6: Report Generator - complete and verified
- Module 7: Main Orchestrator - complete and verified
- Broad U.S.-Listed Universe Mode - complete and verified
- Today Focus Layer - complete and verified
- Focus Structure Layer - complete and verified
- Post-Primary Detector Retrieval Layer - complete and verified
- Optional Visual Chart Review Layer - added and verified, disabled by default

## Completed Work

### Module 1: Data Fetcher

- Implemented `fetch_stock_data()`.
- Implemented `fetch_multiple_stocks()`.
- Implemented `validate_ohlcv()`.
- Verified live yfinance data fetching for AAPL, MSFT, and NVDA.

### Module 2: Sector Scanner

- Implemented sector ETF proxy map in `sector_scanner.py`.
- Implemented `calculate_performance(df, days)`.
- Implemented `rank_sectors()`.
- Implemented `get_top_sectors(n=5)`.
- Sector ranking uses 5-day, 21-day, and 63-day percentage returns.
- Scoring assigns rank points per timeframe and sums them into a combined sector strength score.
- Output now exposes transparent timeframe ranks: `Rank_1W`, `Rank_1M`, and `Rank_3M`.
- ETF failures or insufficient history are skipped without crashing the full scan.

### Module 3: Stock Filter

- Implemented `load_sp500_universe()` using Wikipedia S&P 500 constituents parsed with `pandas.read_html`.
- Implemented yfinance-compatible symbol cleaning, including `BRK.B` to `BRK-B` style replacements.
- Implemented `filter_by_top_sectors()` using Module 2 sector names with GICS sector aliases where needed.
- Implemented `passes_basic_filters()` with price, 20-day average volume, and SMA20 rules.
- Implemented `scan_candidates()` to load top sectors, scan matching S&P 500 stocks, skip ticker failures, and return sorted candidate results.
- Candidate output includes `Symbol`, `Company`, `Sector`, `Close`, `AvgVolume20`, `SMA20`, and `AboveSMA20`.
- Added universe modes to `scan_candidates()`: `sp500` and `us_listed`.
- `scan_candidates()` now defaults to symbol-row loading for the primary hard universe gate; sector filtering and legacy light price/volume/SMA filters are opt-in only.
- `us_listed` mode uses the broad U.S.-listed universe from `universe.py` and feeds symbol rows directly to the primary hard gate in the main scanner.
- Added broad-mode tradability constants: `MIN_PRICE = 3`, `MIN_AVG_VOLUME = 500_000`, and `MIN_DOLLAR_VOLUME = 20_000_000`.
- Candidate output now also includes `Exchange` and `DollarVolume20` when available.
- Added `lxml` to `requirements.txt` because `pandas.read_html` requires an HTML parser.
- Verified live S&P 500 loading, top-sector selection, ticker scanning, candidate filtering, and graceful skip behavior.

### Module 4: Technical Analyzer

- Implemented `add_moving_averages()` with EMA8, EMA21, EMA50, SMA20, and AvgVolume20.
- Implemented `calculate_atr()` with ATR14 output by default.
- Implemented `detect_ema_regime()` for strong bullish, bullish, strong bearish, bearish, and mixed regimes.
- Implemented `detect_ignition_candle()` using body-to-range and relative-volume rules.
- Implemented `detect_volume_anomalies()` for wide-spread low-volume and narrow-spread high-volume candles.
- Implemented `detect_accumulation_distribution()` using up/down volume and a simple OBV trend proxy.
- Implemented `find_support_resistance()` using swing lows and swing highs.
- Implemented `detect_supply_demand_zones()` using high-volume directional candles and prior-candle zones.
- Implemented `analyze_stock_technicals()` as the main structured Module 4 entrypoint.
- Verified live technical analysis output for NVDA, AAPL, and MSFT.

### Module 5: Claude Analysis Layer

- Implemented `load_anthropic_client()` using `.env` and `ANTHROPIC_API_KEY`.
- Implemented `build_blueprint_prompt()` for blueprint-based setup evaluation.
- Implemented `analyze_with_claude()` to call Claude for one stock and return parsed structured JSON.
- Implemented `analyze_stock_with_ai()` to combine Module 4 technical analysis with Claude evaluation.
- Added `DEFAULT_CLAUDE_MODEL` in `claude_analyzer.py` so model naming is easy to change in one place.
- Claude API usage is intended only for one shortlisted candidate at a time, not bulk scans.
- The module returns structured JSON-like Python dictionaries with setup score, bias, setup type, warnings, disqualifiers, and verdict.
- Verified live NVDA analysis with one Claude API call.
- Refined the Module 5 prompt for current-price-relative level interpretation so supply/resistance above price and support/demand below price are handled correctly.
- Added a deterministic post-parse level guardrail to keep reclaimed below-price supply/resistance from appearing as active overhead levels.
- Added `REQUIRED_AI_FIELDS` and `validate_ai_analysis()` so Claude outputs must include symbol, numeric overall score, bias, setup type, setup quality, and final verdict.
- Added `build_failed_analysis()` so malformed, incomplete, empty, or API-failed AI responses become explicit standardized failure dictionaries.
- Improved JSON parsing to handle simple Markdown JSON fences.
- Claude malformed or incomplete outputs now retry once with a stricter repair prompt before being marked failed.
- Claude prompt now explicitly evaluates whether each stock belongs on TODAY's focus list, not whether it is generally bullish.
- Claude schema now includes optional same-day fields: `actionability`, `trigger_level`, `invalidation_level`, `do_not_chase_above`, `same_day_plan`, and `why_today`.
- Claude prompt now also respects the deterministic Focus Structure Layer, including FocusStructureScore, structure type, impulse/digestion/compression evidence, trigger/invalidation proximity, extension risk, and structure disqualifiers.
- Compacted the Claude prompt for quick-read daily trading output and removed requests for long prose.
- Tightened the Claude JSON contract to a compact flat schema with exact fields, numeric-or-null trade levels, short assessment strings, and short arrays for warnings, disqualifiers, and same-day plan.
- Improved JSON extraction with `extract_json_object()` so raw JSON, fenced JSON, wrapper text, and the first balanced JSON object are parsed before falling back to repair.
- Added `normalize_ai_analysis()` to coerce/clamp scores, normalize bias/actionability/setup quality, normalize numeric levels, convert plan/warnings/disqualifiers to short arrays, truncate long strings, and preserve deterministic Today Focus and Focus Structure context.
- Made the Claude repair prompt strict and short; it now includes the invalid response, symbol, schema, and error only, instead of resending the full original prompt.
- Reduced default Claude token budgets to `PRIMARY_MAX_TOKENS = 1000` and `REPAIR_MAX_TOKENS = 800` with temperature remaining 0.
- Preserved the existing failed-analysis fallback so malformed or API-failed Claude responses still produce explicit report entries instead of crashing.
- Re-validated analyze-from-audit dry-run after compacting Claude output with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --dry-run`; it loaded 200 audit rows, found 6 passed candidates, selected `HUN`, `MBLY`, `HMY`, `IOVA`, `CNC`, and `COLD`, made 0 Claude calls, saved `reports/premarket_report_2026-06-02_2133.md`, and completed in 1 second.
- Re-validated analyze-from-audit Claude mode after compacting Claude output with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --max-ai-analyses 10`; it selected the same 6 tickers, avoided a full universe scan, made 6 Claude calls, parsed 6 of 6 analyses successfully, had 0 failed analyses, saved `reports/premarket_report_2026-06-02_2134.md`, and completed in 38 seconds.
- The prior analyze-from-audit JSON failure rate improved from 2 failed JSON parses out of 6 to 0 failed JSON parses out of 6 on the same audit candidate set.
- Added Claude Setup Judge v1 behind the `--use-setup-judge` flag for `--analyze-from-audit` mode only.
- Setup Judge v1 acts as a manual Blueprint-style chart review layer after deterministic focus gates and before full Claude analysis; it can approve, downgrade, or veto deterministic pass candidates.
- Setup Judge v1 returns compact JSON only with `judge_action`, `manual_review_pass`, `judge_rank_score`, setup grade, actionability, pattern, level quality, chase risk, structure quality, volume quality, thesis, reasons, veto reasons, and watch plan.
- Deterministic trigger, invalidation, and do-not-chase levels remain the source of truth; Setup Judge normalization overwrites any Claude-returned levels with deterministic levels from Today Focus context.
- If Setup Judge returns `veto` or `manual_review_pass = false`, full Claude analysis is skipped for that ticker and the report keeps the ticker as an auditable Judge veto entry.
- Setup Judge v1 is disabled by default, so existing scanner and analyze-from-audit behavior remains unchanged unless `--use-setup-judge` is passed.
- Re-validated default analyze-from-audit behavior without Setup Judge using `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --max-ai-analyses 10`; it selected `HUN`, `MBLY`, `HMY`, `IOVA`, `CNC`, and `COLD`, made 6 full Claude calls, saved `reports/premarket_report_2026-06-02_2309.md`, and did not include Setup Judge sections.
- Validated Setup Judge mode with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --max-ai-analyses 10 --use-setup-judge`; it selected the same 6 tickers, made 6 Setup Judge calls, parsed 6 Setup Judge results successfully, had 0 Setup Judge failures, made 6 full Claude calls because no tickers were vetoed, parsed 6 full Claude results successfully, had 0 full Claude failures, saved `reports/premarket_report_2026-06-02_2310.md`, and completed in 1 minute 8 seconds.
- No scoring weights, strategy thresholds, focus gates, Today Focus logic, Focus Structure logic, universe logic, APIs, dashboard, scheduler, database, broker integration, or auto-trading were changed.

### Module 6: Report Generator

- Implemented `sort_analyses()` to rank AI analysis dictionaries by `overall_score` descending, treating missing scores as 0 for sorting.
- Implemented `grade_label()` with Elite, Strong, Watchlist, Weak, Avoid, and Unknown labels.
- Implemented `format_single_analysis()` to create readable per-stock Markdown sections with verdict, key levels, trade plan ideas, blueprint assessment, warnings, and disqualifiers.
- Implemented `generate_markdown_report()` with timestamp, summary table, and detailed analysis sections.
- Implemented `save_report()` and `generate_and_save_report()` to save Markdown reports under `reports/`.
- `report.py` formats already-generated analysis dictionaries only.
- `report.py` does not fetch market data, scan stocks, or call Claude.
- Added a mock-data `__main__` verification block that prints a report and saves a `.md` file without external API calls.
- Corrected `requirements.txt` to include the project dependencies currently used across modules.
- Verified with `venv/bin/python report.py`.
- Failed AI analyses are displayed explicitly in summary and detail sections instead of appearing as vague Unknown / Not provided setup sections.
- Report mock data now includes a failed AI analysis sample to verify failure formatting without calling Claude.
- Report title changed to `Trading Blueprint Daily Focus List`.
- Summary table now includes `Actionability` and `Today Focus Score`.
- Per-stock report sections now include deterministic Today Focus details when present.
- Summary table now includes `Focus Structure Score`, `Structure Type`, and `Final Pre-AI Score`.
- Per-stock report sections now include deterministic Focus Structure details when present.
- Per-stock Claude sections now use a quicker-read format: compact score/actionability header, one-line verdict, trigger, invalidation, do-not-chase, concise plan, and concise warnings before deterministic details.
- Report formatting remains compatible with deterministic-only and failed-analysis entries.
- Report formatting now displays an optional compact Setup Judge section only when Setup Judge output is attached, including decision, rank score, grade, pattern, level quality, chase risk, volume quality, thesis, reasons, and veto reasons.
- Judge-vetoed candidates are clearly marked in the ticker header and still display deterministic trigger, invalidation, and do-not-chase levels.

### Module 7: Main Orchestrator

- Implemented `MAX_AI_ANALYSES` as the Claude cost-control cap.
- Implemented `score_candidate_pre_ai()` to rank Module 3 candidates deterministically using price above SMA20, distance from SMA20, and 20-day average volume.
- Implemented `select_candidates_for_ai()` to add `PreAIScore`, sort candidates, and select only the top capped candidates for Claude analysis.
- Implemented `run_premarket_scan()` to connect sector scanning, stock filtering, candidate pre-selection, OHLCV fetching, Claude AI analysis, and Markdown report generation.
- Implemented `main()` and the CLI block so the MVP can run with `venv/bin/python main.py`.
- The MVP pipeline now runs end-to-end and saves a Markdown premarket report under `reports/`.
- Default Claude calls were initially capped by `MAX_AI_ANALYSES = 5`; the cap was later raised to `MAX_AI_ANALYSES = 10` after quality gates were added so the cap is a maximum, not a target.
- Individual ticker failures are caught and do not crash the full report run.
- The system remains a decision-support tool only, not an auto-trader.
- No broker integration, order placement, scheduling, dashboarding, database, or prompt caching was added.
- Verified live with `venv/bin/python main.py`.
- Re-verified after AI failure-handling cleanup with `venv/bin/python main.py`; full pipeline still saves a report under `reports/`.
- Local retry tests confirmed that incomplete Claude output triggers exactly one repair attempt and falls back to a standardized failed analysis if repair also fails.
- Added `MAX_CANDIDATES_TO_SCORE` as an optional performance cap; it now defaults to no cap so all primary hard-gate survivors are technically/focus scored unless a caller explicitly limits it.
- Added `score_technicals_pre_ai()` to score candidates using Module 4 features before spending Claude calls.
- Technical pre-AI scoring uses EMA regime, ignition candle direction, accumulation/distribution, OBV trend, support/resistance, demand/supply context, volume anomalies, and extension from EMA8.
- Added `build_technical_shortlist()` to fetch OHLCV, run `analyze_stock_technicals()`, compute `TechnicalPreAIScore`, and select the final Claude shortlist.
- `run_premarket_scan()` sends Claude only candidates that pass deterministic pre-AI selection gates, reusing precomputed technical dictionaries.
- Live verification after technical-shortlist upgrade found 138 candidates, technically scored 50, selected 5, and saved a report under `reports/`.
- Added `UNIVERSE_MODE = "sp500"` and `MAX_UNIVERSE_SIZE = None` in `main.py`.
- Default remains `sp500` for speed and continuity; broad scans can be enabled by setting `UNIVERSE_MODE = "us_listed"` or calling `run_premarket_scan(universe_mode="us_listed", max_universe_size=300)`.
- Terminal output now reports universe mode, max universe size, raw universe count, and scanned universe count when available.
- Added CLI support in `main.py` for `--universe`, `--max-universe-size`, `--max-ai-analyses`, `--dry-run`, `--max-candidates-to-score`, `--output-dir`, and `--top-n-sectors`.
- Default `venv/bin/python main.py` behavior remains unchanged, while full broad-universe testing can now be run from the command line.
- Added dry-run/no-Claude mode; `--dry-run` forces the Claude cap to 0 while still scanning, filtering, scoring, applying focus gates, saving the audit CSV, and saving a Markdown report.
- Added runtime reporting with `time.perf_counter()` so each run prints total elapsed minutes and seconds.
- Terminal output now prints configuration, raw symbol count, primary hard-gate survivors/rejects, technically/focus scored count, qualified-after-gates count, selected-for-Claude count, Claude call count, focus gate audit path, report path, and total runtime.
- The MVP remains decision-support only, with no dashboard, scheduling, broker integration, database, prompt caching, or auto-trading added.

### Broad U.S.-Listed Universe

- Added `universe.py` to load broad U.S.-listed symbols from free Nasdaq Trader symbol directory files.
- Implemented `load_nasdaq_listed()` using `nasdaqlisted.txt`.
- Implemented `load_other_listed()` using `otherlisted.txt`.
- Implemented `clean_symbol_for_yfinance()` to strip symbols, convert dot share-class notation to hyphen notation, and skip obviously unsupported symbol forms.
- Implemented `is_likely_common_stock()` to filter obvious warrants, units, preferreds, rights, notes, bonds, ETFs, ETNs, funds, and other non-common-stock issues before yfinance calls.
- Implemented `load_us_listed_universe(include_etfs=False, common_stock_only=True)` to combine Nasdaq-listed and other-listed securities, drop duplicates, and return clean columns: `Symbol`, `Company`, `Exchange`, `Source`, `IsETF`, and `RawSecurityName`.
- Added optional `save_universe_cache()` and `load_universe_cache()` helpers under `data/us_listed_universe.csv`.
- Verified `venv/bin/python universe.py`; live counts were Nasdaq listed 4,256, other listed 3,166, combined raw 7,422, cleaned universe 5,161.
- Verification confirmed `TE`, `PLTR`, `SMCI`, `F`, and `AAPL` were present in the loaded broad universe.
- Re-verified default `venv/bin/python main.py`; `sp500` mode still works, found 149 candidates, technically/focus scored 50, qualified 0, selected 0 for Claude, and saved audit/report files.
- Verified limited broad run with `run_premarket_scan(universe_mode="us_listed", max_universe_size=300)`; loaded 5,161 symbols, scanned 300, found 65 basic candidates, technically/focus scored 50, qualified 1, selected 1 for Claude, saved `reports/focus_gate_audit_2026-06-02_1855.csv`, and saved `reports/premarket_report_2026-06-02_1856.md`.
- Verified CLI smoke test `venv/bin/python main.py --universe sp500 --dry-run`; raw universe count was 503, symbols scanned from universe were 247, basic candidates found were 149, technically/focus scored count was 50, qualified-after-gates count was 0, selected-for-Claude count was 0, Claude calls were 0, runtime was 1 minute 7 seconds, audit saved to `reports/focus_gate_audit_2026-06-02_1908.csv`, and report saved to `reports/premarket_report_2026-06-02_1908.md`.
- Verified limited broad CLI dry run `venv/bin/python main.py --universe us_listed --max-universe-size 300 --max-candidates-to-score 50 --dry-run`; cleaned broad universe was 5,161, symbols scanned were 300, basic candidates found were 65, technically/focus scored count was 50, qualified-after-gates count was 1, selected-for-Claude count was 0, Claude calls were 0, runtime was 1 minute 10 seconds, audit saved to `reports/focus_gate_audit_2026-06-02_1909.csv`, and report saved to `reports/premarket_report_2026-06-02_1909.md`.
- Attempted and completed full broad-universe CLI dry run `venv/bin/python main.py --universe us_listed --max-candidates-to-score 200 --dry-run`; cleaned broad universe was 5,161, symbols scanned were 5,161, basic candidates found were 1,011, technically/focus scored count was 200, qualified-after-gates count was 2, selected-for-Claude count was 0, Claude calls were 0, runtime was 26 minutes 31 seconds, audit saved to `reports/focus_gate_audit_2026-06-02_1936.csv`, and report saved to `reports/premarket_report_2026-06-02_1936.md`.
- The full broad-universe Claude-enabled repeat run was skipped after the dry run because the full yfinance scan took 26 minutes 31 seconds and would require repeating the same full scan before making any Claude calls.
- Full broad-universe dry-run qualified tickers were `HMY` and `CNC`; both remained un-analyzed by Claude because dry-run mode was enabled.
- The broad universe solves the discovery gap where non-S&P focus-list names could never enter the scanner.
- No Discord/X automation, social scraping, browser automation, TradingView automation, paid APIs, broker integration, dashboard, scheduler, database, or auto-trading was added.

### Today Focus Layer

- Added `today_focus.py` to distinguish general bullish technical setups from same-day focus-list candidates.
- Implemented `evaluate_today_focus(symbol, technicals)` to produce `today_focus_score`, `actionability`, trigger level, invalidation level, do-not-chase level, preferred entry style, same-day thesis, reasons, warnings, and disqualifiers.
- Implemented `batch_evaluate_today_focus()` for batch evaluation of precomputed technical dictionaries.
- Actionability classes are `ready_today`, `breakout_only`, `pullback_only`, `needs_more_time`, and `avoid`.
- Today Focus scoring uses EMA context, ignition freshness, accumulation/distribution, OBV trend, nearby support/invalidation, resistance trigger proximity, demand/supply context, extension from EMA8/EMA21, and volume anomalies.
- `main.py` now computes `TodayFocusScore` for technically scored candidates; final selection later became a three-factor score after the Focus Structure Layer was added.
- `main.py` attaches `today_focus` to each Claude analysis and includes the deterministic today focus context in the Claude prompt input.
- Claude analysis is now capped by `MAX_AI_ANALYSES = 10` by default after the Focus Structure quality-gate upgrade.
- Verified `venv/bin/python today_focus.py` with MSFT, NVDA, F, and NKE live data.
- Verified full `venv/bin/python main.py`; live run found 149 candidates, scored 50, selected 5 for Claude, and saved a daily focus-list report.
- Tightened Today Focus calibration so `ready_today` now requires a nearby trigger or retest area plus reasonable invalidation.
- Added diagnostics to each Today Focus result for actionability debugging, including EMA extension, nearby trigger/retest checks, invalidation quality, supply/demand distances, ignition age, and stale impulse distance.
- Extended names without nearby breakout triggers are downgraded to `pullback_only` or `needs_more_time`.
- Severe extension from EMA8/EMA21 prevents `ready_today` and sets a do-not-chase reference.
- Stale bullish ignition plus a move far above the ignition close prevents `ready_today`.
- TodayFocusScore caps now align score with actionability: downgraded pullback-only, needs-more-time, avoid, and lower-quality breakout-only labels cannot keep misleadingly high scores.
- Re-verified `venv/bin/python today_focus.py`; F was classified `pullback_only` instead of `ready_today` due to stale ignition, extension, no nearby trigger, and no reasonable invalidation.
- Re-verified full `venv/bin/python main.py`; live run selected EMR, F, PLTR, MSFT, and HPQ, with F and HPQ downgraded to `pullback_only`, and saved `reports/premarket_report_2026-06-02_1610.md`.
- The system remains a same-day discretionary focus-list generator and decision-support tool only.
- No Discord/X automation, web scraping, TradingView browser automation, dashboarding, scheduling, broker integration, database, or auto-trading was added.

### Focus Structure Layer

- Added `focus_structure.py` to evaluate Sean-style focus-list chart structure separately from general technical strength and same-day actionability.
- Implemented `detect_recent_impulse()` to identify recent bullish/bearish impulse moves using multi-candle returns, wide-range bullish candles, body-to-range, and relative volume.
- Implemented `detect_controlled_digestion()` to evaluate post-impulse digestion using pullback depth, EMA21 hold, quieter volume, and bearish damage checks.
- Implemented `detect_compression()` to detect tightening using range contraction, lower/flat highs, rising/stable lows, and close volatility.
- Implemented `detect_volume_dryup()` to compare recent consolidation volume with prior participation and flag red-candle volume expansion.
- Implemented `evaluate_ema_structure()` to score EMA holding, stack regime, extension from EMA8/EMA21, and extension risk.
- Implemented `detect_trigger_and_invalidation()` to identify nearby trigger and invalidation references from support/resistance, recent highs, EMA21, and demand zones.
- Implemented `evaluate_focus_structure(symbol, df, technicals=None)` to return `FocusStructureScore`, `StructureType`, structural booleans, extension risk, verdict, reasons, warnings, disqualifiers, and diagnostics.
- Structure types include `trendline_compression`, `high_tight_flag`, `breakout_retest`, `ema_reclaim_base`, `extended_no_base`, `sloppy_chop`, and `no_clear_structure`.
- Main pipeline now computes `FocusStructureScore` and `StructureType` for each technically scored candidate.
- Final pre-AI scoring now uses `FinalPreAIScore = 0.25 * TechnicalPreAIScore + 0.35 * TodayFocusScore + 0.40 * FocusStructureScore`.
- Added `passes_focus_quality_gate()` so candidates must have same-day actionability, TodayFocusScore >= 70, FocusStructureScore >= 65, acceptable structure type, and a valid trigger/retest/invalidation path before Claude is called.
- `MAX_AI_ANALYSES = 10` is now a true maximum cap, not a target; some days may produce 0 qualified candidates and no Claude calls.
- If no candidates qualify, `main.py` saves a report saying no high-quality same-day focus-list setups passed the filters and includes scanned/scored/qualified/selected counts.
- Claude prompt now receives `focus_structure` context for qualified candidates and is instructed to respect the stricter structure layer.
- Verified `venv/bin/python focus_structure.py` with PLTR, MSFT, HPQ, F, ORCL, and EMR live data.
- Verification showed HPQ and ORCL classified as `extended_no_base`, F as low/no-clear structure, and PLTR receiving impulse/digestion/trigger credit without being over-scored because compression was not detected.
- Re-verified full `venv/bin/python main.py`; live run found 149 candidates, technically/focus scored 50, qualified 0 after focus gates, selected 0 for Claude, made 0 Claude calls, and saved `reports/premarket_report_2026-06-02_1818.md`.
- Added a focus gate audit CSV saved under `reports/` on each full `main.py` run.
- Audit rows include every technically/focus scored candidate with scores, actionability, structure type, diagnostics, pass/fail flag, and exact gate failure reasons.
- `passes_focus_quality_gate()` now delegates to `get_focus_gate_failure_reasons()` so gate pass/fail logic and audit reasons stay aligned.
- Added `build_focus_gate_audit_rows()` and `save_focus_gate_audit()` to produce CSV-ready rows and save timestamped files like `reports/focus_gate_audit_YYYY-MM-DD_HHMM.csv`.
- The audit is for calibration and debugging only; quality gates and strategy thresholds were not loosened.
- Claude remains called only on candidates that pass focus gates.
- Re-verified full `venv/bin/python main.py`; live run found 149 candidates, technically/focus scored 50, qualified 0 after focus gates, selected 0 for Claude, made 0 Claude calls, saved `reports/focus_gate_audit_2026-06-02_1834.csv`, and saved `reports/premarket_report_2026-06-02_1834.md`.
- Fixed Focus Structure classification consistency so high `FocusStructureScore` candidates can no longer remain `no_clear_structure` unless the score is capped below the focus gate.
- Added explicit `classify_structure_type()` and score-cap logic so classification is derived from impulse, digestion, compression/tightening, EMA hold, trigger/invalidation, EMA regime, and extension evidence after the evidence score is computed.
- `no_clear_structure`, `sloppy_chop`, and `extended_no_base` now have score caps aligned with the gate logic: `no_clear_structure <= 64`, `sloppy_chop <= 49`, and `extended_no_base <= 59`.
- High-score candidates with impulse, controlled digestion, compression or tightening evidence, and nearby trigger/invalidation now classify into valid structure types such as `trendline_compression`, `high_tight_flag`, `breakout_retest`, or `ema_reclaim_base` instead of falling through to `no_clear_structure`.
- Added `classification_reason`, `score_before_caps`, `score_after_caps`, and score-cap reason diagnostics to Focus Structure output.
- Expanded the focus gate audit CSV diagnostics with classification reason, score before/after caps, lower highs, higher lows, compression quality, digestion quality, and EMA quality.
- Quality gates were not broadly loosened; `main.py` still rejects `no_clear_structure`, `sloppy_chop`, and `extended_no_base`.
- Historical audit `reports/focus_gate_audit_2026-06-02_1936.csv` had 23 rows with `FocusStructureScore >= 65` and `StructureType == no_clear_structure`, including MBLY at 95.
- Targeted live verification after the fix reclassified MBLY from the contradictory high-score `no_clear_structure` pattern to `trendline_compression` with score 95; HUN, USAS, IOVA, AFRM, and B also classified into valid structure types from similar evidence.
- Re-verified `venv/bin/python focus_structure.py`; HPQ and ORCL stayed `extended_no_base`, F stayed low `no_clear_structure`, PLTR and MSFT received coherent valid labels with sub-gate scores, and EMR classified coherently as `trendline_compression` with a sub-gate score.
- Re-verified limited broad CLI dry run `venv/bin/python main.py --universe us_listed --max-universe-size 300 --max-candidates-to-score 50 --dry-run`; cleaned broad universe was 5,161, symbols scanned were 300, basic candidates found were 65, technically/focus scored count was 50, qualified-after-gates count was 4, selected-for-Claude count was 0, Claude calls were 0, runtime was 1 minute 12 seconds, audit saved to `reports/focus_gate_audit_2026-06-02_1953.csv`, and report saved to `reports/premarket_report_2026-06-02_1953.md`.
- New audit verification for `reports/focus_gate_audit_2026-06-02_1953.csv` showed zero rows with `no_clear_structure`, `extended_no_base`, or `sloppy_chop` and `FocusStructureScore >= 65`.
- The full broad-universe 200-candidate dry run was not repeated after this fix because the prior full dry run took 26 minutes 31 seconds; the required 300-symbol dry run passed and saved both audit and report outputs.
- Re-ran the full broad `us_listed` dry run after the Focus Structure classification consistency fix with `venv/bin/python main.py --universe us_listed --max-candidates-to-score 200 --dry-run`.
- Full post-fix broad dry run loaded 5,161 cleaned symbols, scanned 5,161 symbols, found 1,011 basic candidates, technically/focus scored 200 candidates, qualified 6 after focus gates, selected 0 for Claude, made 0 Claude calls, and completed in 17 minutes 29 seconds.
- Full post-fix broad dry run saved audit CSV `reports/focus_gate_audit_2026-06-02_2030.csv` and report `reports/premarket_report_2026-06-02_2030.md`.
- Full post-fix audit mismatch checks were all zero: `no_clear_structure` with `FocusStructureScore >= 65` was 0, `extended_no_base` with `FocusStructureScore >= 65` was 0, and `sloppy_chop` with `FocusStructureScore >= 65` was 0.
- Full post-fix qualified tickers were `HUN`, `MBLY`, `HMY`, `IOVA`, `CNC`, and `COLD`; prior full broad dry-run qualified tickers `HMY` and `CNC` remained qualified, with additional names now passing because high-scoring valid structures are no longer mislabeled as invalid structure types.
- Full post-fix gate failures are now more coherent: the largest failure reasons were low Today Focus score, low Focus Structure score after caps, disallowed actionability, and explicitly disqualified structure types.
- Full `us_listed` scanning is usable for manual full-universe verification, but still needs optimization before being treated as a practical daily premarket default because live yfinance scanning remains a 17-26 minute operation.
- Added an analyze-from-audit workflow in `main.py` so Claude analysis can be run from an existing focus gate audit CSV without rescanning the full universe.
- New CLI option `--analyze-from-audit` accepts a saved focus gate audit CSV path, filters rows where `PassedFocusGate` is true, sorts by `FinalPreAIScore` descending, caps by `--max-ai-analyses`, fetches fresh OHLCV only for selected tickers, rebuilds technicals, Today Focus, and Focus Structure context, and then either generates a deterministic-only report or calls Claude for only those selected tickers.
- Example command: `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --max-ai-analyses 10`.
- Analyze-from-audit dry-run mode works with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --dry-run`; it loaded 200 audit rows, found 6 passed candidates, selected `HUN`, `MBLY`, `HMY`, `IOVA`, `CNC`, and `COLD`, fetched fresh OHLCV only for those tickers, made 0 Claude calls, saved `reports/premarket_report_2026-06-02_2111.md`, and completed in 1 second.
- Analyze-from-audit Claude mode works with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --max-ai-analyses 10`; it loaded the same 6 passed candidates, avoided a full universe scan, made 6 Claude calls, saved `reports/premarket_report_2026-06-02_2114.md`, and completed in 2 minutes 40 seconds.
- During the analyze-from-audit Claude run, `MBLY`, `HMY`, `IOVA`, and `CNC` produced parsed Claude analysis; `HUN` and `COLD` returned invalid JSON after the existing repair retry and were included as explicit failed-analysis report entries instead of crashing the run.
- Re-verified normal scan behavior with `venv/bin/python main.py --universe sp500 --dry-run`; it used the existing scan path, scanned 247 S&P 500 sector-filtered symbols, found 149 basic candidates, technically/focus scored 50, qualified 0, selected 0 for Claude, made 0 Claude calls, saved `reports/focus_gate_audit_2026-06-02_2115.csv`, saved `reports/premarket_report_2026-06-02_2115.md`, and completed in 1 minute 7 seconds.
- Re-verified normal scan behavior again after the compact Claude-output/report changes with `venv/bin/python main.py --universe sp500 --dry-run`; it used the existing scan path, scanned 247 S&P 500 sector-filtered symbols, found 149 basic candidates, technically/focus scored 50, qualified 0, selected 0 for Claude, made 0 Claude calls, saved `reports/focus_gate_audit_2026-06-02_2135.csv`, saved `reports/premarket_report_2026-06-02_2135.md`, and completed in 1 minute 8 seconds.
- Re-verified normal scan behavior after adding Setup Judge v1 with `venv/bin/python main.py --universe sp500 --dry-run`; it used the existing scan path, scanned 247 S&P 500 sector-filtered symbols, found 149 basic candidates, technically/focus scored 50, qualified 0, selected 0 for Claude, made 0 Claude calls, saved `reports/focus_gate_audit_2026-06-02_2312.csv`, saved `reports/premarket_report_2026-06-02_2312.md`, and completed in 1 minute 7 seconds.
- The analyze-from-audit workflow solves the issue where a full 5,161-symbol yfinance universe scan had to be repeated before running Claude on already-qualified audit candidates.
- Added Option C sector ETF proxy leadership as scanner context using `XLK`, `XLE`, `XLC`, `XLI`, `XLY`, `XLF`, `XLRE`, `XLB`, `XLU`, `XLV`, and `XLP`.
- Sector leadership now calculates 1W, 1M, 3M, 6M, and 1Y ETF performance with weighted score `0.20 * 1W + 0.30 * 1M + 0.30 * 3M + 0.10 * 6M + 0.10 * 1Y`, normalizing weights when a lookback is missing.
- Sector leadership is report context only, not a hard gate; unknown sector metadata does not reject a stock.
- Added the primary hard universe gate as the scanner's first strategic tradability filter after available symbol rows are loaded and before chart/setup/focus/Claude analysis.
- Primary hard gate rules are price > SMA21, price > SMA50, price > $5, market cap > $300M, ATR14 > 1.5, and 20-day average volume > 1M.
- `main.py` now loads raw symbol rows without relying on the old weak `$3`, 500k-volume, SMA20, and dollar-volume filters as the strategic first-stage gate.
- Only primary hard-gate survivors reach `build_technical_shortlist()`, Today Focus, Focus Structure, chart/setup scoring, Setup Judge, or full Claude analysis.
- `scan_candidates()` now defaults to raw symbol-row loading; the old light price/volume/SMA filters remain available only for explicit legacy calls.
- `MAX_CANDIDATES_TO_SCORE` now defaults to no cap, so every hard-gate survivor is run through technical/focus scoring unless a caller explicitly passes `--max-candidates-to-score`.
- Reports now show `Symbols loaded for hard gate`, `Hard gate survivors`, and `Hard gate rejected` so the first real universe reduction is visible in the run output.
- Focus gate thresholds, scoring weights, Today Focus logic, Focus Structure logic, and Claude analysis logic were not loosened.
- Focus gate audit CSV rows now include primary gate diagnostics: `price`, `sma21`, `sma50`, `market_cap`, `atr`, `avg_volume`, per-rule pass booleans, `primary_gate_pass`, and `primary_gate_fail_reasons`.
- Audit rows now also include sector context fields: `sector_etf`, `sector_perf_1w`, `sector_perf_1m`, `sector_perf_3m`, `sector_perf_6m`, `sector_perf_1y`, `sector_score`, and `sector_rank`.
- Primary-gate rejected symbols are retained in the audit with `primary_gate_pass = false`, `PassedFocusGate = false`, explicit `primary_gate_failed` reasons, and no chart/setup/Claude analysis.
- Reports now include a Sector Leadership section near the top with top sectors, weak sectors, and the ETF score table.
- Candidate report output now includes sector ETF/rank/score context when available.
- Claude and Setup Judge context now receive primary gate and sector leadership fields, while deterministic gates and levels remain source of truth.
- Analyze-from-audit mode now rechecks the primary hard gate on selected audit tickers only; audit candidates that fail the current primary gate are skipped before Setup Judge or full Claude.
- Verified syntax/imports with `env PYTHONPYCACHEPREFIX=/private/tmp/trading_system_pycache venv/bin/python -m py_compile main.py sector_scanner.py stock_filter.py report.py claude_analyzer.py`.
- Verified small S&P 500 dry-run with `venv/bin/python main.py --universe sp500 --max-universe-size 20 --max-candidates-to-score 5 --dry-run`; it scanned 20 symbols, had 8 primary hard-gate survivors, 12 primary hard-gate rejects, technically scored 5, qualified 1 after focus gates, made 0 Claude calls, saved audit `reports/focus_gate_audit_2026-06-03_1105.csv`, and saved report `reports/premarket_report_2026-06-03_1105.md`.
- Verified hard-gate-first default scoring with `venv/bin/python main.py --universe sp500 --max-universe-size 10 --dry-run`; it loaded 10 symbols, had 6 primary hard-gate survivors, 4 primary hard-gate rejects, scored all 6 hard-gate survivors through technical/focus layers, qualified 1 after focus gates, made 0 Claude calls, saved audit `reports/focus_gate_audit_2026-06-03_1113.csv`, and saved report `reports/premarket_report_2026-06-03_1113.md`.
- Verified analyze-from-audit dry-run with `venv/bin/python main.py --analyze-from-audit reports/focus_gate_audit_2026-06-02_2030.csv --dry-run`; it loaded 6 prior passed candidates, rechecked the new primary hard gate, skipped HUN, MBLY, HMY, IOVA, and COLD before Claude due current hard-gate failures, retained CNC for deterministic-only reporting, made 0 Claude calls, and saved a report.
- Verified limited broad dry-run with `venv/bin/python main.py --universe us_listed --max-universe-size 50 --max-candidates-to-score 10 --dry-run`; it loaded 5,162 cleaned symbols, scanned the first 50 broad rows, had 7 primary hard-gate survivors, 43 primary hard-gate rejects, technically scored 7, qualified 1 after focus gates, made 0 Claude calls, saved audit `reports/focus_gate_audit_2026-06-03_1103.csv`, and saved report `reports/premarket_report_2026-06-03_1103.md`.
- No new APIs, automation, paid data sources, broker integration, database, dashboard, or auto-trading were added.
- This fix improves audit interpretability and prevents valid high-scoring structures from being rejected only because of contradictory labels.
- No Discord/X automation, browser automation, TradingView automation, broker integration, dashboard, scheduling, database, paid APIs, or auto-trading was added.

### Blueprint Fit Upgrade

- Added a dedicated Blueprint Fit layer in `focus_structure.py` that scores whether a candidate resembles the focus-list examples using named setup, impulse/base location, controlled digestion, compression or tight base, bullish volume or volume dry-up, EMA hold, nearby trigger/retest, nearby invalidation, and extension-risk checks.
- The focus-quality gate now rejects candidates with `BlueprintFitScore < 65`, hard blueprint-fit failures, or known weak sector alignment below 45.
- Final deterministic ranking now weights `BlueprintFitScore` and sector alignment directly: 15% technical, 25% Today Focus, 25% Focus Structure, 20% Blueprint Fit, and 15% Sector Alignment.
- Primary-gate enrichment now records stock 1W/1M/3M performance and relative strength versus the mapped sector ETF for 1M and 3M windows.
- Audit CSVs, reports, Claude context, and Setup Judge context now carry `BlueprintFitScore`, `BlueprintFitPass`, `BlueprintFitFailReasons`, stock performance, and sector-relative strength fields.
- Added `STRATEGY_HANDOFF.md` so ChatGPT can review what Codex implemented and decide the next strategy iteration from concrete audit fields.

### Post-Primary Detector Retrieval Layer

- Added `detector_models.py` with tag-based detector data models, explicit high-value tags, warning tags, CSV schema, JSON serialization, and confidence labels `LOW`, `MEDIUM`, and `HIGH`.
- Added `setup_detectors.py` as the new high-recall detector layer after the primary hard universe gate.
- Implemented loose detectors for inside days, tight-range compression, big bases near highs, right-side-of-base setups, possible accumulation bases, catalyst/power gaps, high relative volume, breakout proximity, confirmed breakouts, breakout retests, failed-breakdown/hammer reversals, bull flags/wedge compression, EMA trend/reclaims, extension/chase risk, trigger clarity, rough risk/reward viability, and failed-breakout warnings.
- Detector output is tag based, not a single strict setup score. `interest_rank` exists only for sorting and does not act as the main rejection mechanism.
- Candidate retention now keeps names when any high-value detector fires or when multiple medium-value detectors cluster, unless obvious reject conditions fire.
- Obvious reject conditions are limited to no meaningful setup tags, severe extension without a fresh catalyst/breakout/retest/reclaim, major failed breakout with heavy red breakdown behavior, no actionable trigger cluster when no high-value setup exists, or stop too wide without a compensating high-value setup.
- Added `detector_report.py` to save `detector_candidates_YYYY-MM-DD_HHMM.csv`, `detector_report_YYYY-MM-DD_HHMM.md`, and `detector_candidates_YYYY-MM-DD_HHMM.json`.
- Detector CSV rows include ticker, company, sector, close, volume, average volume, relative volume, detector count, detector names, detector confidence summary, detector tags, high-value tags, warning tags, setup family, trigger level, stop/reference level, interest rank, chart-needed flag, reject reason, notes, and optional chart paths.
- Detector Markdown reports include primary-gated count, detector-hit count, obvious-reject count, chart-review candidate count, and top candidates grouped by Leaders near highs, Right-side/base setups, Inside-day compression, Breakout/retest, Possible accumulation/emerging reclaim, Power gap/catalyst gap, and High RVOL unusual activity.
- `main.py` now runs the detector stage immediately after `apply_primary_universe_gate_to_candidates()` and before the existing technical/focus/Claude shortlist path.
- The existing strict Focus Structure gate and Claude analysis path are unchanged by detector retention; the detector layer is an additive visual-review retrieval audit.
- Added detector-only CLI mode: `venv/bin/python main.py --detectors-from-audit reports/focus_gate_audit_YYYY-MM-DD_HHMM.csv --output-dir reports`.
- Added optional detector chart generation with `--generate-detector-charts` and `--detector-chart-limit N`.
- Extended `chart_generator.py` with `generate_detector_chart_set()` for standardized daily 6M and 1Y charts with volume, EMA8/EMA21/EMA50, trigger level, and stop/reference level overlays.
- Detector chart paths are written back into CSV/JSON when generated. Tags are kept in the filename while chart titles stay short and readable.
- Verified syntax/imports with `env PYTHONPYCACHEPREFIX=/private/tmp/trading_system_pycache venv/bin/python -m py_compile main.py setup_detectors.py detector_models.py detector_report.py chart_generator.py`.
- Verified detector-only mode with `venv/bin/python main.py --detectors-from-audit reports/focus_gate_audit_2026-06-03_1105.csv --output-dir reports`; it loaded 8 primary-gate audit rows, evaluated 8 detector candidates, marked 8 chart-needed, had 0 obvious rejects, and saved detector CSV/report/JSON.
- Verified normal scanner integration with `venv/bin/python main.py --universe sp500 --max-universe-size 10 --max-candidates-to-score 5 --dry-run --output-dir reports`; it scanned 10 symbols, had 5 primary hard-gate survivors, ran detector outputs on those 5, then continued through the existing technical/focus path with 0 Claude calls.
- Verified chart integration with `venv/bin/python main.py --detectors-from-audit reports/focus_gate_audit_2026-06-03_1105.csv --output-dir reports --generate-detector-charts --detector-chart-limit 1`; it generated one 6M and one 1Y detector chart set under `charts/detectors/` and wrote the chart paths into detector CSV/JSON.
- Latest validation artifacts include `reports/detector_candidates_2026-06-03_1940.csv`, `reports/detector_report_2026-06-03_1940.md`, `reports/detector_candidates_2026-06-03_1940.json`, and detector charts under `charts/detectors/`.
- Next calibration step is to inspect detector CSV/report output visually, then tune thresholds or add/remove high-value tags based on missed good charts and false positives.
- Claude visual review should consume `chart_needed = true` detector candidates later; the detector layer is not intended to be the final trader.
- No broker integration, order placement, dashboard, scheduler, database, paid data source, Discord/X scraping, TradingView automation, or auto-trading was added.

### Optional Visual Chart Review Layer

- Added `chart_generator.py` to generate static local candlestick chart PNGs from OHLCV data.
- Chart generation uses `mplfinance` and `matplotlib`, includes daily candles, volume, and EMA8/EMA21/EMA50 overlays.
- Chart images are saved under `charts/` with filenames like `SYMBOL_YYYY-MM-DD.png`.
- Added `vision_reviewer.py` for LLM-based visual chart setup review using local chart image files as inputs.
- Vision review is currently implemented with Anthropic because the project already has Anthropic API setup, while keeping image encoding and prompt construction modular for future provider changes.
- Vision review evaluates focus-list chart quality, impulse, consolidation, EMA structure, volume read, extension risk, trigger/invalidation clarity, and final visual verdict.
- `main.py` now has `USE_VISION_REVIEW = False` and `MAX_VISION_REVIEWS = 5`; default MVP runs do not generate charts or call a vision model.
- When enabled manually later, `main.py` can attach a `vision_review` dictionary to each analysis result for report display without changing ranking yet.
- `report.py` now displays an optional Visual Chart Review section when `vision_review` exists and stays unchanged when absent.
- Updated `requirements.txt` with `matplotlib` and `mplfinance`.
- Verified `venv/bin/python chart_generator.py`, which saved `charts/MSFT_2026-06-02.png`.
- Verified `venv/bin/python vision_reviewer.py`, which generated a chart, sent one vision request, and returned structured JSON.
- Verified default `venv/bin/python main.py` still runs with vision disabled and saves a report under `reports/`.
- No Discord scraping, browser automation, dashboard, scheduling, broker integration, database, prompt caching, or auto-trading was added.
- The visual layer remains decision support only for evaluating chart quality.

## Next Module

Next practical step: review the detector CSV/Markdown output and generated charts, then tune detector thresholds and chart-review handoff rules from observed false positives/misses. The detector layer should stay high-recall until enough chart-review evidence justifies tightening.

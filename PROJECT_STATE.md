# trading_system Project State

Last updated: 2026-06-02

## Architecture Status

- Module 1: Data Fetcher - complete and verified
- Module 2: Sector Scanner - complete and verified
- Module 3: Stock Filter - complete and verified
- Module 4: Technical Analyzer - complete and verified
- Module 5: Claude Analysis Layer - complete and verified
- Module 6: Report Generator - complete and verified
- Module 7: Main Orchestrator - complete and verified
- Today Focus Layer - complete and verified
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

### Module 7: Main Orchestrator

- Implemented `MAX_AI_ANALYSES = 5` as the default Claude cost-control cap.
- Implemented `score_candidate_pre_ai()` to rank Module 3 candidates deterministically using price above SMA20, distance from SMA20, and 20-day average volume.
- Implemented `select_candidates_for_ai()` to add `PreAIScore`, sort candidates, and select only the top capped candidates for Claude analysis.
- Implemented `run_premarket_scan()` to connect sector scanning, stock filtering, candidate pre-selection, OHLCV fetching, Claude AI analysis, and Markdown report generation.
- Implemented `main()` and the CLI block so the MVP can run with `venv/bin/python main.py`.
- The MVP pipeline now runs end-to-end and saves a Markdown premarket report under `reports/`.
- Default Claude calls are capped by `MAX_AI_ANALYSES = 5`; live verification found 138 candidates and selected 5 for AI analysis.
- Individual ticker failures are caught and do not crash the full report run.
- The system remains a decision-support tool only, not an auto-trader.
- No broker integration, order placement, scheduling, dashboarding, database, or prompt caching was added.
- Verified live with `venv/bin/python main.py`.
- Re-verified after AI failure-handling cleanup with `venv/bin/python main.py`; full pipeline still saves a report under `reports/`.
- Local retry tests confirmed that incomplete Claude output triggers exactly one repair attempt and falls back to a standardized failed analysis if repair also fails.
- Added `MAX_CANDIDATES_TO_SCORE = 50` so the basic candidate list is reduced before technical scoring for MVP speed.
- Added `score_technicals_pre_ai()` to score candidates using Module 4 features before spending Claude calls.
- Technical pre-AI scoring uses EMA regime, ignition candle direction, accumulation/distribution, OBV trend, support/resistance, demand/supply context, volume anomalies, and extension from EMA8.
- Added `build_technical_shortlist()` to fetch OHLCV, run `analyze_stock_technicals()`, compute `TechnicalPreAIScore`, and select the final Claude shortlist.
- `run_premarket_scan()` now sends Claude only the top `MAX_AI_ANALYSES = 5` candidates by `TechnicalPreAIScore`, reusing precomputed technical dictionaries.
- Live verification after technical-shortlist upgrade found 138 candidates, technically scored 50, selected 5, and saved a report under `reports/`.
- The MVP remains decision-support only, with no dashboard, scheduling, broker integration, database, prompt caching, or auto-trading added.

### Today Focus Layer

- Added `today_focus.py` to distinguish general bullish technical setups from same-day focus-list candidates.
- Implemented `evaluate_today_focus(symbol, technicals)` to produce `today_focus_score`, `actionability`, trigger level, invalidation level, do-not-chase level, preferred entry style, same-day thesis, reasons, warnings, and disqualifiers.
- Implemented `batch_evaluate_today_focus()` for batch evaluation of precomputed technical dictionaries.
- Actionability classes are `ready_today`, `breakout_only`, `pullback_only`, `needs_more_time`, and `avoid`.
- Today Focus scoring uses EMA context, ignition freshness, accumulation/distribution, OBV trend, nearby support/invalidation, resistance trigger proximity, demand/supply context, extension from EMA8/EMA21, and volume anomalies.
- `main.py` now computes `TodayFocusScore` for technically scored candidates and selects the Claude shortlist using `FinalPreAIScore = 0.45 * TechnicalPreAIScore + 0.55 * TodayFocusScore`.
- `main.py` attaches `today_focus` to each Claude analysis and includes the deterministic today focus context in the Claude prompt input.
- Claude analysis remains capped by `MAX_AI_ANALYSES = 5` by default.
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

MVP complete. Future modules should be added only when explicitly requested.

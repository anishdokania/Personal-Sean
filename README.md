# Personal-Sean

## Daily Watchlist (current, no AI)

`daily_watchlist.py` is the daily driver. It rebuilds the U.S.-listed universe
from scratch each run, screens it with fixed rules, and emails a watchlist.
No API key, no LLM, no cost — the same input always produces the same list.

```bash
python daily_watchlist.py              # scan, save, email
python daily_watchlist.py --no-email   # scan and save only
python daily_watchlist.py --limit 300  # quick partial scan
```

Every threshold that decides what lands on the list is a constant at the top of
`daily_watchlist.py`. The stages:

1. **Universe** — all U.S.-listed common stocks, rebuilt fresh each run.
2. **Liquidity** — price > $5, 20d avg volume > 1M, 20d avg dollar volume > $20M.
3. **Movement** — ADR% (20d) ≥ 5%.
4. **Trend** — close above the 50 EMA, 21 EMA rising.
5. **Setup** — each survivor is either `TRIGGERED` (undercut-and-reclaim fired on
   the last bar, with a swing-high target ≥ 1R away) or `FORMING` (pulling into
   the 8 EMA). Anything extended past 2 ADR above the 8 EMA is dropped.

Scheduled weekdays at 6 AM America/Chicago by
`.github/workflows/daily-watchlist.yml`. It needs only the SMTP secrets.

Rules are covered by offline tests — no network needed:

```bash
python -m unittest test_daily_watchlist -v
```

## Legacy: Chart AI Scanner (LLM, no longer scheduled)

An end-to-end, decision-support scanner for U.S. equities. It scans sectors,
filters stocks, runs deterministic technical detectors, layers on Claude
analysis, and produces a Markdown report (optionally emailed each morning).

> **It does not place orders or auto-trade.** It is a research and
> chart-triage tool that surfaces candidates for a human to review.

## What it does

The pipeline (`main.py`) chains a series of modules:

1. **Data fetch** (`data_fetcher.py`) — pulls price/volume via `yfinance`.
2. **Sector scan** (`sector_scanner.py`) — ranks sector strength.
3. **Stock filter** (`stock_filter.py`) — narrows the universe on liquidity/trend criteria.
4. **Technical + detectors** (`technical.py`, `setup_detectors.py`, `focus_structure.py`) — deterministic setup detection and structure scoring.
5. **Claude analysis** (`claude_analyzer.py`) — LLM review of the shortlisted setups.
6. **Haiku chart triage** (`haiku_chart_triage.py`) — optional fast visual pass over generated charts (disabled by default).
7. **Report** (`report.py`, `daily_report_email.py`) — Markdown output and optional SMTP email delivery.

See `PROJECT_STATE.md` and `STRATEGY_HANDOFF.md` for module-by-module status
and strategy notes.

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`:

- `ANTHROPIC_API_KEY` — required for the Claude analysis stages.
- `SMTP_*` and `REPORT_EMAIL_*` — only needed if you want emailed reports.
- `DAILY_REPORT_*` — universe and run-size defaults.

> **Never commit your real `.env`.** It is already git-ignored; only
> `.env.example` (with blank values) is tracked.

## Run

```bash
python main.py
```

Generate the daily emailed report:

```bash
python daily_report_email.py
```

## Ask questions about this project (local RAG)

`rag_assistant.py` is a small, local retrieval-augmented assistant over this
repo's own docs and source. Retrieval is fully local (a TF-IDF index built with
`numpy`, stored git-ignored in `.rag_index/`); only answer synthesis calls
Claude.

```bash
python rag_assistant.py index                 # build/refresh the local index
python rag_assistant.py ask "How is the report emailed?"
python rag_assistant.py chat                  # interactive session
```

Answers cite the source files and line ranges they draw from. Set `RAG_MODEL`
(or pass `-m`) to change the Claude model; re-run `index` after code changes.

## Notes

- Yahoo (`yfinance`) data is EOD/delayed and rate-limited; the scanner is
  designed around daily chart triage, not intraday execution.
- The Haiku chart-triage and visual-review layers are off by default and can
  be enabled via the `DAILY_REPORT_HAIKU_*` settings.

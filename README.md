# Personal-Sean — Daily Chart AI Scanner

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

## Notes

- Yahoo (`yfinance`) data is EOD/delayed and rate-limited; the scanner is
  designed around daily chart triage, not intraday execution.
- The Haiku chart-triage and visual-review layers are off by default and can
  be enabled via the `DAILY_REPORT_HAIKU_*` settings.

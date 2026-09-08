"""
Daily deterministic watchlist.

No LLM, no API keys, no judgment calls. The same rules run every day and the
same input always produces the same list. Everything the strategy does is a
constant at the top of this file.

The pipeline:

  1. Universe   - the full U.S.-listed common-stock list, rebuilt fresh each run
                  (no hardcoded ticker list, so today's movers can appear and
                  yesterday's can drop out).
  2. Liquidity  - price, volume, dollar volume floors: is it tradeable at all.
  3. Movement   - ADR% floor: does it move enough to be worth a tight stop.
  4. Trend      - above the 50 EMA with a rising 21 EMA: constructive only.
  5. Setup      - each survivor lands in exactly one bucket:
                    TRIGGERED - undercut-and-reclaim fired on the latest bar
                    FORMING   - pulling into the 8 EMA, not yet reclaimed
                    (anything extended or unclear is dropped)
  6. Rank/emit  - fixed ranking, Markdown + CSV, emailed.

Entry/stop/target levels are printed because they are free to compute, but the
deliverable is the list. Nothing here places orders.

Run:
    python daily_watchlist.py                 # scan, save, email
    python daily_watchlist.py --no-email      # scan and save only
    python daily_watchlist.py --limit 300     # quick partial scan
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

from backtest.adr import adr_abs, adr_pct
from backtest.config import BacktestConfig
from backtest.entry_models import detect_entry
from backtest.signals import nearest_swing_high_above
from email_report import send_report_email
from universe import load_us_listed_universe


# --------------------------------------------------------------------------- #
# THE STRATEGY. Every number that decides what lands on the list lives here.
# --------------------------------------------------------------------------- #

# Stage 2 - liquidity floors.
MIN_PRICE = 5.0
MIN_AVG_VOLUME_20D = 1_000_000
MIN_AVG_DOLLAR_VOLUME_20D = 20_000_000

# Stage 3 - movement floor. A tight stop only pays on stocks that range.
MIN_ADR_PCT = 5.0
ADR_LOOKBACK = 20

# Stage 4 - trend. Constructive long context only.
TREND_EMA_SLOW = 50          # close must be above this
TREND_EMA_MID = 21           # this must be rising
TREND_EMA_RISING_BARS = 5    # ...versus its value N bars ago
TREND_EMA_FAST = 8           # the snipe reference

# Stage 5 - setup buckets, measured in ADRs from the 8 EMA.
FORMING_MAX_ADR_ABOVE_EMA8 = 1.0   # within this of the 8 EMA = pulling in
FORMING_MAX_ADR_BELOW_EMA8 = 0.5   # slightly under is fine; deeper is damage
EXTENDED_ADR_ABOVE_EMA8 = 2.0      # beyond this = chase, dropped

# Target/risk sanity for TRIGGERED names.
SWING_PIVOT_K = 3
SWING_LOOKBACK = 40
MIN_RR = 1.0
MAX_RISK_PCT_OF_PRICE = 15.0

# Data + output.
HISTORY_DAYS = 400           # ~1.5y of bars: enough for EMA50 and 3M returns
MIN_BARS_REQUIRED = 80
DOWNLOAD_CHUNK = 120         # tickers per batched yfinance request
MAX_TRIGGERED_IN_EMAIL = 25
MAX_FORMING_IN_EMAIL = 25
OUTPUT_DIR = "reports"

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


@dataclass
class WatchRow:
    """One name on the watchlist."""

    symbol: str
    bucket: str            # TRIGGERED | FORMING
    setup: str
    close: float
    adr_pct: float
    dist_ema8_adr: float   # +ve = above the 8 EMA, in ADRs
    perf_1w: Optional[float]
    perf_1m: Optional[float]
    perf_3m: Optional[float]
    avg_dollar_volume: float
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    risk_pct: Optional[float]
    rr: Optional[float]
    rank_score: float


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def _normalize_frame(df: Any) -> Optional[pd.DataFrame]:
    """Coerce one ticker's yfinance slice into a clean OHLCV frame."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None

    frame = df.copy()
    frame.columns = [str(c).title() for c in frame.columns]
    if any(col not in frame.columns for col in OHLCV):
        return None

    out = frame.loc[:, OHLCV].apply(pd.to_numeric, errors="coerce").dropna()
    if out.empty:
        return None
    out.index = pd.to_datetime(out.index)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _split_batch(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a grouped multi-ticker yfinance download into per-symbol frames."""
    frames: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return frames

    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        for symbol in symbols:
            if symbol not in available:
                continue
            normalized = _normalize_frame(raw[symbol])
            if normalized is not None:
                frames[symbol] = normalized
    elif len(symbols) == 1:
        normalized = _normalize_frame(raw)
        if normalized is not None:
            frames[symbols[0]] = normalized
    return frames


def download_history(
    symbols: list[str], history_days: int = HISTORY_DAYS
) -> dict[str, pd.DataFrame]:
    """Batch-download daily bars. Batching is what makes a full scan practical."""
    frames: dict[str, pd.DataFrame] = {}
    chunks = [
        symbols[i : i + DOWNLOAD_CHUNK]
        for i in range(0, len(symbols), DOWNLOAD_CHUNK)
    ]
    for index, chunk in enumerate(chunks, 1):
        try:
            raw = yf.download(
                chunk,
                period=f"{history_days}d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            frames.update(_split_batch(raw, chunk))
        except Exception as exc:
            print(f"  ! batch {index}/{len(chunks)} failed: {exc}", flush=True)
        print(
            f"  batch {index}/{len(chunks)}: {len(frames)} symbols with data",
            flush=True,
        )
    return frames


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def _ema(closes: pd.Series, span: int) -> pd.Series:
    return closes.ewm(span=span, adjust=False).mean()


def _pct_change_over_bars(closes: pd.Series, bars: int) -> Optional[float]:
    if len(closes) <= bars:
        return None
    latest = float(closes.iloc[-1])
    prior = float(closes.iloc[-bars - 1])
    if not np.isfinite(latest) or not np.isfinite(prior) or prior == 0:
        return None
    return (latest / prior - 1.0) * 100.0


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
def evaluate(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    funnel: Optional[dict[str, int]] = None,
) -> Optional[WatchRow]:
    """Run the full rule stack on one symbol's latest bar. None = not a candidate.

    Pass `funnel` (a stage -> count dict) to record where each symbol dropped
    out. That is how you tell a threshold that is too tight from one that is
    too loose without guessing.
    """

    def drop(stage: str) -> None:
        if funnel is not None:
            funnel[stage] = funnel.get(stage, 0) + 1

    drop("evaluated")

    if df is None or len(df) < MIN_BARS_REQUIRED:
        drop("fail_history")
        return None

    closes = df["Close"]
    close = float(closes.iloc[-1])
    if not np.isfinite(close) or close <= MIN_PRICE:
        drop("fail_price")
        return None

    # Stage 2 - liquidity.
    avg_volume = float(df["Volume"].tail(20).mean())
    if not np.isfinite(avg_volume) or avg_volume < MIN_AVG_VOLUME_20D:
        drop("fail_volume")
        return None
    avg_dollar_volume = avg_volume * close
    if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME_20D:
        drop("fail_dollar_volume")
        return None

    # Stage 3 - movement.
    adrp = adr_pct(df, ADR_LOOKBACK)
    adr = adr_abs(df, ADR_LOOKBACK)
    if adrp is None or adr is None or adrp < MIN_ADR_PCT:
        drop("fail_adr")
        return None

    # Stage 4 - trend.
    ema_slow = float(_ema(closes, TREND_EMA_SLOW).iloc[-1])
    ema_mid_series = _ema(closes, TREND_EMA_MID)
    ema_mid = float(ema_mid_series.iloc[-1])
    ema_mid_prior = float(ema_mid_series.iloc[-(TREND_EMA_RISING_BARS + 1)])
    ema_fast = float(_ema(closes, TREND_EMA_FAST).iloc[-1])
    if close <= ema_slow or ema_mid < ema_mid_prior:
        drop("fail_trend")
        return None

    dist_ema8_adr = (close - ema_fast) / adr
    if dist_ema8_adr > EXTENDED_ADR_ABOVE_EMA8:
        drop("fail_extended")
        return None  # extended: this is a chase, not a watchlist name

    perf_1w = _pct_change_over_bars(closes, 5)
    perf_1m = _pct_change_over_bars(closes, 21)
    perf_3m = _pct_change_over_bars(closes, 63)

    def build(
        bucket: str,
        setup: str,
        entry: Optional[float],
        stop: Optional[float],
        target: Optional[float],
        rank_score: float,
    ) -> WatchRow:
        risk_pct = (
            100.0 * (entry - stop) / entry
            if entry and stop and entry > 0
            else None
        )
        rr = (
            (target - entry) / (entry - stop)
            if entry and stop and target and entry > stop
            else None
        )
        return WatchRow(
            symbol=symbol,
            bucket=bucket,
            setup=setup,
            close=round(close, 2),
            adr_pct=round(adrp, 1),
            dist_ema8_adr=round(dist_ema8_adr, 2),
            perf_1w=round(perf_1w, 1) if perf_1w is not None else None,
            perf_1m=round(perf_1m, 1) if perf_1m is not None else None,
            perf_3m=round(perf_3m, 1) if perf_3m is not None else None,
            avg_dollar_volume=round(avg_dollar_volume, 0),
            entry=round(entry, 2) if entry else None,
            stop=round(stop, 2) if stop else None,
            target=round(target, 2) if target else None,
            risk_pct=round(risk_pct, 1) if risk_pct is not None else None,
            rr=round(rr, 1) if rr is not None else None,
            rank_score=round(rank_score, 2),
        )

    # Stage 5a - TRIGGERED: an undercut-and-reclaim fired on the latest bar.
    entry_result = detect_entry(df, config)
    if entry_result is not None:
        entry = entry_result.reclaim_level
        stop = entry_result.undercut_low - config.snipe_stop_buffer_adr * adr
        risk = entry - stop
        if risk > 0 and entry < close:
            risk_pct = 100.0 * risk / entry
            target = nearest_swing_high_above(df, entry, SWING_PIVOT_K, SWING_LOOKBACK)
            if target is None:
                drop("reclaim_no_target")
            elif (target - entry) < MIN_RR * risk:
                drop("reclaim_below_min_rr")
            elif risk_pct > MAX_RISK_PCT_OF_PRICE:
                drop("reclaim_risk_too_wide")
            else:
                # Rank triggered names by reward:risk. Nothing subjective.
                drop("triggered")
                return build(
                    "TRIGGERED",
                    entry_result.entry_type,
                    entry,
                    stop,
                    target,
                    (target - entry) / risk,
                )

    # Stage 5b - FORMING: pulling into the 8 EMA but not reclaimed yet.
    if -FORMING_MAX_ADR_BELOW_EMA8 <= dist_ema8_adr <= FORMING_MAX_ADR_ABOVE_EMA8:
        # Rank by tightness to the 8 EMA first, then by 1M strength.
        tightness = FORMING_MAX_ADR_ABOVE_EMA8 - abs(dist_ema8_adr)
        strength = (perf_1m or 0.0) / 100.0
        drop("forming")
        return build("FORMING", "ema8_pullback", None, None, None, tightness + strength)

    drop("fail_not_near_ema8")
    return None


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _fmt(value: Optional[float], suffix: str = "") -> str:
    return "-" if value is None else f"{value}{suffix}"


def _table(rows: list[WatchRow], columns: list[tuple[str, Any]]) -> str:
    header = "| " + " | ".join(name for name, _ in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, divider]
    for row in rows:
        lines.append("| " + " | ".join(str(fn(row)) for _, fn in columns) + " |")
    return "\n".join(lines)


def render_markdown(rows: list[WatchRow], stats: dict[str, Any]) -> str:
    triggered = [r for r in rows if r.bucket == "TRIGGERED"][:MAX_TRIGGERED_IN_EMAIL]
    forming = [r for r in rows if r.bucket == "FORMING"][:MAX_FORMING_IN_EMAIL]
    as_of = stats.get("as_of", "unknown")

    parts = [
        f"# Daily Watchlist - {stats.get('run_date')}",
        "",
        f"Data as of the close on **{as_of}**. Deterministic rules only, no AI.",
        "",
        "## Counts",
        "",
        f"- Universe loaded: {stats.get('universe_count')}",
        f"- Symbols with usable data: {stats.get('with_data')}",
        f"- Passed all rules: {stats.get('candidates')}",
        f"- Triggered: {stats.get('triggered_count')} | Forming: {stats.get('forming_count')}",
        f"- Runtime: {stats.get('runtime')}",
        "",
    ]

    funnel = stats.get("funnel") or {}
    if funnel:
        # Where names dropped out. This is the tuning dial: a stage that eats
        # almost everything is the threshold to revisit first.
        # Terminal stages partition the evaluated set: every symbol lands in
        # exactly one. These counts sum to `evaluated`.
        terminal = [
            ("fail_history", "not enough history"),
            ("fail_price", "price too low"),
            ("fail_volume", "share volume too low"),
            ("fail_dollar_volume", "dollar volume too low"),
            ("fail_adr", "ADR% too low (too quiet)"),
            ("fail_trend", "not in an uptrend"),
            ("fail_extended", "extended above the 8 EMA"),
            ("fail_not_near_ema8", "healthy but not near the 8 EMA"),
            ("triggered", "-> TRIGGERED"),
            ("forming", "-> FORMING"),
        ]
        # Near misses overlap the terminal stages on purpose: a reclaim that
        # fails one of these can still qualify as FORMING, so it is counted in
        # both. They do not sum to `evaluated`.
        near_miss = [
            ("reclaim_no_target", "reclaimed, but no overhead swing high"),
            ("reclaim_below_min_rr", "reclaimed, but target under 1R"),
            ("reclaim_risk_too_wide", "reclaimed, but stop too wide"),
        ]
        parts += ["## Where names dropped out", ""]
        parts.append(f"- evaluated: {funnel.get('evaluated', 0)}")
        for key, label in terminal:
            if funnel.get(key):
                parts.append(f"- {label}: {funnel[key]}")
        if any(funnel.get(key) for key, _ in near_miss):
            parts += ["", "Near misses on the reclaim entry (overlaps the above):", ""]
            for key, label in near_miss:
                if funnel.get(key):
                    parts.append(f"- {label}: {funnel[key]}")
        parts.append("")

    parts += ["## Triggered - reclaim fired on the last bar", ""]
    if triggered:
        parts.append(
            _table(
                triggered,
                [
                    ("Symbol", lambda r: r.symbol),
                    ("Setup", lambda r: r.setup),
                    ("Close", lambda r: r.close),
                    ("Entry", lambda r: _fmt(r.entry)),
                    ("Stop", lambda r: _fmt(r.stop)),
                    ("Target", lambda r: _fmt(r.target)),
                    ("Risk%", lambda r: _fmt(r.risk_pct, "%")),
                    ("R:R", lambda r: _fmt(r.rr)),
                    ("ADR%", lambda r: f"{r.adr_pct}%"),
                    ("1M", lambda r: _fmt(r.perf_1m, "%")),
                ],
            )
        )
    else:
        parts.append("_None today._")

    parts += ["", "## Forming - pulling into the 8 EMA", ""]
    if forming:
        parts.append(
            _table(
                forming,
                [
                    ("Symbol", lambda r: r.symbol),
                    ("Close", lambda r: r.close),
                    ("From 8EMA", lambda r: f"{r.dist_ema8_adr} ADR"),
                    ("ADR%", lambda r: f"{r.adr_pct}%"),
                    ("1W", lambda r: _fmt(r.perf_1w, "%")),
                    ("1M", lambda r: _fmt(r.perf_1m, "%")),
                    ("3M", lambda r: _fmt(r.perf_3m, "%")),
                ],
            )
        )
    else:
        parts.append("_None today._")

    parts += [
        "",
        "## The rules",
        "",
        f"- Price > ${MIN_PRICE:.0f}, 20d avg volume > {MIN_AVG_VOLUME_20D:,}, "
        f"20d avg dollar volume > ${MIN_AVG_DOLLAR_VOLUME_20D:,}",
        f"- ADR% (20d) >= {MIN_ADR_PCT}%",
        f"- Close above the {TREND_EMA_SLOW} EMA, {TREND_EMA_MID} EMA rising over "
        f"{TREND_EMA_RISING_BARS} bars",
        f"- Dropped if more than {EXTENDED_ADR_ABOVE_EMA8} ADR above the "
        f"{TREND_EMA_FAST} EMA (chase)",
        "- TRIGGERED: bar undercut the prior low / 8 EMA / 21 EMA and closed back "
        "above it, with a swing-high target at least 1R away",
        f"- FORMING: within {FORMING_MAX_ADR_ABOVE_EMA8} ADR above to "
        f"{FORMING_MAX_ADR_BELOW_EMA8} ADR below the {TREND_EMA_FAST} EMA",
        "",
        "Research only. No orders are placed.",
        "",
    ]
    return "\n".join(parts)


def save_outputs(rows: list[WatchRow], markdown: str, output_dir: str) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")

    report_path = directory / f"watchlist_{stamp}.md"
    report_path.write_text(markdown, encoding="utf-8")

    csv_path = directory / f"watchlist_{stamp}.csv"
    frame = pd.DataFrame([asdict(r) for r in rows]) if rows else pd.DataFrame(
        columns=[f.name for f in WatchRow.__dataclass_fields__.values()]
    )
    frame.to_csv(csv_path, index=False)
    return {"report": str(report_path), "csv": str(csv_path)}


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def build_watchlist(
    limit: Optional[int] = None, history_days: int = HISTORY_DAYS
) -> tuple[list[WatchRow], dict[str, Any]]:
    started = time.perf_counter()

    print("Loading U.S.-listed universe...", flush=True)
    universe = load_us_listed_universe(include_etfs=False, common_stock_only=True)
    symbols = [str(s).strip().upper() for s in universe["Symbol"].tolist() if str(s).strip()]
    if limit:
        symbols = symbols[:limit]
    print(f"Universe loaded: {len(symbols)} symbols", flush=True)

    print("Downloading daily bars...", flush=True)
    frames = download_history(symbols, history_days=history_days)
    print(f"Symbols with usable data: {len(frames)}", flush=True)

    print("Applying rules...", flush=True)
    config = BacktestConfig(adr_lookback=ADR_LOOKBACK, min_adr_pct=MIN_ADR_PCT)
    rows: list[WatchRow] = []
    funnel: dict[str, int] = {}
    for symbol, frame in frames.items():
        try:
            row = evaluate(symbol, frame, config, funnel=funnel)
        except Exception as exc:
            print(f"  ! {symbol} skipped: {exc}", flush=True)
            continue
        if row is not None:
            rows.append(row)

    # TRIGGERED always sorts above FORMING; within a bucket, by rank score.
    rows.sort(key=lambda r: (r.bucket != "TRIGGERED", -r.rank_score))

    as_of = "unknown"
    if frames:
        as_of = max(f.index[-1] for f in frames.values()).date().isoformat()

    elapsed = time.perf_counter() - started
    stats = {
        "run_date": datetime.now().strftime("%Y-%m-%d"),
        "as_of": as_of,
        "universe_count": len(symbols),
        "with_data": len(frames),
        "candidates": len(rows),
        "triggered_count": sum(1 for r in rows if r.bucket == "TRIGGERED"),
        "forming_count": sum(1 for r in rows if r.bucket == "FORMING"),
        "runtime": f"{int(elapsed // 60)}m {int(elapsed % 60)}s",
        "funnel": funnel,
    }
    return rows, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and email the daily watchlist.")
    parser.add_argument("--limit", type=int, default=None, help="Cap universe size (testing).")
    parser.add_argument("--no-email", action="store_true", help="Save files but do not send.")
    parser.add_argument(
        "--output-dir",
        default=os.getenv("WATCHLIST_OUTPUT_DIR", OUTPUT_DIR),
        help="Where to write the report and CSV.",
    )
    parser.add_argument(
        "--history-days", type=int, default=HISTORY_DAYS, help="Calendar days of history."
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    rows, stats = build_watchlist(limit=args.limit, history_days=args.history_days)
    markdown = render_markdown(rows, stats)
    paths = save_outputs(rows, markdown, args.output_dir)

    print("", flush=True)
    print(markdown, flush=True)
    print(f"Report saved: {paths['report']}", flush=True)
    print(f"CSV saved: {paths['csv']}", flush=True)

    if args.no_email:
        print("Email skipped (--no-email).", flush=True)
        return 0

    subject_prefix = os.getenv("REPORT_EMAIL_SUBJECT_PREFIX", "Daily Watchlist")
    subject = (
        f"{subject_prefix} - {stats['run_date']} - "
        f"{stats['triggered_count']} triggered, {stats['forming_count']} forming"
    )
    try:
        send_report_email(
            report_path=paths["report"],
            subject=subject,
            body=markdown,
            extra_attachments=[paths["csv"]],
        )
    except Exception as exc:
        print(f"Email failed: {exc}", flush=True)
        return 1
    print(f"Email sent: {subject}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

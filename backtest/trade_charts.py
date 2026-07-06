"""Per-trade candlestick charts with risk/reward overlay.

For each trade in a backtest trades CSV, render a daily candlestick chart showing
the setup context, entry/stop/target levels, the shaded risk (entry->stop) and
reward (entry->target) zones, and entry/exit markers. This turns the trade log
into something you can eyeball to sanity-check what the strategy actually took.

Run from the repo root:

    venv/bin/python -m backtest.trade_charts --limit 20
    venv/bin/python -m backtest.trade_charts --setup bullish_power_gap_base
    venv/bin/python -m backtest.trade_charts --symbol NVDA --winners

Reuses the daily data provider (cached Yahoo bars) and the same mplfinance style
as chart_generator.py.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional

import pandas as pd

# Reuse the shared matplotlib cache setup from chart_generator.
from chart_generator import MPL_CACHE_DIR  # noqa: F401  (sets MPLCONFIGDIR)
import mplfinance as mpf

from .data import DailyDataProvider

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "trade_charts")

# Bars of context to show before entry and after exit.
BARS_BEFORE = 45
BARS_AFTER = 12

_ENTRY_COLOR = "#2ca02c"   # green
_STOP_COLOR = "#d62728"    # red
_TARGET_COLOR = "#1f77b4"  # blue


def _with_emas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for span in (8, 21, 50):
        out[f"EMA{span}"] = out["Close"].ewm(span=span, adjust=False).mean()
    return out


def _window(df: pd.DataFrame, entry: pd.Timestamp, exit_: Optional[pd.Timestamp]) -> pd.DataFrame:
    idx = df.index
    try:
        e_pos = idx.get_indexer([entry], method="nearest")[0]
    except Exception:
        return df.iloc[-(BARS_BEFORE + BARS_AFTER):]
    if exit_ is not None and pd.notna(exit_):
        x_pos = idx.get_indexer([exit_], method="nearest")[0]
    else:
        x_pos = e_pos
    lo = max(0, e_pos - BARS_BEFORE)
    hi = min(len(idx), x_pos + BARS_AFTER + 1)
    return df.iloc[lo:hi]


def render_trade_chart(
    trade: pd.Series,
    df: pd.DataFrame,
    output_dir: str = OUTPUT_DIR,
) -> Optional[str]:
    """Render a single trade's chart. Returns the file path, or None if skipped."""
    symbol = str(trade["symbol"]).upper()
    entry_date = pd.to_datetime(trade["entry_date"])
    exit_date = pd.to_datetime(trade.get("exit_date")) if pd.notna(trade.get("exit_date")) else None
    entry = float(trade["entry"])
    stop = float(trade["stop"])
    target = float(trade["target"])
    r_mult = trade.get("R")
    reason = str(trade.get("reason") or "")
    setup = str(trade.get("setup") or "")

    win = pd.notna(r_mult) and float(r_mult) > 0
    chart_df = _with_emas(_window(df, entry_date, exit_date))
    if chart_df.empty or len(chart_df) < 5:
        return None

    ema_plots = [
        mpf.make_addplot(chart_df["EMA8"], color="#1f77b4", width=1.0),
        mpf.make_addplot(chart_df["EMA21"], color="#ff7f0e", width=1.0),
        mpf.make_addplot(chart_df["EMA50"], color="#2ca02c", width=1.0),
    ]

    # Shaded zones: reward (entry->target) green, risk (entry->stop) red.
    fill_between = [
        dict(y1=entry, y2=target, color=_TARGET_COLOR, alpha=0.10),
        dict(y1=entry, y2=stop, color=_STOP_COLOR, alpha=0.12),
    ]

    hlines = dict(
        hlines=[entry, stop, target],
        colors=[_ENTRY_COLOR, _STOP_COLOR, _TARGET_COLOR],
        linestyle="--",
        linewidths=1.1,
    )

    # Entry/exit vertical markers (snap to nearest available bar in window).
    vdates, vcolors = [], []
    near_entry = chart_df.index[chart_df.index.get_indexer([entry_date], method="nearest")[0]]
    vdates.append(near_entry)
    vcolors.append(_ENTRY_COLOR)
    if exit_date is not None:
        near_exit = chart_df.index[chart_df.index.get_indexer([exit_date], method="nearest")[0]]
        vdates.append(near_exit)
        vcolors.append("#555555")
    vlines = dict(vlines=vdates, colors=vcolors, linestyle=":", linewidths=1.0)

    rr = (target - entry) / (entry - stop) if entry > stop else float("nan")
    r_txt = f"{float(r_mult):+.2f}R" if pd.notna(r_mult) else "open"
    outcome = "WIN" if win else "LOSS"
    title = (
        f"{symbol}  {setup}\n"
        f"{entry_date.date()} -> {exit_date.date() if exit_date is not None else 'open'}  "
        f"| {outcome} {r_txt} ({reason})  |  planned R:R {rr:.1f}"
    )

    style = mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle=":", y_on_right=False)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    fname = f"{symbol}_{entry_date.date()}_{setup}_{outcome}.png"
    fname = "".join(c if c.isalnum() or c in "-_." else "_" for c in fname)
    filepath = os.path.join(output_dir, fname)

    mpf.plot(
        chart_df,
        type="candle",
        style=style,
        addplot=ema_plots,
        volume=True,
        title=title,
        ylabel="Price",
        ylabel_lower="Volume",
        figsize=(14, 8),
        tight_layout=True,
        warn_too_much_data=len(chart_df) + 20,
        hlines=hlines,
        vlines=vlines,
        fill_between=fill_between,
        savefig={"fname": filepath, "dpi": 130, "bbox_inches": "tight"},
    )
    return filepath


def main() -> None:
    p = argparse.ArgumentParser(description="Render per-trade R:R candlestick charts")
    p.add_argument("--trades", default=None, help="path to a trades CSV (default: latest in output/)")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--limit", type=int, default=20, help="max charts to render (0 = all)")
    p.add_argument("--setup", default=None, help="only this setup type")
    p.add_argument("--symbol", default=None, help="only this symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--winners", action="store_true", help="only winning trades")
    g.add_argument("--losers", action="store_true", help="only losing trades")
    args = p.parse_args()

    trades_path = args.trades or os.path.join(
        os.path.dirname(__file__), "output", f"trades_{args.start}_{args.end}.csv"
    )
    if not os.path.exists(trades_path):
        raise SystemExit(f"Trades CSV not found: {trades_path}\nRun a backtest first.")

    trades = pd.read_csv(trades_path)
    if args.setup:
        trades = trades[trades["setup"] == args.setup]
    if args.symbol:
        trades = trades[trades["symbol"].str.upper() == args.symbol.upper()]
    if args.winners:
        trades = trades[trades["R"] > 0]
    if args.losers:
        trades = trades[trades["R"] <= 0]
    if args.limit > 0:
        trades = trades.head(args.limit)

    if trades.empty:
        raise SystemExit("No trades match the filters.")

    symbols = sorted(trades["symbol"].str.upper().unique())
    provider = DailyDataProvider(args.start, args.end)
    print(f"Loading data for {len(symbols)} symbols...", flush=True)
    data = provider.get_many(symbols)

    made = 0
    for _, trade in trades.iterrows():
        sym = str(trade["symbol"]).upper()
        df = data.get(sym)
        if df is None:
            continue
        try:
            path = render_trade_chart(trade, df)
        except Exception as exc:
            print(f"  ! {sym} {trade['entry_date']}: {exc}", flush=True)
            continue
        if path:
            made += 1
            print(f"  [{made}] {os.path.basename(path)}", flush=True)

    print(f"\nRendered {made} trade charts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

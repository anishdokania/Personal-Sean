"""Forward watchlist generator (deterministic, no paid AI).

Runs the same point-in-time strategy the backtest validates, but against the
*latest* bar of fresh data, to produce a tradeable watchlist for the next
session. For every liquid-universe symbol that qualifies as a fresh setup on the
most recent close, it records the trigger (entry), invalidation (stop), a 2R
target, the setup type, and the component scores -- then renders a candlestick
chart per name with the entry/stop levels drawn on.

Run from the repo root (after the close):

    venv/bin/python -m backtest.watchlist --for 2026-07-06
"""

from __future__ import annotations

import argparse
import os
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from chart_generator import generate_chart_image
from .config import BacktestConfig
from .data import DailyDataProvider
from .sector import SectorRanker
from .signals import evaluate_bar
from .universe_movers import MOVERS_UNIVERSE

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "watchlist")

# Rank weight (Today/Structure/Blueprint/Sector). Mirrors the live FinalPreAIScore
# emphasis, dropping the technical pre-AI term which isn't produced here.
RANK_W = {"today": 0.30, "structure": 0.30, "blueprint": 0.25, "sector": 0.15}
TARGET_R = 2.0

# Setups the backtest showed are net-losing -- suppress them from the live
# watchlist. accumulation_base_lows: 14% win rate, -0.59R avg over 2021-2024.
EXCLUDE_SETUPS = {"accumulation_base_lows"}


def _rank_score(sig) -> float:
    return (
        RANK_W["today"] * (sig.today_score or 0)
        + RANK_W["structure"] * (sig.structure_score or 0)
        + RANK_W["blueprint"] * (sig.blueprint_score or 0)
        + RANK_W["sector"] * (sig.sector_alignment_score or 50)
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the next-session watchlist")
    p.add_argument("--for", dest="for_date", default=None,
                   help="the session the watchlist is for, e.g. 2026-07-06 (label only)")
    p.add_argument("--start", default="2024-01-01", help="history start for indicators")
    p.add_argument("--limit", type=int, default=0, help="cap universe (0 = all)")
    p.add_argument("--no-charts", action="store_true")
    args = p.parse_args()

    symbols = list(MOVERS_UNIVERSE)
    if args.limit > 0:
        symbols = symbols[: args.limit]

    # Pull fresh data through today (end is exclusive-ish; pad by 2 days).
    end = (pd.Timestamp.today().normalize() + pd.Timedelta(days=2)).date().isoformat()
    provider = DailyDataProvider(args.start, end, use_cache=False)
    print(f"Fetching fresh daily data for {len(symbols)} symbols -> {end}...", flush=True)
    data = provider.get_many(symbols)
    ranker = SectorRanker(provider)

    config = BacktestConfig(start=args.start, end=end)

    rows = []
    for sym, df in data.items():
        if df.empty:
            continue
        i = len(df) - 1  # evaluate the most recent bar only
        sig = evaluate_bar(sym, df, i, config, sector_ranker=ranker)
        if sig is None:
            continue
        if sig.setup_type in EXCLUDE_SETUPS:
            continue  # proven net-losing setup, kept out of the live watchlist
        if sig.target_level is None:
            continue
        entry = sig.trigger_level        # limit entry at the reclaim level
        stop = sig.invalidation_level
        risk = entry - stop
        rr = (sig.target_level - entry) / risk if risk > 0 else 0.0
        rows.append({
            "symbol": sym,
            "as_of": df.index[-1].date().isoformat(),
            "setup": sig.setup_type,
            "sector_etf": sig.sector_etf,
            "close": round(sig.close, 2),
            "entry_limit": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(sig.target_level, 2),
            "risk_pct": round(100 * risk / entry, 1),
            "rr": round(rr, 1),
            "rank_score": round(rr, 1),   # rank by reward:risk
            "_sig": sig,
        })

    if not rows:
        print("\nNo qualifying setups on the latest close. Empty watchlist.")
        return

    rows.sort(key=lambda r: r["rank_score"], reverse=True)
    as_of = rows[0]["as_of"]
    label = args.for_date or "next session"

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame([{k: v for k, v in r.items() if k != "_sig"} for r in rows])
    csv_path = os.path.join(OUTPUT_DIR, f"watchlist_{label}.csv")
    df_out.to_csv(csv_path, index=False)

    print(f"\n============  WATCHLIST for {label}  (as-of close {as_of})  ============")
    cols = ["symbol", "setup", "close", "entry_limit", "stop", "target",
            "risk_pct", "rr", "sector_etf"]
    print(df_out[cols].to_string(index=False))
    print(f"\n{len(rows)} names. Entry = limit at reclaim level. CSV: {csv_path}")

    if args.no_charts:
        return

    print("\nRendering charts...", flush=True)
    for r in rows:
        sig = r["_sig"]
        try:
            path = generate_chart_image(
                r["symbol"],
                data[r["symbol"]],
                output_dir=OUTPUT_DIR,
                lookback=120,
                trigger_level=sig.target_level,      # blue: target (swing high)
                stop_reference=sig.invalidation_level,  # red: stop
                filename_suffix=f"watch_{label}_{r['setup']}",
                title_suffix=(f"{r['setup']} | entry {r['entry_limit']} / stop {r['stop']} "
                              f"/ target {r['target']} | risk {r['risk_pct']}% | {r['rr']}R"),
            )
            print(f"  {r['symbol']}: {os.path.basename(path)}", flush=True)
        except Exception as exc:
            print(f"  ! {r['symbol']} chart failed: {exc}", flush=True)

    print(f"\nCharts + CSV in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

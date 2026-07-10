"""CLI entrypoint for the backtest.

Run from the repo root (uses the committed marketdata snapshot by default):

    python -m backtest.run --start 2021-01-01 --end 2026-07-09
    python -m backtest.run --exit-model trail_ema8
    python -m backtest.run --ambiguity optimistic --no-hourly   # upper bound

Outputs a stats summary to stdout and writes a trades CSV + equity curve CSV to
backtest/output/.
"""

from __future__ import annotations

import argparse
import os

from .config import BacktestConfig
from .engine import run_backtest
from .localdata import HourlyStore, LocalDataProvider
from .metrics import compute_stats, trades_to_frame
from .sector import SectorRanker
from .universe_movers import MOVERS_UNIVERSE

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Blueprint strategy backtester")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-12-31")
    p.add_argument("--symbols", default="", help="comma-separated; default = movers universe")
    p.add_argument("--limit", type=int, default=0, help="cap number of symbols (0 = all)")
    p.add_argument("--provider", choices=["local", "yahoo"], default="local",
                   help="local = committed marketdata snapshot; yahoo = live yfinance")
    p.add_argument("--entry-model",
                   choices=["limit_reclaim", "next_open", "signal_close"],
                   default="limit_reclaim")
    p.add_argument("--exit-model", choices=["swing_target", "trail_ema8", "hybrid"],
                   default="swing_target")
    p.add_argument("--ambiguity", choices=["pessimistic", "optimistic"],
                   default="pessimistic")
    p.add_argument("--no-hourly", action="store_true",
                   help="disable hourly intrabar resolution (bounds-only mode)")
    p.add_argument("--min-adr", type=float, default=5.0, help="ADR%% universe floor")
    p.add_argument("--min-rr", type=float, default=1.0,
                   help="required reward:risk to the swing-high target")
    p.add_argument("--target-r", type=float, default=2.0)
    p.add_argument("--max-hold", type=int, default=15)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--max-positions", type=int, default=10)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument(
        "--no-sector",
        action="store_true",
        help="disable the point-in-time sector-alignment gate (for A/B comparison)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(MOVERS_UNIVERSE)
    )
    if args.limit > 0:
        symbols = symbols[: args.limit]

    config = BacktestConfig(
        symbols=symbols,
        start=args.start,
        end=args.end,
        entry_model=args.entry_model,
        exit_model=args.exit_model,
        ambiguity_mode=args.ambiguity,
        use_hourly_resolution=not args.no_hourly,
        min_adr_pct=args.min_adr,
        min_rr=args.min_rr,
        target_r_multiple=args.target_r,
        max_hold_days=args.max_hold,
        risk_pct_per_trade=args.risk_pct,
        max_concurrent_positions=args.max_positions,
    )

    print(f"Universe: {len(symbols)} symbols | {args.start} -> {args.end}", flush=True)
    print(f"Entry: {config.entry_model} | Exit: {config.exit_model} | "
          f"Ambiguity: {config.ambiguity_mode} | Hourly resolution: "
          f"{'on' if config.use_hourly_resolution else 'off'}", flush=True)

    if args.provider == "local":
        provider = LocalDataProvider(args.start, args.end)
        hourly = HourlyStore() if config.use_hourly_resolution else None
    else:
        from .data import DailyDataProvider

        provider = DailyDataProvider(args.start, args.end, use_cache=not args.no_cache)
        hourly = None

    print("Loading daily data...", flush=True)
    data = provider.get_many(symbols)
    print(f"Loaded {len(data)} symbols with data.\n", flush=True)

    sector_ranker = None
    if not args.no_sector:
        print("Loading sector ETF history for point-in-time alignment...", flush=True)
        sector_ranker = SectorRanker(provider)
        print(f"Sector ranker ready ({len(sector_ranker.loaded_etfs)} ETFs).\n", flush=True)

    result = run_backtest(data, config, sector_ranker=sector_ranker, hourly_store=hourly)
    stats = compute_stats(result)

    print("\n" + stats.as_text())
    if result.resolver_stats:
        print(f"Ambiguity resolver: {result.resolver_stats}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = f"{args.start}_{args.end}_{config.entry_model}_{config.exit_model}_{config.ambiguity_mode}"
    trades_df = trades_to_frame(result)
    trades_path = os.path.join(OUTPUT_DIR, f"trades_{stamp}.csv")
    equity_path = os.path.join(OUTPUT_DIR, f"equity_{stamp}.csv")
    trades_df.to_csv(trades_path, index=False)
    result.equity_curve.to_csv(equity_path, header=["equity"])
    print(f"\nTrades written to {trades_path}")
    print(f"Equity curve written to {equity_path}")


if __name__ == "__main__":
    main()

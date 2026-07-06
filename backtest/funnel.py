"""Gate drop-off diagnostic.

Walks the same point-in-time evaluation as the backtest but records where every
candidate bar drops out of the funnel, so we can see which gate does the most
filtering. Run from the repo root:

    venv/bin/python -m backtest.funnel --limit 25 --start 2021-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse

from .config import BacktestConfig
from .data import DailyDataProvider
from .sector import SectorRanker
from .signals import evaluate_bar
from .universe_movers import MOVERS_UNIVERSE


STAGES = [
    ("pass_hard_gate", "Passed hard gate (price/vol/ATR/SMA)"),
    ("fail_hard_gate", "  dropped: failed hard gate"),
    ("fail_adr", "  dropped: ADR% below floor"),
    ("fail_no_entry", "  dropped: no UnR reclaim entry"),
    ("fail_no_target", "  dropped: no swing-high target / RR too low"),
    ("fail_scoring_error", "  dropped: scoring error"),
    ("fail_today", "  dropped: Today-Focus < 70 (legacy)"),
    ("fail_structure", "  dropped: Structure < 65 (legacy)"),
    ("fail_blueprint", "  dropped: Blueprint-Fit < 65 (legacy)"),
    ("fail_sector", "  dropped: Sector-Align < 45"),
    ("fail_extended", "  dropped: entry too extended above 8 EMA"),
    ("fail_no_levels", "  dropped: no trigger/stop levels"),
    ("fail_bad_levels", "  dropped: trigger>close>stop violated"),
    ("fail_risk_too_wide", "  dropped: stop too far"),
    ("signal", "SIGNALS produced"),
]


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest funnel drop-off report")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--limit", type=int, default=25, help="cap symbols (0 = all)")
    args = p.parse_args()

    symbols = list(MOVERS_UNIVERSE)
    if args.limit > 0:
        symbols = symbols[: args.limit]

    config = BacktestConfig(symbols=symbols, start=args.start, end=args.end)
    provider = DailyDataProvider(args.start, args.end)
    print(f"Loading {len(symbols)} symbols {args.start} -> {args.end}...", flush=True)
    data = provider.get_many(symbols)
    ranker = SectorRanker(provider)

    funnel: dict = {}
    for n, (sym, df) in enumerate(data.items(), 1):
        for i in range(len(df)):
            evaluate_bar(sym, df, i, config, sector_ranker=ranker, funnel=funnel)
        print(f"  [{n}/{len(data)}] {sym} done", flush=True)

    evaluated = funnel.get("evaluated", 0)
    hard_pass = funnel.get("pass_hard_gate", 0)

    print("\n================  FUNNEL DROP-OFF  ================")
    print(f"Bars evaluated (enough history)....... {evaluated:>8,}")
    print("-" * 52)
    for key, label in STAGES:
        count = funnel.get(key, 0)
        # % shown relative to hard-gate survivors for the scoring stages.
        base = hard_pass if key not in ("pass_hard_gate", "fail_hard_gate") else evaluated
        pct = (100.0 * count / base) if base else 0.0
        print(f"{label:<44} {count:>8,}  ({pct:5.1f}%)")
    print("-" * 52)
    sig = funnel.get("signal", 0)
    if evaluated:
        print(f"Overall: {sig:,} signals from {evaluated:,} bars "
              f"({100.0 * sig / evaluated:.3f}% survive)")


if __name__ == "__main__":
    main()

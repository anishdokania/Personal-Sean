"""Parameter sweep for the close-entry + EMA-trail UnR model (the G5 recipe).

Axes:
  * stop buffer below the undercut low (ADRs)  — trades win rate vs R size
  * trail EMA span (8 vs 21) and confirmation closes (1 vs 2) — whipsaw control
  * time stop (15 sessions vs effectively none) — the 15-day cap was inherited
    from the swing-target model and force-closes exactly the runners the trail
    exists to ride

Every cell reports the 2021-23 and 2024-26 halves separately: a cell is only
trustworthy if it holds in both. Run:

    python -m backtest.sweep
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Dict

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .engine import Signal, generate_signals, run_backtest
from .localdata import HourlyStore, LocalDataProvider
from .metrics import bootstrap_expectancy_ci, trades_to_frame
from .universe_movers import MOVERS_UNIVERSE

OUTPUT = os.path.join(os.path.dirname(__file__), "results", "SWEEP_TRAIL.md")

STOP_BUFFERS = (0.05, 0.25, 0.50)
TRAILS = ((8, 1), (8, 2), (21, 1), (21, 2))
MAX_HOLDS = (15, 400)  # 400 ≈ "no time stop" inside a 5.5y window
SPLIT_AT = pd.Timestamp("2024-01-01")


def main() -> None:
    provider = LocalDataProvider("2021-01-01", "2026-12-31")
    data = provider.get_many(list(MOVERS_UNIVERSE))
    hourly = HourlyStore()

    spy = provider.get("SPY")
    spy_regime = spy["Close"] > spy["Close"].rolling(50).mean()

    def g5_filter(sigs: Dict[str, Dict[pd.Timestamp, Signal]]):
        def keep(s: Signal) -> bool:
            if s.date not in spy_regime.index or not bool(spy_regime.loc[s.date]):
                return False
            if s.chase_adr is None or s.chase_adr > 0.5:
                return False
            return s.adr_pct is not None and 5.0 <= s.adr_pct < 10.0
        return {sym: {d: s for d, s in ss.items() if keep(s)} for sym, ss in sigs.items()}

    rows = []
    for buffer in STOP_BUFFERS:
        base = BacktestConfig(min_adr_pct=5.0, snipe_stop_buffer_adr=buffer,
                              entry_model="signal_close", exit_model="trail_ema8")
        print(f"Signal pass @ stop buffer {buffer} ADR ...", flush=True)
        signals = {}
        for sym, df in data.items():
            signals[sym] = generate_signals(sym, df, base)
        filtered = g5_filter(signals)
        n_sigs = sum(len(s) for s in filtered.values())
        print(f"  filtered signals: {n_sigs}", flush=True)

        for span, confirm in TRAILS:
            for hold in MAX_HOLDS:
                cfg = replace(base, trail_ema_span=span, trail_confirm_closes=confirm,
                              max_hold_days=hold)
                res = run_backtest(data, cfg, hourly_store=hourly,
                                   signals_by_symbol=filtered, quiet=True)
                df = trades_to_frame(res)
                df = df[df["R"].notna()].copy()
                df["entry_date"] = pd.to_datetime(df["entry_date"])
                rs = df["R"].astype(float).to_numpy()
                if len(rs) == 0:
                    continue
                wins = rs[rs > 0]
                early = df[df["entry_date"] < SPLIT_AT]["R"].astype(float)
                late = df[df["entry_date"] >= SPLIT_AT]["R"].astype(float)
                lo, hi = bootstrap_expectancy_ci(list(rs))
                rows.append({
                    "stop_buf": buffer, "trail": f"EMA{span}x{confirm}",
                    "time_stop": hold if hold < 400 else None,
                    "n": len(rs),
                    "win%": 100 * len(wins) / len(rs),
                    "avg_win": wins.mean() if len(wins) else 0.0,
                    "avg_loss": rs[rs <= 0].mean() if (rs <= 0).any() else 0.0,
                    "exp": rs.mean(), "ci_lo": lo, "ci_hi": hi,
                    "total_r": rs.sum(),
                    "exp_21_23": early.mean() if len(early) else np.nan,
                    "exp_24_26": late.mean() if len(late) else np.nan,
                })
                r = rows[-1]
                print(f"  buf={buffer} {r['trail']} hold={hold}: n={r['n']} "
                      f"win={r['win%']:.0f}% exp={r['exp']:+.3f}R "
                      f"[{lo:+.2f},{hi:+.2f}] halves=({r['exp_21_23']:+.2f}/"
                      f"{r['exp_24_26']:+.2f})", flush=True)

    tbl = pd.DataFrame(rows).sort_values("exp", ascending=False)
    lines = [
        "# Trail-model parameter sweep (G5-filtered signals)", "",
        "Signals: UnR + risk-on + no-chase(≤0.5 ADR) + ADR 5–10%. Entry at signal",
        "close; exits = stop under undercut low − buffer, EMA trail, optional",
        "time stop. All cells are exact on daily bars (zero assumed outcomes).",
        "A cell is only meaningful if both era halves agree.", "",
        "| Stop buf (ADR) | Trail | Time stop | N | Win% | Avg win | Avg loss | "
        "Expectancy (95% CI) | Total R | 2021-23 | 2024-26 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in tbl.iterrows():
        lines.append(
            f"| {r['stop_buf']} | {r['trail']} | {r['time_stop'] or '—'} | {r['n']} | "
            f"{r['win%']:.0f}% | {r['avg_win']:+.2f}R | {r['avg_loss']:+.2f}R | "
            f"**{r['exp']:+.3f}R** [{r['ci_lo']:+.2f}, {r['ci_hi']:+.2f}] | "
            f"{r['total_r']:+.0f}R | {r['exp_21_23']:+.2f}R | {r['exp_24_26']:+.2f}R |"
        )
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nSweep table written to {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()

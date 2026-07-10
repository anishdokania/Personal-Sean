"""Performance statistics for a backtest result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .engine import BacktestResult, Trade


@dataclass
class Stats:
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float          # average R per trade -- the headline edge number
    expectancy_ci95: Tuple[float, float]  # bootstrap CI on expectancy
    profit_factor: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    avg_bars_held: float
    exposure_note: str
    exit_reason_counts: Dict[str, int]
    resolution_counts: Dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        lo, hi = self.expectancy_ci95
        lines = [
            "================  BACKTEST RESULTS  ================",
            f"Trades taken........ {self.trades}",
            f"Win rate............ {self.win_rate:.1%}  ({self.wins}W / {self.losses}L)",
            f"Avg win............. {self.avg_win_r:+.2f}R",
            f"Avg loss............ {self.avg_loss_r:+.2f}R",
            f"Expectancy.......... {self.expectancy_r:+.3f}R per trade   <-- the edge",
            f"  95% bootstrap CI.. [{lo:+.3f}R, {hi:+.3f}R]",
            f"Profit factor....... {self.profit_factor:.2f}",
            "----------------------------------------------------",
            f"Total return........ {self.total_return_pct:+.1f}%",
            f"CAGR................ {self.cagr_pct:+.1f}%",
            f"Max drawdown........ {self.max_drawdown_pct:.1f}%",
            f"Avg bars held....... {self.avg_bars_held:.1f}",
            "----------------------------------------------------",
            "Outcome provenance (how each trade's path was determined):",
        ]
        for res in ("exact", "hourly", "pessimistic", "optimistic"):
            if res in self.resolution_counts:
                lines.append(f"  {res:<18} {self.resolution_counts[res]}")
        lines.append("Exits by reason:")
        for reason, count in sorted(
            self.exit_reason_counts.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {reason:<18} {count}")
        lines.append("====================================================")
        return "\n".join(lines)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min() * 100)


def bootstrap_expectancy_ci(
    rs: List[float], n_boot: int = 10_000, ci: float = 0.95, seed: int = 7
) -> Tuple[float, float]:
    """Percentile bootstrap CI for mean R. Answers: could this expectancy be
    luck from this many trades?"""
    if len(rs) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    arr = np.asarray(rs, dtype=float)
    means = rng.choice(arr, size=(n_boot, arr.size), replace=True).mean(axis=1)
    alpha = (1 - ci) / 2
    return (float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha)))


def compute_stats(result: BacktestResult) -> Stats:
    trades: List[Trade] = [t for t in result.trades if t.exit_price is not None]
    n = len(trades)
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)

    equity = result.equity_curve
    if not equity.empty:
        total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100
    else:
        total_return = cagr = 0.0

    reason_counts: Dict[str, int] = {}
    res_counts: Dict[str, int] = {}
    for t in trades:
        reason_counts[t.exit_reason or "?"] = reason_counts.get(t.exit_reason or "?", 0) + 1
        res_counts[t.resolution] = res_counts.get(t.resolution, 0) + 1

    return Stats(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / n) if n else 0.0,
        avg_win_r=float(np.mean(wins)) if wins else 0.0,
        avg_loss_r=float(np.mean(losses)) if losses else 0.0,
        expectancy_r=float(np.mean(rs)) if rs else 0.0,
        expectancy_ci95=bootstrap_expectancy_ci(rs),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        total_return_pct=total_return,
        cagr_pct=cagr,
        max_drawdown_pct=_max_drawdown(equity),
        avg_bars_held=float(np.mean([t.bars_held for t in trades])) if trades else 0.0,
        exposure_note=f"{n} trades over {len(equity)} sessions",
        exit_reason_counts=reason_counts,
        resolution_counts=res_counts,
    )


def trades_to_frame(result: BacktestResult) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        rows.append(
            {
                "symbol": t.symbol,
                "signal_date": t.signal_date.date() if t.signal_date is not None else None,
                "entry_date": t.entry_date.date() if t.entry_date is not None else None,
                "entry": round(t.entry_price, 2),
                "stop": round(t.stop_initial, 2),
                "target": round(t.target, 2) if t.target is not None else None,
                "shares": t.shares,
                "partial_date": t.partial_date.date() if t.partial_date is not None else None,
                "partial_exit": round(t.partial_price, 2) if t.partial_price is not None else None,
                "exit_date": t.exit_date.date() if t.exit_date is not None else None,
                "exit": round(t.exit_price, 2) if t.exit_price is not None else None,
                "reason": t.exit_reason,
                "bars_held": t.bars_held,
                "R": round(t.r_multiple, 2) if t.r_multiple is not None else None,
                "pnl": round(t.pnl, 2),
                "setup": t.setup_type,
                "adr_pct": round(t.adr_pct, 2) if t.adr_pct is not None else None,
                "rr_planned": round(t.rr_planned, 2) if t.rr_planned is not None else None,
                "chase_adr": round(t.chase_adr, 2) if t.chase_adr is not None else None,
                "resolution": t.resolution,
            }
        )
    return pd.DataFrame(rows)

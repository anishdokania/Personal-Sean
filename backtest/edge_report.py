"""Edge study: run the UnR strategy across entry/exit/ambiguity variants and
decompose where the expectancy actually comes from.

The output is a bracket, not a single number:

  * pessimistic bound  — every unresolvable intrabar ambiguity scored against us
  * optimistic bound   — every unresolvable ambiguity scored for us
  * hourly resolution  — 2 years of hourly bars order most ambiguous days, so
                         inside that window the bracket collapses to a
                         measurement

plus slices (setup type, ADR%, planned RR, year, market regime, symbol
concentration) over the resolved trade set, with bootstrap CIs so a hot slice
with 9 trades doesn't get mistaken for edge.

Run from the repo root:

    python -m backtest.edge_report --start 2021-01-01 --end 2026-07-09
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .engine import BacktestResult, Signal, run_backtest
from .localdata import HourlyStore, LocalDataProvider
from .metrics import bootstrap_expectancy_ci, compute_stats, trades_to_frame
from .universe_movers import MOVERS_UNIVERSE

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# Signals are generated once at this loose ADR floor; variants and slices then
# filter upward. Keeps one signal pass for the whole study.
SIGNAL_GEN_ADR_FLOOR = 3.5
HOURLY_COVERAGE_START = pd.Timestamp("2024-07-11")  # first day of hourly data


def _filter_signals(
    signals: Dict[str, Dict[pd.Timestamp, Signal]], min_adr: float
) -> Dict[str, Dict[pd.Timestamp, Signal]]:
    return {
        sym: {d: s for d, s in sigs.items() if (s.adr_pct or 0) >= min_adr}
        for sym, sigs in signals.items()
    }


def _r_stats(rs: List[float]) -> dict:
    rs = [r for r in rs if r is not None and np.isfinite(r)]
    n = len(rs)
    if n == 0:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    lo, hi = bootstrap_expectancy_ci(rs)
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "expectancy": float(np.mean(rs)),
        "total_r": float(np.sum(rs)),
        "ci_lo": lo,
        "ci_hi": hi,
    }


def _fmt_row(label: str, s: dict) -> str:
    if s.get("n", 0) == 0:
        return f"| {label} | 0 | – | – | – | – | – |"
    ci = f"[{s['ci_lo']:+.2f}, {s['ci_hi']:+.2f}]" if np.isfinite(s.get("ci_lo", np.nan)) else "–"
    return (
        f"| {label} | {s['n']} | {s['win_rate']:.0%} | {s['avg_win']:+.2f}R | "
        f"{s['avg_loss']:+.2f}R | **{s['expectancy']:+.3f}R** {ci} | {s['total_r']:+.1f}R |"
    )


SLICE_HEADER = (
    "| Slice | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | Total R |\n"
    "|---|---|---|---|---|---|---|"
)


def _trade_records(result: BacktestResult) -> pd.DataFrame:
    df = trades_to_frame(result)
    closed = df[df["R"].notna()].copy()
    closed["entry_date"] = pd.to_datetime(closed["entry_date"])
    closed["signal_date"] = pd.to_datetime(closed["signal_date"])
    return closed


def _slice_table(df: pd.DataFrame, key_fn, title: str) -> List[str]:
    lines = [f"### {title}", "", SLICE_HEADER]
    groups: Dict[str, List[float]] = {}
    for _, row in df.iterrows():
        key = key_fn(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(row["R"])
    for key in sorted(groups.keys()):
        lines.append(_fmt_row(key, _r_stats(groups[key])))
    lines.append("")
    return lines


def _adr_bucket(row) -> Optional[str]:
    a = row.get("adr_pct")
    if a is None or not np.isfinite(a):
        return None
    if a < 5:
        return "ADR 3.5–5%"
    if a < 7:
        return "ADR 5–7%"
    if a < 10:
        return "ADR 7–10%"
    return "ADR 10%+"


def _chase_bucket(row) -> Optional[str]:
    c = row.get("chase_adr")
    if c is None or not np.isfinite(c):
        return None
    if c < 0.25:
        return "chase < 0.25 ADR"
    if c < 0.5:
        return "chase 0.25–0.5 ADR"
    if c < 1.0:
        return "chase 0.5–1.0 ADR"
    return "chase 1.0+ ADR"


def _rr_bucket(row) -> Optional[str]:
    r = row.get("rr_planned")
    if r is None or not np.isfinite(r):
        return None
    if r < 1.5:
        return "RR 1.0–1.5"
    if r < 2.5:
        return "RR 1.5–2.5"
    if r < 4:
        return "RR 2.5–4"
    return "RR 4+"


def main() -> None:
    p = argparse.ArgumentParser(description="UnR edge study")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-07-09")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--primary-adr", type=float, default=5.0,
                   help="ADR%% floor for the primary variants")
    args = p.parse_args()

    symbols = list(MOVERS_UNIVERSE)
    if args.limit > 0:
        symbols = symbols[: args.limit]

    provider = LocalDataProvider(args.start, args.end)
    data = provider.get_many(symbols)
    hourly = HourlyStore()
    print(f"Loaded {len(data)} symbols | {args.start} -> {args.end}", flush=True)

    # SPY regime for slicing (risk-on = SPY close above its 50-day SMA).
    spy = provider.get("SPY")
    spy_regime: Optional[pd.Series] = None
    if spy is not None:
        spy_regime = spy["Close"] > spy["Close"].rolling(50).mean()

    base = BacktestConfig(
        symbols=symbols, start=args.start, end=args.end,
        min_adr_pct=SIGNAL_GEN_ADR_FLOOR,
    )

    # --- One signal pass, reused by every variant ----------------------------
    print("Signal pass (point-in-time, one-time)...", flush=True)
    from .engine import generate_signals
    from .signals import precompute  # noqa: F401  (imported for clarity)

    all_signals: Dict[str, Dict[pd.Timestamp, Signal]] = {}
    for n, (sym, df) in enumerate(data.items(), 1):
        all_signals[sym] = generate_signals(sym, df, base)
        if n % 20 == 0:
            print(f"  ...{n}/{len(data)} symbols", flush=True)
    total = sum(len(s) for s in all_signals.values())
    print(f"Signals at ADR>={SIGNAL_GEN_ADR_FLOOR}%: {total}", flush=True)

    primary_signals = _filter_signals(all_signals, args.primary_adr)
    n_primary = sum(len(s) for s in primary_signals.values())
    print(f"Signals at primary ADR>={args.primary_adr}%: {n_primary}", flush=True)

    # --- Variant matrix -------------------------------------------------------
    def run(name: str, cfg: BacktestConfig, sigs) -> Tuple[str, BacktestResult]:
        print(f"  running variant: {name}", flush=True)
        res = run_backtest(data, cfg, hourly_store=hourly,
                           signals_by_symbol=sigs, quiet=True)
        return name, res

    variants: Dict[str, BacktestResult] = {}
    specs = [
        # name, entry, exit, ambiguity, hourly, signals
        ("A1 snipe→swing target (pessimistic, hourly)", "limit_reclaim", "swing_target", "pessimistic", True),
        ("A2 snipe→swing target (optimistic, hourly)", "limit_reclaim", "swing_target", "optimistic", True),
        ("A3 snipe→swing target (pessimistic, daily-only)", "limit_reclaim", "swing_target", "pessimistic", False),
        ("A4 snipe→swing target (optimistic, daily-only)", "limit_reclaim", "swing_target", "optimistic", False),
        ("B1 snipe→8EMA trail (exact)", "limit_reclaim", "trail_ema8", "pessimistic", True),
        ("C1 snipe→hybrid partial+trail (pessimistic, hourly)", "limit_reclaim", "hybrid", "pessimistic", True),
        ("C2 snipe→hybrid partial+trail (optimistic, hourly)", "limit_reclaim", "hybrid", "optimistic", True),
        ("D1 next-open→swing target (pessimistic, hourly)", "next_open", "swing_target", "pessimistic", True),
        ("D2 next-open→8EMA trail (exact)", "next_open", "trail_ema8", "pessimistic", True),
        ("F1 close-entry→swing target (pessimistic, hourly)", "signal_close", "swing_target", "pessimistic", True),
        ("F2 close-entry→swing target (optimistic, hourly)", "signal_close", "swing_target", "optimistic", True),
        ("F3 close-entry→8EMA trail (exact)", "signal_close", "trail_ema8", "pessimistic", True),
        ("F4 close-entry→hybrid (pessimistic, hourly)", "signal_close", "hybrid", "pessimistic", True),
        ("F5 close-entry→hybrid (optimistic, hourly)", "signal_close", "hybrid", "optimistic", True),
    ]
    for name, entry, exit_m, amb, use_hourly in specs:
        cfg = replace(base, entry_model=entry, exit_model=exit_m,
                      ambiguity_mode=amb, use_hourly_resolution=use_hourly,
                      min_adr_pct=args.primary_adr)
        variants[name] = run(name, cfg, primary_signals)[1]

    # ADR floor sensitivity on the core variant.
    for floor in (3.5, 7.0):
        cfg = replace(base, ambiguity_mode="pessimistic", min_adr_pct=floor)
        sigs = _filter_signals(all_signals, floor)
        variants[f"E ADR>={floor}% snipe→swing (pessimistic, hourly)"] = run(
            f"E ADR>={floor}%", cfg, sigs)[1]

    # --- Hypothesis-driven filters on the style-faithful model ----------------
    # (close entry + trail exit). Each filter has a trading rationale stated up
    # front — these are not mined from the slice tables:
    #   risk-on:   breakouts/reclaims follow through better when the index is
    #              above its 50-day (standard momentum-regime evidence).
    #   no-chase:  entering at the close far above the reclaimed level worsens
    #              the stop distance and buys into extension — the blueprint's
    #              own do-not-chase rule.
    #   adr-band:  the trail needs range to pay, but 10%+ ADR names gap through
    #              tight stops; 5-10% is the style's home turf.
    def sig_filter(pred) -> Dict[str, Dict[pd.Timestamp, Signal]]:
        return {
            sym: {d: s for d, s in sigs.items() if pred(s)}
            for sym, sigs in primary_signals.items()
        }

    def risk_on(s: Signal) -> bool:
        if spy_regime is None or s.date not in spy_regime.index:
            return False
        return bool(spy_regime.loc[s.date])

    def no_chase(s: Signal) -> bool:
        return s.chase_adr is not None and s.chase_adr <= 0.5

    def adr_band(s: Signal) -> bool:
        return s.adr_pct is not None and 5.0 <= s.adr_pct < 10.0

    filter_specs = [
        ("G1 F3 + risk-on regime", lambda s: risk_on(s)),
        ("G2 F3 + no-chase (close ≤0.5 ADR above level)", lambda s: no_chase(s)),
        ("G3 F3 + ADR 5–10% band", lambda s: adr_band(s)),
        ("G5 F3 + all three filters", lambda s: risk_on(s) and no_chase(s) and adr_band(s)),
    ]
    for name, pred in filter_specs:
        cfg = replace(base, entry_model="signal_close", exit_model="trail_ema8",
                      ambiguity_mode="pessimistic", use_hourly_resolution=True,
                      min_adr_pct=args.primary_adr)
        variants[name] = run(name, cfg, sig_filter(pred))[1]

    # --- Report ---------------------------------------------------------------
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    lines: List[str] = []
    lines.append("# UnR Snipe — Edge Report")
    lines.append("")
    lines.append(f"Window: **{args.start} → {args.end}** | Universe: "
                 f"{len(data)} high-ADR movers | Primary ADR floor: {args.primary_adr}%")
    lines.append("")
    lines.append("Every number below is point-in-time (no look-ahead). Where daily bars")
    lines.append("cannot order stop-vs-target inside one bar, the outcome is resolved with")
    lines.append("hourly data when it exists; what remains is *bracketed* between a")
    lines.append("pessimistic and an optimistic bound rather than guessed.")
    lines.append("")

    lines.append("## Variant matrix")
    lines.append("")
    lines.append("| Variant | Trades | Win% | Avg win | Avg loss | Expectancy (95% CI) | "
                 "Total R | PF | MaxDD | exact/hourly/assumed |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, res in variants.items():
        st = compute_stats(res)
        rc = st.resolution_counts
        assumed = rc.get("pessimistic", 0) + rc.get("optimistic", 0)
        prov = f"{rc.get('exact', 0)}/{rc.get('hourly', 0)}/{assumed}"
        ci = st.expectancy_ci95
        ci_s = f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if np.isfinite(ci[0]) else "–"
        lines.append(
            f"| {name} | {st.trades} | {st.win_rate:.0%} | {st.avg_win_r:+.2f}R | "
            f"{st.avg_loss_r:+.2f}R | **{st.expectancy_r:+.3f}R** {ci_s} | "
            f"{sum(r for r in (t.r_multiple for t in res.trades if t.exit_price) if r is not None):+.1f}R | "
            f"{st.profit_factor:.2f} | {st.max_drawdown_pct:.1f}% | {prov} |"
        )
    lines.append("")

    # --- Ground-truth window: bracket collapse --------------------------------
    lines.append("## Ground-truth window (hourly coverage, "
                 f"{HOURLY_COVERAGE_START.date()} onward)")
    lines.append("")
    lines.append("Inside this window ambiguous days are *measured* from hourly bars, so the")
    lines.append("pessimistic/optimistic bracket nearly collapses — this is the closest thing")
    lines.append("to the strategy's true daily-approximated edge:")
    lines.append("")
    lines.append(SLICE_HEADER)
    for name in ("A1 snipe→swing target (pessimistic, hourly)",
                 "A2 snipe→swing target (optimistic, hourly)",
                 "B1 snipe→8EMA trail (exact)",
                 "C1 snipe→hybrid partial+trail (pessimistic, hourly)",
                 "C2 snipe→hybrid partial+trail (optimistic, hourly)",
                 "F1 close-entry→swing target (pessimistic, hourly)",
                 "F2 close-entry→swing target (optimistic, hourly)",
                 "F3 close-entry→8EMA trail (exact)",
                 "F4 close-entry→hybrid (pessimistic, hourly)",
                 "F5 close-entry→hybrid (optimistic, hourly)",
                 "G5 F3 + all three filters"):
        df = _trade_records(variants[name])
        recent = df[df["entry_date"] >= HOURLY_COVERAGE_START]
        lines.append(_fmt_row(name, _r_stats(list(recent["R"]))))
    lines.append("")

    # Provenance-pure subset: only trades whose outcome needed no assumption.
    lines.append("Measured-only subset (trades resolved exactly or by hourly data — zero")
    lines.append("assumption content), core variant:")
    lines.append("")
    lines.append(SLICE_HEADER)
    core = _trade_records(variants["A1 snipe→swing target (pessimistic, hourly)"])
    measured = core[core["resolution"].isin(["exact", "hourly"])]
    assumed = core[~core["resolution"].isin(["exact", "hourly"])]
    lines.append(_fmt_row("measured (exact+hourly)", _r_stats(list(measured["R"]))))
    lines.append(_fmt_row("assumed (bounds applied)", _r_stats(list(assumed["R"]))))
    lines.append("")

    # --- Slices over the core resolved trade set -------------------------------
    lines.append("## Where the edge lives (core variant, pessimistic + hourly)")
    lines.append("")
    lines.extend(_slice_table(core, lambda r: r.get("setup"), "By setup type"))
    lines.extend(_slice_table(core, _adr_bucket, "By ADR% at signal"))
    lines.extend(_slice_table(core, _rr_bucket, "By planned reward:risk"))
    lines.extend(_slice_table(core, lambda r: str(r["entry_date"].year), "By year"))
    if spy_regime is not None:
        def regime_key(row):
            d = row["signal_date"]
            if d in spy_regime.index:
                return "SPY > 50SMA (risk-on)" if bool(spy_regime.loc[d]) else "SPY < 50SMA (risk-off)"
            return None
        lines.extend(_slice_table(core, regime_key, "By market regime at signal"))

    # Symbol concentration: is the edge one ticker's fluke?
    lines.append("### Symbol concentration (top 10 by |total R|, core variant)")
    lines.append("")
    lines.append(SLICE_HEADER)
    by_sym: Dict[str, List[float]] = {}
    for _, row in core.iterrows():
        by_sym.setdefault(row["symbol"], []).append(row["R"])
    ranked = sorted(by_sym.items(), key=lambda kv: -abs(float(np.sum(kv[1]))))
    for sym, rs in ranked[:10]:
        lines.append(_fmt_row(sym, _r_stats(rs)))
    lines.append("")

    # Same slices for the trail variant (exact, so no assumption content).
    trail = _trade_records(variants["B1 snipe→8EMA trail (exact)"])
    lines.append("## Trail-exit variant slices (B1, exact on daily bars)")
    lines.append("")
    lines.extend(_slice_table(trail, lambda r: r.get("setup"), "By setup type"))
    lines.extend(_slice_table(trail, _adr_bucket, "By ADR% at signal"))
    lines.extend(_slice_table(trail, lambda r: str(r["entry_date"].year), "By year"))

    # --- Style-faithful model (F3) slices + split-sample validation -----------
    f3 = _trade_records(variants["F3 close-entry→8EMA trail (exact)"])
    lines.append("## Close-entry + trail model (F3, exact on daily bars)")
    lines.append("")
    lines.extend(_slice_table(f3, lambda r: r.get("setup"), "By setup type"))
    lines.extend(_slice_table(f3, _adr_bucket, "By ADR% at signal"))
    lines.extend(_slice_table(f3, _chase_bucket, "By chase (close vs level, ADRs)"))
    lines.extend(_slice_table(f3, lambda r: str(r["entry_date"].year), "By year"))
    if spy_regime is not None:
        def regime_key_f3(row):
            d = row["signal_date"]
            if d in spy_regime.index:
                return "SPY > 50SMA (risk-on)" if bool(spy_regime.loc[d]) else "SPY < 50SMA (risk-off)"
            return None
        lines.extend(_slice_table(f3, regime_key_f3, "By market regime at signal"))

    lines.append("## Split-sample check (filters must hold out-of-window)")
    lines.append("")
    lines.append("Filters were chosen for trading-logic reasons; this table checks they")
    lines.append("are not an artifact of one era. 2021–2023 has no hourly data (bounded),")
    lines.append("2024–2026 is the measured window:")
    lines.append("")
    lines.append(SLICE_HEADER)
    split_at = pd.Timestamp("2024-01-01")
    for name in ("F3 close-entry→8EMA trail (exact)", "G5 F3 + all three filters"):
        df = _trade_records(variants[name])
        early = df[df["entry_date"] < split_at]
        late = df[df["entry_date"] >= split_at]
        lines.append(_fmt_row(f"{name} · 2021–2023", _r_stats(list(early["R"]))))
        lines.append(_fmt_row(f"{name} · 2024–2026", _r_stats(list(late["R"]))))
    lines.append("")

    # --- Persist ---------------------------------------------------------------
    report_path = os.path.join(OUTPUT_DIR, "EDGE_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nReport written to {report_path}", flush=True)

    for name, res in variants.items():
        slug = (name.split(" ")[0]).lower()
        trades_to_frame(res).to_csv(
            os.path.join(OUTPUT_DIR, f"edge_trades_{slug}.csv"), index=False
        )
    print(f"Per-variant trade CSVs written to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()

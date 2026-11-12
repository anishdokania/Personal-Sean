"""Summarise backtest.ranking_study output.

    python -m backtest.ranking_report backtest/output/ranking_study/signals_<...>.csv
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    n = len(r)
    if n == 0:
        return {"n": 0}
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    sd = r.std(ddof=1) if n > 1 else np.nan
    return {"n": n, "win%": round(100 * (r > 0).mean(), 1), "avgR": round(r.mean(), 3),
            "t": round(r.mean() / (sd / np.sqrt(n)), 2) if n > 1 and sd > 0 else np.nan,
            "PF": round(wins / losses, 2) if losses > 0 else np.inf, "totalR": round(r.sum(), 1)}


def table(df: pd.DataFrame, by, col: str = "R_pess") -> pd.DataFrame:
    return pd.DataFrame({k: stats(g[col]) for k, g in df.groupby(by, observed=True)}).T


def welch(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    return (a.mean() - b.mean()) / np.sqrt(a.var() / len(a) + b.var() / len(b))


def top_k(df: pd.DataFrame, rank_col: str, k: int, pool=None) -> pd.DataFrame:
    d = df if pool is None else df[pool]
    d = d.sort_values(["date", rank_col])
    return d.groupby("date").head(k)


def main(path: str) -> None:
    df = pd.read_csv(path)
    df["year"] = df["date"].str[:4]
    df["rs_bucket"] = pd.cut(df["rs"], [0, 50, 70, 80, 90, 99], labels=["<50", "50-70",
                                                                          "70-80", "80-90", "90+"])
    f = df[df["filled"]]
    pd.set_option("display.width", 160)
    print(f"Plans: {len(df)} | filled: {len(f)} ({100 * len(f) / len(df):.0f}%) | "
          f"days: {df['date'].nunique()} | {df['date'].min()} -> {df['date'].max()}\n")

    for col, name in (("R_pess", "PESSIMISTIC (entry bar touching stop = stopped)"),
                      ("R_opt", "OPTIMISTIC (entry bar only stops on a gap)")):
        print(f"=== {name} ===")
        print("All filled:", stats(f[col]))
        print("\nBy group:\n", table(f, "kind", col))
        print(f"  sector vs outside Welch t = {welch(f[f.kind == 'sector'][col], f[f.kind == 'outside'][col]):.2f}")
        print(f"  in-group vs outside Welch t = {welch(f[f.kind != 'outside'][col], f[f.kind == 'outside'][col]):.2f}")
        print("\nBy RS bucket:\n", table(f, "rs_bucket", col))
        print("\nBy regime (0 = SPY/QQQ below all EMAs):\n", table(f, "regime", col))
        print("\nBy year x group (avgR / n):")
        piv = f.groupby(["year", "kind"])[col].agg(["mean", "size"]).round(3).unstack()
        print(piv)
        print()

    print("=== Daily picks: what the top of the list would have traded ===")
    for k in (1, 3, 5):
        old = top_k(df, "old_rank", k)
        new = top_k(df, "new_rank", k)
        lead_only = top_k(df, "new_rank", k, pool=df["kind"] != "outside")
        rows = {}
        for name, picks in (("old (R:R)", old), ("new (groups+RS)", new),
                            ("new, leading groups only", lead_only)):
            fp = picks[picks["filled"]]
            rows[name] = {**stats(fp["R_pess"]), "avgR_opt": round(fp["R_opt"].mean(), 3)}
        print(f"\nTop {k} per day:\n", pd.DataFrame(rows).T)
        # Paired by day: mean R of new picks minus old picks.
        a = old[old.filled].groupby("date")["R_pess"].mean()
        b = new[new.filled].groupby("date")["R_pess"].mean()
        both = pd.concat([a, b], axis=1, keys=["old", "new"]).dropna()
        diff = both["new"] - both["old"]
        diff = diff[diff != 0]
        if len(diff) > 1:
            print(f"  paired days that differ: {len(diff)}, new-old = {diff.mean():+.3f}R/trade, "
                  f"t = {diff.mean() / (diff.std() / np.sqrt(len(diff))):.2f}")


if __name__ == "__main__":
    main(sys.argv[1])

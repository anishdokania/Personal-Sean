"""Does the sector / theme / RS ranking pick better trades than the old order?

Replays the daily watchlist on every trading day of the window, point in time:

  1. the exact live rules (``daily_watchlist.evaluate``) on the last 275 bars
     each day, keeping TRIGGERED names (the only ones with a trade plan);
  2. the exact live ranking (``watchlist_ranking``): leading sectors from the
     SPDR ETFs, themes and RS from the liquid universe, all as of that close;
  3. each plan traded on its own: limit at the reclaim level for 3 sessions,
     stop / swing-high target, 15-day time stop, stop first on ambiguous bars.

Then it compares groups (leading sector / theme / outside), RS buckets, regime,
and the names each order would have put on top every day.

Run from the repo root:

    python -m backtest.ranking_study --start 2021-06-01

Known biases, stated in the report: the universe is today's liquid names
(survivorship) and sector/industry labels are today's.
"""

from __future__ import annotations

import argparse
import csv
import math
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

import daily_watchlist as dw
import watchlist_ranking as wr
from backtest.config import BacktestConfig

OUT = Path(__file__).parent / "output" / "ranking_study"
WINDOW_BARS = 275          # ~400 calendar days: what the live run downloads
ENTRY_VALID_DAYS = 3
MAX_HOLD_DAYS = 15
SLIPPAGE_BPS = 5.0
CHUNK = 120


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def _download(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        raw = yf.download(chunk, start=start, end=end, interval="1d", auto_adjust=True,
                          progress=False, group_by="ticker", threads=True)
        frames.update(dw._split_batch(raw, chunk))
        print(f"  {min(i + CHUNK, len(symbols))}/{len(symbols)} downloaded", flush=True)
    return frames


def load_bars(name: str, symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"{name}_{start}_{end}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    frames = _download(symbols, start, end)
    cache.write_bytes(pickle.dumps(frames))
    return frames


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
def _candidate_bars(df: pd.DataFrame, side: str = "call") -> np.ndarray:
    """Cheap, loose prefilter; every survivor is re-checked by the live rules.

    Liquidity is read on the real chart, the pattern on the mirrored one for puts.
    """
    real_c, v = df["Close"], df["Volume"]
    vol20 = v.rolling(20).mean()
    liquid = ((real_c > dw.MIN_PRICE) & (vol20 >= dw.MIN_AVG_VOLUME_20D)
              & (vol20 * real_c >= dw.MIN_AVG_DOLLAR_VOLUME_20D))
    if side == "put":
        df = dw.mirror_frame(df)
    c, h, l = df["Close"], df["High"], df["Low"]
    adrp = ((h / l.replace(0, np.nan)).rolling(20).mean() - 1) * 100
    ema8 = c.ewm(span=8, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    rng = (h - l).replace(0, np.nan)
    undercut = (l < l.shift(1)) | (l < ema8 * 1.01) | (l < ema21 * 1.01)
    mask = (liquid & (adrp >= dw.MIN_ADR_PCT * 0.95)
            & (c > ema50 * 0.98) & ((c - l) / rng >= 0.38) & undercut)
    return np.flatnonzero(mask.to_numpy())


def triggered_signals(frames: dict[str, pd.DataFrame], first_day: pd.Timestamp,
                      side: str = "call") -> dict:
    """date -> list[WatchRow] of TRIGGERED names on that close."""
    rule = dw.evaluate_put if side == "put" else dw.evaluate
    config = BacktestConfig(adr_lookback=dw.ADR_LOOKBACK, min_adr_pct=dw.MIN_ADR_PCT)
    by_day: dict[pd.Timestamp, list] = {}
    for n, (symbol, df) in enumerate(frames.items(), 1):
        for i in _candidate_bars(df, side):
            day = df.index[i]
            if day < first_day or i + 1 < dw.MIN_BARS_REQUIRED:
                continue
            row = rule(symbol, df.iloc[max(0, i + 1 - WINDOW_BARS) : i + 1], config)
            if row is not None and row.bucket == "TRIGGERED":
                by_day.setdefault(day, []).append(row)
        if n % 250 == 0:
            print(f"  signals: {n}/{len(frames)} symbols", flush=True)
    return by_day


# --------------------------------------------------------------------------- #
# Point-in-time leadership
# --------------------------------------------------------------------------- #
class LeadershipReplay:
    def __init__(self, frames, reference, industry_map, side: str = "call"):
        self.imap, self.side = industry_map, side
        sign = -1 if side == "put" else 1  # puts rank the weakest first
        close = pd.DataFrame({s: f["Close"] for s, f in frames.items()})
        volume = pd.DataFrame({s: f["Volume"] for s, f in frames.items()})
        self.liquid = ((close > wr.LIQUID_MIN_PRICE)
                       & (volume.rolling(20).mean() > wr.LIQUID_MIN_AVG_VOLUME)
                       & (close.notna().cumsum() > wr.WINDOWS["3M"]))
        self.stock_ret = {w: sign * 100 * (close / close.shift(b) - 1)
                          for w, b in wr.WINDOWS.items()}
        etf = pd.DataFrame({sec: reference[t]["Close"] for sec, t in wr.SECTOR_ETFS.items()
                            if t in reference})
        self.etf_ret = {w: sign * 100 * (etf / etf.shift(b) - 1) for w, b in wr.WINDOWS.items()}
        index = {k: reference[k]["Close"] for k in ("SPY", "QQQ")}
        self.call_regime = self._regime_series(index)
        self.put_regime = self._regime_series({k: 1.0 / c for k, c in index.items()})
        self.regime = self.put_regime if side == "put" else self.call_regime

    def day_side(self, day: pd.Timestamp) -> str:
        """What the live trade_side() would have said that day."""
        if int(self.call_regime.get(day, 0)) >= 1:
            return "call"
        return "put" if int(self.put_regime.get(day, 0)) >= 1 else "both"

    @staticmethod
    def _regime_series(closes: dict[str, pd.Series]) -> pd.Series:
        states = []
        for c in closes.values():
            e8, e21, e50 = (c.ewm(span=s, adjust=False).mean() for s in (8, 21, 50))
            s = pd.Series(0, index=c.index)
            s[c > e50] = 1
            s[(c > e21) & (c > e50)] = 2
            s[(c > e8) & (c > e21) & (c > e50)] = 3
            states.append(s)
        return pd.concat(states, axis=1).min(axis=1)

    def on(self, day: pd.Timestamp) -> wr.Leadership:
        liquid = self.liquid.loc[day]
        names = liquid.index[liquid.to_numpy()]
        returns = pd.DataFrame({w: r.loc[day, names] for w, r in self.stock_ret.items()}).dropna()
        sectors = wr.overlap_leaders(
            pd.DataFrame({w: r.loc[day] for w, r in self.etf_ret.items()}).dropna(),
            wr.LEADING_SECTORS_MAX)
        leading = list(sectors.index[sectors["leading"]])
        themes = (wr.industry_leadership(returns, self.imap, exclude_sectors=leading)
                  if wr.USE_THEMES else pd.DataFrame())
        level = int(self.regime.get(day, 0))
        label, advice = wr.REGIMES[level]
        # returns are already sign-flipped for puts, so rank them as calls.
        return wr.Leadership(sectors=sectors, themes=themes, rs=wr.rs_ratings(returns),
                             regime=wr.Regime(level, label, advice, {}), side=self.side)


# --------------------------------------------------------------------------- #
# Trade simulation (same fill/exit rules as backtest.engine)
# --------------------------------------------------------------------------- #
def _negate(df: pd.DataFrame) -> pd.DataFrame:
    """A short on the real chart is a long on the negated one (highs and lows swap)."""
    return pd.DataFrame({"Open": -df["Open"], "High": -df["Low"], "Low": -df["High"],
                         "Close": -df["Close"]}, index=df.index)


def simulate(df: pd.DataFrame, day: pd.Timestamp, entry: float, stop: float,
             target: float, pessimistic: bool, side: str = "call") -> Optional[dict]:
    if side == "put":
        df, entry, stop, target = _negate(df), -entry, -stop, -target
    i0 = df.index.get_loc(day)
    bars = df.iloc[i0 + 1 :]
    risk_ref = entry - stop
    for k in range(min(ENTRY_VALID_DAYS, len(bars))):
        o, h, l = (float(bars[x].iloc[k]) for x in ("Open", "High", "Low"))
        if l > entry:
            continue
        fill = min(o, entry)
        if o <= stop or (pessimistic and l <= stop):
            return _trade(fill, min(o, stop), risk_ref, bars.index[k], bars.index[k], "stop_same_day")
        held = 0
        for j in range(k + 1, len(bars)):
            o, h, l, c = (float(bars[x].iloc[j]) for x in ("Open", "High", "Low", "Close"))
            held += 1
            for cond, px, why in ((o <= stop, o, "stop_gap"), (o >= target, o, "target_gap"),
                                  (l <= stop, stop, "stop"), (h >= target, target, "target"),
                                  (held >= MAX_HOLD_DAYS, c, "time")):
                if cond:
                    return _trade(fill, px, risk_ref, bars.index[k], bars.index[j], why)
        return None  # still open at the end of the data
    return {"filled": False, "risk_ref": risk_ref}


def _trade(entry, exit_px, risk, entry_day, exit_day, reason) -> dict:
    exit_px -= abs(exit_px) * SLIPPAGE_BPS / 10_000  # always against us, long or short
    return {"filled": True, "entry_day": entry_day, "exit_day": exit_day, "reason": reason,
            "R": (exit_px - entry) / risk}  # in units of the planned risk


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2021-06-01", help="data start (first ~120 bars warm up)")
    p.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    p.add_argument("--side", choices=["call", "put"], default="call")
    args = p.parse_args()

    with wr.INDUSTRY_CACHE.open() as fh:
        symbols = [r["symbol"] for r in csv.DictReader(fh)]
    industry_map = wr.load_industry_map(symbols, max_fetch=0)
    print(f"{len(symbols)} symbols, {sum(1 for v in industry_map.values() if v[1])} with "
          f"industry", flush=True)

    frames = load_bars("stocks", symbols, args.start, args.end)
    reference = load_bars("reference", list(wr.SECTOR_ETFS.values()) + ["SPY", "QQQ"], args.start, args.end)
    calendar = reference["SPY"].index
    first_day = calendar[130]  # 63-bar returns + EMA50 warm-up
    print(f"bars for {len(frames)} symbols; test {first_day.date()} -> {calendar[-1].date()}")

    signals = triggered_signals(frames, first_day, args.side)
    replay = LeadershipReplay(frames, reference, industry_map, args.side)

    records = []
    for day in sorted(signals):
        rows = signals[day]
        old = sorted(rows, key=lambda r: -r.rank_score)
        old_rank = {r.symbol: i for i, r in enumerate(old, 1)}
        lead = replay.on(day)
        ranked = wr.rank_rows(rows, lead, industry_map)
        for new_i, r in enumerate(ranked, 1):
            df = frames[r.symbol]
            pess = simulate(df, day, r.entry, r.stop, r.target, True, args.side)
            opt = simulate(df, day, r.entry, r.stop, r.target, False, args.side)
            if pess is None or opt is None:
                continue
            kind = ("sector" if r.group_order[0] == 0 else "theme" if r.group_order[0] == 1
                    else "outside")
            records.append({
                "date": day.date().isoformat(), "symbol": r.symbol, "setup": r.setup,
                "group": r.group, "kind": kind, "sector": r.sector, "industry": r.industry,
                "rs": r.rs, "score": r.score, "rr": r.rr, "risk_pct": r.risk_pct,
                "adr_pct": r.adr_pct, "quality": wr.setup_quality(r),
                "regime": lead.regime.level, "n_today": len(rows), "side": args.side,
                "day_side": replay.day_side(day),
                "old_rank": old_rank[r.symbol], "new_rank": new_i,
                "filled": pess["filled"], "reason": pess.get("reason"),
                "R_pess": pess.get("R"), "R_opt": opt.get("R"),
            })
    out = pd.DataFrame(records)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"signals_{args.side}_{args.start}_{args.end}.csv"
    out.to_csv(path, index=False)
    print(f"{len(out)} triggered plans, {int(out['filled'].sum())} filled -> {path}")


if __name__ == "__main__":
    main()

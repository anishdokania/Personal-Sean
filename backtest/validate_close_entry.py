"""Validate the `signal_close` entry model's live-tradability assumption.

The model enters at the UnR bar's close, which is only tradable if the setup is
already detectable by a late-session scan. Using hourly bars (coverage
2024-07-11+), this script re-evaluates every signal with the 15:30 ET price
standing in for the close:

  * persistence: would the same UnR still be flagged at 15:30?
  * drift: how far does price move from 15:30 to the close (in ADRs) — the
    execution gap between "scan fires" and the backtested fill price.

A high persistence rate + small drift means the close-entry backtest is
buyable in practice; a low rate would mean the backtest trades signals a live
scanner could not have seen. Run:

    python -m backtest.validate_close_entry
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .engine import generate_signals
from .localdata import HourlyStore, LocalDataProvider
from .universe_movers import MOVERS_UNIVERSE

HOURLY_START = pd.Timestamp("2024-07-11")


def main() -> None:
    config = BacktestConfig(min_adr_pct=5.0)
    provider = LocalDataProvider("2021-01-01", "2026-12-31")
    data = provider.get_many(list(MOVERS_UNIVERSE))
    hourly = HourlyStore()

    checked = persisted = 0
    drifts = []
    lost_reasons: dict = {}

    for sym, df in data.items():
        sigs = generate_signals(sym, df, config)
        closes = df["Close"]
        for date, sig in sigs.items():
            if date < HOURLY_START:
                continue
            session = hourly.session(sym, date)
            if session is None or len(session) < 7:
                continue
            i = df.index.get_loc(date)
            if i < 55:
                continue

            # State as of 15:30 ET: last hourly bar's open is the 15:30 print;
            # day high/low so far exclude the final half-hour.
            p1530 = float(session.iloc[-1]["Open"])
            day_low = float(session.iloc[:-1]["Low"].min())
            day_high = float(session.iloc[:-1]["High"].max())

            # Intraday EMA estimate at 15:30: previous session's EMA advanced
            # one step with the 15:30 price (exactly what a live scan computes).
            def ema_at(series: pd.Series, span: int) -> float:
                prev = float(series.iloc[: i].ewm(span=span, adjust=False).mean().iloc[-1])
                alpha = 2.0 / (span + 1)
                return alpha * p1530 + (1 - alpha) * prev

            ema8 = ema_at(closes, 8)
            ema21 = ema_at(closes, 21)
            ema50 = ema_at(closes, 50)
            ema21_series = closes.iloc[: i].ewm(span=21, adjust=False).mean()
            ema21_prev = float(ema21_series.iloc[-5])  # ~5 bars back of the live value
            pdl = float(df["Low"].iloc[i - 1])

            checked += 1
            reasons = []
            if not (p1530 > ema50 and ema21 >= ema21_prev):
                reasons.append("regime")
            if not (day_high > day_low and
                    (p1530 - day_low) / (day_high - day_low) >= 0.4):
                reasons.append("close_position")
            unr_ok = any(day_low < ref < p1530 for ref in (pdl, ema8, ema21))
            if not unr_ok:
                reasons.append("no_unr")

            if not reasons:
                persisted += 1
                adr = float((df["High"] - df["Low"]).iloc[max(0, i - 19): i + 1].mean())
                if adr > 0:
                    drifts.append((float(closes.iloc[i]) - p1530) / adr)
            else:
                for r in reasons:
                    lost_reasons[r] = lost_reasons.get(r, 0) + 1

    print(f"Signals checked (hourly coverage): {checked}")
    print(f"Detectable at 15:30 ET:            {persisted} ({persisted / max(checked,1):.1%})")
    print(f"Lost-signal reasons: {lost_reasons}")
    if drifts:
        d = np.array(drifts)
        print("\nDrift 15:30 -> close, in ADRs (positive = close higher, i.e. the")
        print("backtested fill is worse than the scan price — conservative):")
        print(f"  mean {d.mean():+.3f} | median {np.median(d):+.3f} | "
              f"p10 {np.quantile(d, 0.1):+.3f} | p90 {np.quantile(d, 0.9):+.3f}")


if __name__ == "__main__":
    main()

"""Native "sniper" entry detector for the Sean / Options Cartel style.

The entry is a snipe at an undercut-and-reclaim (UnR): price pokes below a
reference level -- the previous day's low (PDL), the 8 EMA, or the 21 EMA --
and then reclaims it (closes back above). The trade plan that follows is:

  * ENTRY  = a next-day limit back at the reclaimed level (a tight pullback fill),
  * STOP   = just under the undercut low (very small risk),
  * TARGET = the nearest swing high above (set in signals.py).

This is the opposite of chasing a breakout of the whole bar: by sniping the
reclaim level the stop sits right under the undercut, so risk is tiny and the
reward-to-risk is driven by how far the next structural high sits above.

Detection is point-in-time on window = bars[0..i]; the last bar is the UnR bar.
True fidelity is intraday; on daily bars we approximate the undercut with the
bar's low and the reclaim with its close.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .adr import adr_abs
from .config import BacktestConfig


@dataclass
class EntryResult:
    entry_type: str        # e.g. "unr_pdl", "unr_ema8", "unr_ema21"
    reclaim_level: float   # the level that was undercut then reclaimed -> limit entry
    undercut_low: float    # the low of the undercut -> stop reference


def _emas(window: pd.DataFrame):
    close = window["Close"]
    return (
        close.ewm(span=8, adjust=False).mean(),
        close.ewm(span=21, adjust=False).mean(),
        close.ewm(span=50, adjust=False).mean(),
    )


def detect_entry(window: pd.DataFrame, config: BacktestConfig) -> Optional[EntryResult]:
    """Return the UnR snipe firing on the last bar, or None."""
    n = len(window)
    if n < 55:
        return None
    adr = adr_abs(window, config.adr_lookback)
    if adr is None:
        return None

    ema8s, ema21s, ema50s = _emas(window)
    ema8 = float(ema8s.iloc[-1]); ema21 = float(ema21s.iloc[-1]); ema50 = float(ema50s.iloc[-1])
    ema21_prev = float(ema21s.iloc[-6])

    o = float(window["Open"].iloc[-1]); h = float(window["High"].iloc[-1])
    l = float(window["Low"].iloc[-1]); c = float(window["Close"].iloc[-1])
    pdl = float(window["Low"].iloc[-2])

    # Constructive long regime: above the 50 EMA with a rising intermediate trend.
    if not (c > ema50 and ema21 >= ema21_prev):
        return None
    # The reclaim bar should close in the upper part of its range (buyers won).
    if not (h > l and (c - l) / (h - l) >= 0.4):
        return None

    # A reference is "UnR" if the bar undercut it (low below) and reclaimed it
    # (closed above). Prefer PDL, then the 8 EMA, then the 21 EMA. The chosen
    # reference must sit below the close so the next-day limit is a real pullback.
    for name, ref in (("pdl", pdl), ("ema8", ema8), ("ema21", ema21)):
        if l < ref < c:
            return EntryResult(f"unr_{name}", reclaim_level=float(ref), undercut_low=l)

    return None

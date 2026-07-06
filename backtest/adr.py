"""Average Daily Range helpers (point-in-time).

ADR% is the core universe filter in the Sean / Options Cartel style -- every
example chart shows it. High ADR% means the stock moves enough that a tight
8 EMA stop and a 3-4 day hold can still produce meaningful R.

ADR%  = 100 * (mean(High/Low) over n - 1)   -- the standard TradingView ADR%.
ADR$  = mean(High - Low) over n              -- absolute range, used to size stops.

Both use only bars up to the as-of position (no look-ahead).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def adr_pct(window: pd.DataFrame, n: int = 20) -> Optional[float]:
    """Average Daily Range as a percent of price over the last `n` bars."""
    if len(window) < n + 1:
        return None
    high = window["High"].tail(n)
    low = window["Low"].tail(n).replace(0, np.nan)
    ratio = (high.values / low.values)
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        return None
    return float((ratio.mean() - 1.0) * 100.0)


def adr_abs(window: pd.DataFrame, n: int = 20) -> Optional[float]:
    """Average absolute daily range (High - Low) over the last `n` bars."""
    if len(window) < n:
        return None
    rng = (window["High"] - window["Low"]).tail(n)
    val = float(rng.mean())
    return val if np.isfinite(val) and val > 0 else None

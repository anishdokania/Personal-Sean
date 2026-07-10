"""Intrabar ambiguity resolution using hourly bars.

Daily OHLC cannot order events inside the bar. Two facts keep most days exact
anyway (stop S sits strictly below the limit entry L):

  * A continuous path that touches S has already crossed L on the way down, so
    "daily low <= S" on the entry day means the limit filled AND the stop hit —
    that is certain, not pessimistic.
  * Gap exits (open beyond stop/target) are printed at the open — also certain.

What remains genuinely unknowable from daily bars:

  A. entry day touching both S and target T after the fill (stop-vs-target order),
  B. entry day touching T but not S (did the target print before or after the
     pullback that filled the limit?),
  C. any later day touching both S and T.

These functions replay the day's hourly bars to resolve A/B/C. Within a single
hourly bar the same ambiguity can recur at a smaller scale; that residual case
returns None and the caller falls back to the configured pessimistic /
optimistic bound (and counts it, so reports show exactly how much of the result
rests on assumption).

yfinance hourly data mismatches daily extremes by a few cents sometimes; any
inconsistency between what the daily bar implies and what the hourly session
shows also returns None rather than trusting either side.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def resolve_entry_day(
    bars: pd.DataFrame,
    limit: float,
    stop: float,
    target: Optional[float],
) -> Optional[str]:
    """Order of events on the day a resting limit buy (at `limit`, stop `stop`,
    optional intrabar `target`) may fill.

    Returns one of "no_fill", "fill_held", "fill_stopped", "fill_target",
    or None when hourly bars cannot order the events either.
    """
    filled = False
    for row in bars.itertuples():
        o, h, l = float(row.Open), float(row.High), float(row.Low)
        if not filled:
            if o <= limit or l <= limit:
                filled = True
                # Events in the remainder of the fill bar:
                hit_stop = l <= stop
                hit_target = target is not None and h >= target
                if hit_stop and hit_target:
                    return None  # ambiguous inside one hourly bar
                if hit_stop:
                    return "fill_stopped"  # S < L: any path to S crossed L first
                if hit_target:
                    # High may have printed before the pullback that filled us.
                    return None
            continue
        # Position open from a previous bar.
        if o <= stop:
            return "fill_stopped"
        if target is not None and o >= target:
            return "fill_target"
        hit_stop = l <= stop
        hit_target = target is not None and h >= target
        if hit_stop and hit_target:
            return None
        if hit_stop:
            return "fill_stopped"
        if hit_target:
            return "fill_target"
    return "fill_held" if filled else "no_fill"


def resolve_position_day(
    bars: pd.DataFrame,
    stop: float,
    target: float,
) -> Optional[str]:
    """Order of stop vs target on a day an open position's daily bar touched
    both. Returns "stopped", "target", or None (unresolvable / inconsistent)."""
    for row in bars.itertuples():
        o, h, l = float(row.Open), float(row.High), float(row.Low)
        if o <= stop:
            return "stopped"
        if o >= target:
            return "target"
        hit_stop = l <= stop
        hit_target = h >= target
        if hit_stop and hit_target:
            return None
        if hit_stop:
            return "stopped"
        if hit_target:
            return "target"
    # Daily bar said both levels were touched but hourly shows neither: data
    # mismatch — refuse to resolve.
    return None

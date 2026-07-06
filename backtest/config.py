"""Tunable parameters for the backtest engine.

Everything that controls how a setup becomes a trade lives here so the
assumptions are explicit and auditable, exactly like the live scanner's gate
thresholds in main.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


# --- Gate thresholds (mirror the live scanner) -------------------------------
# These match main.py / focus gate so the backtest selects the same setups the
# live system would have flagged.
HARD_GATE_MIN_PRICE = 5.0
HARD_GATE_MIN_ATR14 = 1.5
HARD_GATE_MIN_AVG_VOLUME = 1_000_000
# Market cap is point-in-time-unsafe from yfinance (it returns *today's* cap),
# so we approximate the "real, liquid company" intent with a dollar-volume floor
# instead. price * avg_volume >= this.
HARD_GATE_MIN_DOLLAR_VOLUME = 20_000_000

FOCUS_GATE_MIN_TODAY = 70
FOCUS_GATE_MIN_STRUCTURE = 65
FOCUS_GATE_MIN_BLUEPRINT = 65
# Live focus gate also rejects sector_alignment_score < 45 (main.py:991).
FOCUS_GATE_MIN_SECTOR = 45


# --- Trade model -------------------------------------------------------------
@dataclass
class BacktestConfig:
    # Universe + window
    symbols: List[str] = field(default_factory=list)
    start: str = "2021-01-01"
    end: str = "2024-12-31"

    # Entry model: next-day LIMIT back at the reclaimed level (sniped pullback).
    entry_valid_days: int = 3          # how long the pending limit order stays armed
    slippage_bps: float = 5.0          # applied on exits (limit entries fill at the level)
    commission_per_share: float = 0.0  # set >0 to model per-share commission

    # Snipe stop / target model.
    snipe_stop_buffer_adr: float = 0.05  # stop sits this many ADRs below the undercut low
    swing_lookback: int = 40           # bars to search for the nearest overhead swing high
    swing_pivot_k: int = 3             # a swing high is a local max over +/- k bars
    min_rr: float = 1.0                # require target at least this many R above entry

    # Universe: ADR% filter (Sean style trades high-ADR movers only).
    min_adr_pct: float = 5.0           # require Average Daily Range >= this % of price
    adr_lookback: int = 20

    # Stop model: 8 EMA-anchored tight stop (not a wide base support).
    # Stop = swing low of the last `stop_swing_lookback` bars, but clamped so it
    # never sits more than `stop_max_adr_below_ema` ADRs below the 8 EMA -- i.e.
    # the stop stays tight to the 8 EMA the way the style requires.
    use_ema_stop: bool = True
    stop_ema_span: int = 8
    stop_swing_lookback: int = 3
    stop_buffer_adr: float = 0.25      # place the EMA floor this many ADRs below the 8 EMA
    stop_max_adr_below_ema: float = 0.75  # cap: stop can't be deeper than this many ADRs below 8 EMA
    # No-chase rule: reject entries too far ABOVE the 8 EMA, so the 8 EMA stop
    # stays tight. This is the other half of "stop can't be far below the 8 EMA".
    max_entry_adr_above_ema: float = 1.0

    # Native entry models (8 EMA hold / reclaim / ignition-tight) instead of the
    # old mega-cap blueprint scoring, which almost never fires on high-ADR names.
    use_native_entry: bool = True
    entry_pullback_lookback: int = 4     # bars back to look for an 8 EMA tag
    entry_ema8_touch_tol: float = 0.5    # ADRs of tolerance for "touched the 8 EMA"
    ignition_lookback: int = 10          # bars back to find an ignition candle
    ignition_range_adr: float = 1.5      # ignition candle range >= this many ADRs

    # Risk / exit model
    target_r_multiple: float = 2.0     # profit target = entry + R * (entry - stop)
    max_hold_days: int = 15            # time stop: exit at close after N trading days
    max_risk_pct_of_price: float = 0.15  # skip setups whose stop is >15% away (too wide)

    # Intrabar ambiguity: if both stop and target are touched the same day and we
    # only have daily OHLC, assume the stop filled first (pessimistic / honest).
    stop_first_on_ambiguous_bar: bool = True

    # Limit entries fill on an intraday pullback to the reclaim level; on daily
    # bars we can't tell whether price then bounced or ran to the (very tight)
    # stop. With True we assume it also stopped that bar (pessimistic bound);
    # with False we assume the fill held into the close unless it gapped below
    # the stop at the open (optimistic bound). The truth needs intraday data.
    entry_bar_same_day_stop: bool = True

    # Portfolio / sizing
    starting_equity: float = 100_000.0
    risk_pct_per_trade: float = 0.01   # risk 1% of equity per trade
    max_concurrent_positions: int = 10
    one_position_per_symbol: bool = True

    # Data requirements
    min_bars_for_signal: int = 80      # need enough history for EMA50 / S-R lookbacks


DEFAULT_CONFIG = BacktestConfig()

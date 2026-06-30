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


# --- Trade model -------------------------------------------------------------
@dataclass
class BacktestConfig:
    # Universe + window
    symbols: List[str] = field(default_factory=list)
    start: str = "2021-01-01"
    end: str = "2024-12-31"

    # Entry model: breakout above the trigger level.
    entry_valid_days: int = 5          # how long a pending breakout order stays armed
    slippage_bps: float = 5.0          # per fill, in basis points of price
    commission_per_share: float = 0.0  # set >0 to model per-share commission

    # Risk / exit model
    target_r_multiple: float = 2.0     # profit target = entry + R * (entry - stop)
    max_hold_days: int = 15            # time stop: exit at close after N trading days
    max_risk_pct_of_price: float = 0.15  # skip setups whose stop is >15% away (too wide)

    # Intrabar ambiguity: if both stop and target are touched the same day and we
    # only have daily OHLC, assume the stop filled first (pessimistic / honest).
    stop_first_on_ambiguous_bar: bool = True

    # Portfolio / sizing
    starting_equity: float = 100_000.0
    risk_pct_per_trade: float = 0.01   # risk 1% of equity per trade
    max_concurrent_positions: int = 10
    one_position_per_symbol: bool = True

    # Data requirements
    min_bars_for_signal: int = 80      # need enough history for EMA50 / S-R lookbacks


DEFAULT_CONFIG = BacktestConfig()

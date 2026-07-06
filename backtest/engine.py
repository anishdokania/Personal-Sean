"""Event-driven portfolio backtest.

Two passes:
  1. Signal pass (expensive): walk each symbol's history and record every bar
     that qualifies as a fresh setup, with its trigger/invalidation levels.
  2. Portfolio pass: walk the unified calendar day by day, arming breakout
     orders from signals, filling entries, managing stops/targets/time-stops,
     and marking equity to market -- all with position sizing and concurrency
     limits applied against live equity.

Fills use daily OHLC, so intrabar ordering is unknowable. Every ambiguous case
is resolved pessimistically (stop assumed to fill before target; same-day stop
checked on the entry bar; entry slippage adverse). This biases results toward
being conservative rather than flattering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .config import BacktestConfig
from .sector import SectorRanker
from .signals import Signal, evaluate_bar


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    shares: int
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    setup_type: Optional[str] = None
    bars_held: int = 0

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.stop

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def r_multiple(self) -> Optional[float]:
        if self.exit_price is None or self.risk_per_share <= 0:
            return None
        return (self.exit_price - self.entry_price) / self.risk_per_share


@dataclass
class PendingOrder:
    signal: Signal
    armed_until: pd.Timestamp


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity_curve: pd.Series
    config: BacktestConfig
    signal_count: int


def _slip_buy(price: float, bps: float) -> float:
    return price * (1 + bps / 10_000.0)


def _slip_sell(price: float, bps: float) -> float:
    return price * (1 - bps / 10_000.0)


def generate_signals(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
) -> Dict[pd.Timestamp, Signal]:
    """Pass 1: every qualifying setup bar for one symbol (point-in-time)."""
    out: Dict[pd.Timestamp, Signal] = {}
    for i in range(len(df)):
        sig = evaluate_bar(symbol, df, i, config, sector_ranker=sector_ranker)
        if sig is not None:
            out[df.index[i]] = sig
    return out


def run_backtest(
    data: Dict[str, pd.DataFrame],
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
) -> BacktestResult:
    # --- Pass 1: signals -----------------------------------------------------
    print("Generating signals (point-in-time)...", flush=True)
    signals_by_symbol: Dict[str, Dict[pd.Timestamp, Signal]] = {}
    signal_count = 0
    for n, (sym, df) in enumerate(data.items(), 1):
        sigs = generate_signals(sym, df, config, sector_ranker=sector_ranker)
        signals_by_symbol[sym] = sigs
        signal_count += len(sigs)
        print(f"  [{n}/{len(data)}] {sym}: {len(sigs)} setups", flush=True)
    print(f"Total setups found: {signal_count}", flush=True)

    # Unified trading calendar across all symbols.
    all_dates = sorted({d for df in data.values() for d in df.index})
    if not all_dates:
        return BacktestResult([], pd.Series(dtype=float), config, 0)

    # --- Pass 2: portfolio simulation ---------------------------------------
    cash = config.starting_equity
    open_positions: Dict[str, Trade] = {}      # symbol -> open Trade
    pending: Dict[str, PendingOrder] = {}      # symbol -> armed breakout order
    closed: List[Trade] = []
    equity_points: List[float] = []
    equity_index: List[pd.Timestamp] = []

    def bar_for(sym: str, day: pd.Timestamp) -> Optional[pd.Series]:
        df = data[sym]
        if day in df.index:
            return df.loc[day]
        return None

    for day in all_dates:
        # 1) Manage open positions (exits) -----------------------------------
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            row = bar_for(sym, day)
            if row is None:
                continue
            if day <= pos.entry_date:
                continue  # entry bar handled at fill time

            o, h, l, c = (
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
            )
            pos.bars_held += 1
            exit_price = None
            reason = None

            if o <= pos.stop:                       # gap down through stop
                exit_price, reason = o, "stop_gap"
            elif o >= pos.target:                   # gap up through target
                exit_price, reason = o, "target_gap"
            elif l <= pos.stop and h >= pos.target:  # ambiguous bar
                if config.stop_first_on_ambiguous_bar:
                    exit_price, reason = pos.stop, "stop_ambiguous"
                else:
                    exit_price, reason = pos.target, "target_ambiguous"
            elif l <= pos.stop:
                exit_price, reason = pos.stop, "stop"
            elif h >= pos.target:
                exit_price, reason = pos.target, "target"
            elif pos.bars_held >= config.max_hold_days:
                exit_price, reason = c, "time_stop"

            if exit_price is not None:
                fill = _slip_sell(exit_price, config.slippage_bps)
                cash += pos.shares * fill - config.commission_per_share * pos.shares
                pos.exit_date = day
                pos.exit_price = fill
                pos.exit_reason = reason
                closed.append(pos)
                del open_positions[sym]

        # 2) Fill pending LIMIT orders (sniped pullback to the reclaim level) --
        for sym in list(pending.keys()):
            order = pending[sym]
            if day <= order.signal.date:
                continue  # arm starting the day AFTER the signal close
            if day > order.armed_until:
                del pending[sym]
                continue
            if config.one_position_per_symbol and sym in open_positions:
                del pending[sym]
                continue
            if len(open_positions) >= config.max_concurrent_positions:
                continue  # no slot today; keep armed

            row = bar_for(sym, day)
            if row is None:
                continue
            o, h, l = float(row["Open"]), float(row["High"]), float(row["Low"])
            limit = order.signal.trigger_level
            if l > limit:
                continue  # price never pulled back to the limit today; keep armed

            # Limit buy: a gap below the limit fills at the (better) open; else at
            # the limit. No adverse buy slippage -- a limit fills at its price or
            # better.
            entry = o if o <= limit else limit
            stop = order.signal.invalidation_level
            per_share_risk = entry - stop
            if per_share_risk <= 0:
                del pending[sym]
                continue

            equity = cash + sum(
                p.shares * float(bar_for(s, day)["Close"])
                for s, p in open_positions.items()
                if bar_for(s, day) is not None
            )
            risk_dollars = equity * config.risk_pct_per_trade
            shares = int(math.floor(risk_dollars / per_share_risk))
            if shares <= 0:
                del pending[sym]
                continue
            cost = shares * entry + config.commission_per_share * shares
            if cost > cash:  # cash account, no leverage
                shares = int(math.floor((cash * 0.99) / entry))
                if shares <= 0:
                    continue
                cost = shares * entry + config.commission_per_share * shares

            # Structural target (nearest swing high); fall back to an R multiple
            # for the legacy path where no target level is attached.
            if order.signal.target_level is not None:
                target = float(order.signal.target_level)
            else:
                target = entry + config.target_r_multiple * per_share_risk
            trade = Trade(
                symbol=sym,
                entry_date=day,
                entry_price=entry,
                stop=stop,
                target=target,
                shares=shares,
                setup_type=order.signal.setup_type,
            )

            # Same-day stop on the entry bar. Pessimistic: any touch of the stop
            # counts. Optimistic: only a gap open below the stop counts (the limit
            # fill is assumed to have held into the close otherwise).
            stopped_same_day = (l <= stop) if config.entry_bar_same_day_stop else (o <= stop)
            if stopped_same_day:
                exit_at = min(o, stop) if o <= stop else stop
                fill = _slip_sell(exit_at, config.slippage_bps)
                trade.exit_date = day
                trade.exit_price = fill
                trade.exit_reason = "stop_same_day"
                cash += shares * fill - cost - config.commission_per_share * shares
                closed.append(trade)
            else:
                cash -= cost
                open_positions[sym] = trade

            del pending[sym]

        # 3) Arm new orders from signals that closed today -------------------
        for sym, sigs in signals_by_symbol.items():
            sig = sigs.get(day)
            if sig is None:
                continue
            if config.one_position_per_symbol and sym in open_positions:
                continue
            # Refresh/replace any existing pending order with the latest levels.
            armed_until = _add_trading_days(
                all_dates, day, config.entry_valid_days
            )
            pending[sym] = PendingOrder(signal=sig, armed_until=armed_until)

        # 4) Mark equity to market -------------------------------------------
        mv = 0.0
        for sym, pos in open_positions.items():
            row = bar_for(sym, day)
            px = float(row["Close"]) if row is not None else pos.entry_price
            mv += pos.shares * px
        equity_points.append(cash + mv)
        equity_index.append(day)

    equity_curve = pd.Series(equity_points, index=pd.DatetimeIndex(equity_index))
    return BacktestResult(closed, equity_curve, config, signal_count)


def _add_trading_days(
    calendar: List[pd.Timestamp], day: pd.Timestamp, n: int
) -> pd.Timestamp:
    """Return the trading day `n` sessions after `day` (clamped to calendar end)."""
    try:
        idx = calendar.index(day)
    except ValueError:
        return day
    target = min(idx + n, len(calendar) - 1)
    return calendar[target]

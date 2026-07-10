"""Event-driven portfolio backtest.

Two passes:
  1. Signal pass: walk each symbol's history and record every bar that
     qualifies as a fresh setup, with its entry/stop/target levels.
  2. Portfolio pass: walk the unified calendar day by day, arming orders from
     signals, filling entries, managing exits, and marking equity to market,
     with position sizing and concurrency limits applied against live equity.

Honesty model
-------------
Fills use daily OHLC, where intrabar ordering is only *sometimes* unknowable.
The engine is explicit about which case each decision falls into:

  * EXACT      — the daily bar fully determines the outcome. This includes the
                 counterintuitive entry-day case: the stop sits strictly below
                 the limit entry, so "daily low <= stop" means the limit filled
                 and the stop hit, with certainty.
  * HOURLY     — the daily bar was ambiguous (stop and target both touched) and
                 the day's hourly bars ordered the events.
  * PESSIMISTIC/OPTIMISTIC — no intraday data could order the events; the
                 configured bound was assumed. Running both modes brackets the
                 truth for exactly these residual trades.

Every closed trade carries its worst-case provenance in `Trade.resolution`, so
reported edge can be decomposed into "measured" vs "assumed".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import BacktestConfig
from .intraday import resolve_entry_day, resolve_position_day
from .localdata import HourlyStore
from .sector import SectorRanker
from .signals import Signal, evaluate_bar, precompute


_RES_RANK = {"exact": 0, "hourly": 1, "pessimistic": 2, "optimistic": 2}


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop: float                      # current stop (hybrid moves it to breakeven)
    target: Optional[float]
    shares: int                      # initial shares
    stop_initial: float = 0.0
    signal_date: Optional[pd.Timestamp] = None
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    setup_type: Optional[str] = None
    bars_held: int = 0
    resolution: str = "exact"        # exact | hourly | pessimistic | optimistic
    # hybrid partial leg
    partial_date: Optional[pd.Timestamp] = None
    partial_price: Optional[float] = None
    partial_shares: int = 0
    # slicing metadata from the signal
    adr_pct: Optional[float] = None
    rr_planned: Optional[float] = None
    chase_adr: Optional[float] = None
    # runtime state (not part of the result schema)
    stage: str = "full"
    below_ema_count: int = 0
    pending_stop: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.stop_initial:
            self.stop_initial = self.stop

    @property
    def remaining_shares(self) -> int:
        return self.shares - self.partial_shares

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.stop_initial

    @property
    def pnl(self) -> float:
        total = 0.0
        if self.partial_price is not None:
            total += (self.partial_price - self.entry_price) * self.partial_shares
        if self.exit_price is not None:
            total += (self.exit_price - self.entry_price) * self.remaining_shares
        return total

    @property
    def r_multiple(self) -> Optional[float]:
        if self.exit_price is None or self.risk_per_share <= 0 or self.shares <= 0:
            return None
        return self.pnl / (self.risk_per_share * self.shares)

    def note_resolution(self, res: str) -> None:
        if _RES_RANK.get(res, 0) > _RES_RANK.get(self.resolution, 0):
            self.resolution = res


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
    resolver_stats: Dict[str, int] = field(default_factory=dict)


def _slip_buy(price: float, bps: float) -> float:
    return price * (1 + bps / 10_000.0)


def _slip_sell(price: float, bps: float) -> float:
    return price * (1 - bps / 10_000.0)


class AmbiguityResolver:
    """Resolution ladder for intrabar ambiguity: hourly bars first, then the
    configured bound. Tracks how often each rung was used."""

    def __init__(self, hourly_store: Optional[HourlyStore], enabled: bool) -> None:
        self.store = hourly_store if enabled else None
        self.stats: Dict[str, int] = {
            "ambiguous_days": 0, "hourly_resolved": 0, "assumed": 0, "no_hourly_data": 0,
        }

    def _session(self, symbol: str, day: pd.Timestamp):
        if self.store is None:
            return None
        return self.store.session(symbol, day)

    def entry_day(self, symbol: str, day: pd.Timestamp, limit: float,
                  stop: float, target: Optional[float]) -> Optional[str]:
        self.stats["ambiguous_days"] += 1
        bars = self._session(symbol, day)
        if bars is None:
            self.stats["no_hourly_data"] += 1
            return None
        out = resolve_entry_day(bars, limit, stop, target)
        if out in ("fill_stopped", "fill_target", "fill_held"):
            self.stats["hourly_resolved"] += 1
            return out
        self.stats["assumed"] += 1
        return None

    def position_day(self, symbol: str, day: pd.Timestamp,
                     stop: float, target: float) -> Optional[str]:
        self.stats["ambiguous_days"] += 1
        bars = self._session(symbol, day)
        if bars is None:
            self.stats["no_hourly_data"] += 1
            return None
        out = resolve_position_day(bars, stop, target)
        if out in ("stopped", "target"):
            self.stats["hourly_resolved"] += 1
            return out
        self.stats["assumed"] += 1
        return None


def generate_signals(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
) -> Dict[pd.Timestamp, Signal]:
    """Pass 1: every qualifying setup bar for one symbol (point-in-time)."""
    out: Dict[pd.Timestamp, Signal] = {}
    pre = precompute(df, config)
    for i in range(len(df)):
        sig = evaluate_bar(symbol, df, i, config, sector_ranker=sector_ranker, pre=pre)
        if sig is not None:
            out[df.index[i]] = sig
    return out


def run_backtest(
    data: Dict[str, pd.DataFrame],
    config: BacktestConfig,
    sector_ranker: Optional[SectorRanker] = None,
    hourly_store: Optional[HourlyStore] = None,
    signals_by_symbol: Optional[Dict[str, Dict[pd.Timestamp, Signal]]] = None,
    quiet: bool = False,
) -> BacktestResult:
    # --- Pass 1: signals (reusable across portfolio-pass variants) -----------
    if signals_by_symbol is None:
        if not quiet:
            print("Generating signals (point-in-time)...", flush=True)
        signals_by_symbol = {}
        for n, (sym, df) in enumerate(data.items(), 1):
            sigs = generate_signals(sym, df, config, sector_ranker=sector_ranker)
            signals_by_symbol[sym] = sigs
            if not quiet:
                print(f"  [{n}/{len(data)}] {sym}: {len(sigs)} setups", flush=True)
    signal_count = sum(len(s) for s in signals_by_symbol.values())
    if not quiet:
        print(f"Total setups found: {signal_count}", flush=True)

    all_dates = sorted({d for df in data.values() for d in df.index})
    if not all_dates:
        return BacktestResult([], pd.Series(dtype=float), config, 0)

    resolver = AmbiguityResolver(hourly_store, config.use_hourly_resolution)
    optimistic = config.ambiguity_mode == "optimistic"
    assumed_res = "optimistic" if optimistic else "pessimistic"

    # Daily 8 EMA per symbol for the close-based trail (causal: EMA at day t
    # uses only closes <= t).
    ema8_by_symbol: Dict[str, pd.Series] = {
        sym: df["Close"].ewm(span=config.trail_ema_span, adjust=False).mean()
        for sym, df in data.items()
    }

    # --- Pass 2: portfolio simulation ---------------------------------------
    cash = config.starting_equity
    open_positions: Dict[str, Trade] = {}
    pending: Dict[str, PendingOrder] = {}
    closed: List[Trade] = []
    equity_points: List[float] = []
    equity_index: List[pd.Timestamp] = []

    def bar_for(sym: str, day: pd.Timestamp) -> Optional[pd.Series]:
        df = data[sym]
        if day in df.index:
            return df.loc[day]
        return None

    def apply_exit(sym: str, pos: Trade, day: pd.Timestamp,
                   price_raw: float, reason: str) -> None:
        nonlocal cash
        fill = _slip_sell(price_raw, config.slippage_bps)
        rem = pos.remaining_shares
        cash += rem * fill - config.commission_per_share * rem
        pos.exit_date = day
        pos.exit_price = fill
        pos.exit_reason = reason
        closed.append(pos)
        del open_positions[sym]

    def apply_partial(pos: Trade, day: pd.Timestamp, price_raw: float) -> bool:
        """Sell the partial fraction; move the stop to breakeven starting the
        NEXT session (same-day re-entry of the tighter stop would recreate the
        exact ambiguity the partial just resolved). Returns False if the
        position is too small to split."""
        nonlocal cash
        if pos.shares < 2 or pos.partial_shares > 0:
            return False
        psh = int(round(pos.shares * config.partial_fraction))
        psh = max(1, min(psh, pos.shares - 1))
        fill = _slip_sell(price_raw, config.slippage_bps)
        cash += psh * fill - config.commission_per_share * psh
        pos.partial_date = day
        pos.partial_price = fill
        pos.partial_shares = psh
        pos.stage = "runner"
        pos.pending_stop = max(pos.stop, pos.entry_price)
        return True

    def trail_hit(pos: Trade, sym: str, day: pd.Timestamp, close: float) -> bool:
        if config.exit_model not in ("trail_ema8", "hybrid"):
            return False
        ema = ema8_by_symbol[sym].get(day)
        if ema is None or not math.isfinite(float(ema)):
            return False
        if close < float(ema):
            pos.below_ema_count += 1
        else:
            pos.below_ema_count = 0
        return pos.below_ema_count >= config.trail_confirm_closes

    for day in all_dates:
        # 1) Manage open positions (exits) -----------------------------------
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            row = bar_for(sym, day)
            if row is None:
                continue
            if day <= pos.entry_date:
                continue  # entry bar handled at fill time

            if pos.pending_stop is not None:
                pos.stop = pos.pending_stop
                pos.pending_stop = None

            o, h, l, c = (float(row["Open"]), float(row["High"]),
                          float(row["Low"]), float(row["Close"]))
            pos.bars_held += 1

            # Gap exits are exact regardless of exit model.
            if o <= pos.stop:
                apply_exit(sym, pos, day, o, "stop_gap")
                continue

            if config.exit_model == "swing_target":
                assert pos.target is not None
                if o >= pos.target:
                    apply_exit(sym, pos, day, o, "target_gap")
                    continue
                hit_stop, hit_tgt = l <= pos.stop, h >= pos.target
                if hit_stop and hit_tgt:
                    res = resolver.position_day(sym, day, pos.stop, pos.target)
                    if res == "stopped":
                        pos.note_resolution("hourly")
                        apply_exit(sym, pos, day, pos.stop, "stop")
                    elif res == "target":
                        pos.note_resolution("hourly")
                        apply_exit(sym, pos, day, pos.target, "target")
                    else:
                        pos.note_resolution(assumed_res)
                        if optimistic:
                            apply_exit(sym, pos, day, pos.target, "target")
                        else:
                            apply_exit(sym, pos, day, pos.stop, "stop")
                    continue
                if hit_stop:
                    apply_exit(sym, pos, day, pos.stop, "stop")
                    continue
                if hit_tgt:
                    apply_exit(sym, pos, day, pos.target, "target")
                    continue

            elif config.exit_model == "trail_ema8":
                if l <= pos.stop:
                    apply_exit(sym, pos, day, pos.stop, "stop")
                    continue
                if trail_hit(pos, sym, day, c):
                    apply_exit(sym, pos, day, c, "trail_ema")
                    continue

            elif config.exit_model == "hybrid":
                if pos.stage == "full":
                    assert pos.target is not None
                    tp = pos.target
                    if o >= tp:
                        apply_partial(pos, day, o)
                        if l <= pos.stop:  # low prints after the open: exact
                            apply_exit(sym, pos, day, pos.stop, "stop_after_partial")
                            continue
                    else:
                        hit_stop, hit_tgt = l <= pos.stop, h >= tp
                        if hit_stop and hit_tgt:
                            res = resolver.position_day(sym, day, pos.stop, tp)
                            if res == "stopped":
                                pos.note_resolution("hourly")
                                apply_exit(sym, pos, day, pos.stop, "stop")
                                continue
                            if res == "target" or (res is None and optimistic):
                                pos.note_resolution("hourly" if res else assumed_res)
                                apply_partial(pos, day, tp)
                                # The stop was also touched today (daily low is
                                # a fact); target-first means the remainder then
                                # fell to the stop.
                                apply_exit(sym, pos, day, pos.stop, "stop_after_partial")
                                continue
                            pos.note_resolution(assumed_res)
                            apply_exit(sym, pos, day, pos.stop, "stop")
                            continue
                        if hit_stop:
                            apply_exit(sym, pos, day, pos.stop, "stop")
                            continue
                        if hit_tgt:
                            apply_partial(pos, day, tp)
                    if sym in open_positions and trail_hit(pos, sym, day, c):
                        apply_exit(sym, pos, day, c, "trail_ema")
                        continue
                else:  # runner: breakeven stop + close-based trail
                    if l <= pos.stop:
                        apply_exit(sym, pos, day, pos.stop, "stop_breakeven")
                        continue
                    if trail_hit(pos, sym, day, c):
                        apply_exit(sym, pos, day, c, "trail_ema")
                        continue

            if sym in open_positions and pos.bars_held >= config.max_hold_days:
                apply_exit(sym, pos, day, c, "time_stop")

        # 2) Fill pending orders ----------------------------------------------
        for sym in list(pending.keys()):
            order = pending[sym]
            sig = order.signal
            if day <= sig.date:
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
            o, h, l, c = (float(row["Open"]), float(row["High"]),
                          float(row["Low"]), float(row["Close"]))
            limit = sig.trigger_level
            stop = sig.invalidation_level

            if config.entry_model == "next_open":
                # Confirmation entry at the first armed open, adverse slippage.
                entry = _slip_buy(o, config.slippage_bps)
                if entry <= stop:
                    del pending[sym]  # gapped through the stop: setup invalidated
                    continue
                if (entry - stop) / entry > config.max_risk_pct_of_price:
                    del pending[sym]  # opening gap made the risk unacceptable
                    continue
                entry_is_open = True
            else:  # "limit_reclaim"
                if o <= limit:
                    entry = o  # gap below the limit fills at the (better) open
                elif l <= limit:
                    entry = limit  # a limit fills at its price, no buy slippage
                else:
                    continue  # never pulled back to the limit today; keep armed
                if entry <= stop:
                    del pending[sym]
                    continue
                entry_is_open = False

            per_share_risk = entry - stop
            equity = cash + sum(
                p.remaining_shares * float(bar_for(s, day)["Close"])
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

            target = float(sig.target_level) if sig.target_level is not None else (
                entry + config.target_r_multiple * per_share_risk
            )
            trade = Trade(
                symbol=sym,
                entry_date=day,
                entry_price=entry,
                stop=stop,
                stop_initial=stop,
                target=target,
                shares=shares,
                signal_date=sig.date,
                setup_type=sig.setup_type,
                adr_pct=sig.adr_pct,
                rr_planned=sig.rr_planned,
                chase_adr=sig.chase_adr,
            )
            cash -= cost
            open_positions[sym] = trade
            del pending[sym]

            # --- Entry-day events (see module docstring for the exactness
            # case analysis) --------------------------------------------------
            hit_stop = l <= stop
            uses_target = config.exit_model in ("swing_target", "hybrid")
            hit_tgt = uses_target and h >= target

            if config.exit_model == "trail_ema8":
                if hit_stop:
                    # stop < limit entry: touching the stop proves the fill and
                    # the stop-out. EXACT, not an assumption.
                    apply_exit(sym, trade, day, min(stop, o), "stop_same_day")
                continue

            def finish_target_same_day() -> None:
                if config.exit_model == "swing_target":
                    apply_exit(sym, trade, day, target, "target_same_day")
                else:
                    apply_partial(trade, day, target)
                    if hit_stop:
                        apply_exit(sym, trade, day, trade.stop, "stop_after_partial")

            if hit_stop and hit_tgt:
                res = resolver.entry_day(sym, day, limit, stop, target)
                if res == "fill_stopped":
                    trade.note_resolution("hourly")
                    apply_exit(sym, trade, day, min(stop, o), "stop_same_day")
                elif res == "fill_target":
                    trade.note_resolution("hourly")
                    finish_target_same_day()
                else:
                    trade.note_resolution(assumed_res)
                    if optimistic:
                        finish_target_same_day()
                    else:
                        apply_exit(sym, trade, day, min(stop, o), "stop_same_day")
            elif hit_stop:
                apply_exit(sym, trade, day, min(stop, o), "stop_same_day")  # exact
            elif hit_tgt:
                if entry_is_open:
                    # Entered at the open, so the whole bar is post-entry and a
                    # target touch is a real target fill. EXACT.
                    finish_target_same_day()
                else:
                    # Limit fill: did the target print before or after the
                    # pullback that filled us?
                    res = resolver.entry_day(sym, day, limit, stop, target)
                    if res == "fill_target":
                        trade.note_resolution("hourly")
                        finish_target_same_day()
                    elif res == "fill_held":
                        trade.note_resolution("hourly")
                    else:
                        trade.note_resolution(assumed_res)
                        if optimistic:
                            finish_target_same_day()
                        # pessimistic: hold with no target credit

        # 3) Arm new orders from signals that closed today -------------------
        for sym, sigs in signals_by_symbol.items():
            sig = sigs.get(day)
            if sig is None:
                continue
            if config.one_position_per_symbol and sym in open_positions:
                continue

            if config.entry_model == "signal_close":
                # Enter at the UnR bar's own close (live: late-session scan +
                # MOC order). The day is over at fill time, so there are no
                # same-day exit events — a close entry is exact on daily bars.
                if len(open_positions) >= config.max_concurrent_positions:
                    continue
                entry = _slip_buy(float(sig.close), config.slippage_bps)
                stop = sig.invalidation_level
                per_share_risk = entry - stop
                if per_share_risk <= 0:
                    continue
                if per_share_risk / entry > config.max_risk_pct_of_price:
                    continue
                equity = cash + sum(
                    p.remaining_shares * float(bar_for(s, day)["Close"])
                    for s, p in open_positions.items()
                    if bar_for(s, day) is not None
                )
                shares = int(math.floor(equity * config.risk_pct_per_trade / per_share_risk))
                if shares <= 0:
                    continue
                cost = shares * entry + config.commission_per_share * shares
                if cost > cash:
                    shares = int(math.floor((cash * 0.99) / entry))
                    if shares <= 0:
                        continue
                    cost = shares * entry + config.commission_per_share * shares
                target = float(sig.target_level) if sig.target_level is not None else (
                    entry + config.target_r_multiple * per_share_risk
                )
                open_positions[sym] = Trade(
                    symbol=sym,
                    entry_date=day,
                    entry_price=entry,
                    stop=stop,
                    stop_initial=stop,
                    target=target,
                    shares=shares,
                    signal_date=sig.date,
                    setup_type=sig.setup_type,
                    adr_pct=sig.adr_pct,
                    rr_planned=sig.rr_planned,
                    chase_adr=sig.chase_adr,
                )
                cash -= cost
                continue

            armed_until = _add_trading_days(all_dates, day, config.entry_valid_days)
            pending[sym] = PendingOrder(signal=sig, armed_until=armed_until)

        # 4) Mark equity to market -------------------------------------------
        mv = 0.0
        for sym, pos in open_positions.items():
            row = bar_for(sym, day)
            px = float(row["Close"]) if row is not None else pos.entry_price
            mv += pos.remaining_shares * px
        equity_points.append(cash + mv)
        equity_index.append(day)

    equity_curve = pd.Series(equity_points, index=pd.DatetimeIndex(equity_index))
    return BacktestResult(closed, equity_curve, config, signal_count, resolver.stats)


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

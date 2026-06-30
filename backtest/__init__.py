"""Backtesting engine for the trading_system blueprint strategy.

This package reuses the live scanner's deterministic scoring modules
(technical, today_focus, focus_structure) to evaluate setups point-in-time
and simulate the breakout entry / invalidation stop model on historical data.

It deliberately does NOT call Claude: the AI layer cannot be reproduced
historically (cost + look-ahead from model knowledge), so the backtest measures
the edge of the deterministic gates only. Treat Claude as a live-only final
filter on top of whatever edge this proves out.
"""

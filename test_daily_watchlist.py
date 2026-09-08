"""Offline tests for the daily watchlist rules.

Builds synthetic price series with known shapes so every rule can be checked
without touching the network. Run:

    python -m unittest test_daily_watchlist -v
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import daily_watchlist as dw
from backtest.config import BacktestConfig


CONFIG = BacktestConfig(adr_lookback=dw.ADR_LOOKBACK, min_adr_pct=dw.MIN_ADR_PCT)


def make_frame(
    closes: list[float],
    range_pct: float = 0.08,
    volume: float = 5_000_000,
    lows: dict[int, float] | None = None,
    highs: dict[int, float] | None = None,
) -> pd.DataFrame:
    """Build an OHLCV frame from a close series with a fixed daily range."""
    index = pd.bdate_range("2024-01-01", periods=len(closes))
    rows = []
    for i, close in enumerate(closes):
        half = close * range_pct / 2.0
        low = close - half
        high = close + half
        if lows and i in lows:
            low = lows[i]
        if highs and i in highs:
            high = highs[i]
        rows.append(
            {
                "Open": close - half / 4,
                "High": max(high, close),
                "Low": min(low, close),
                "Close": close,
                "Volume": volume,
            }
        )
    return pd.DataFrame(rows, index=index)


def uptrend(n: int = 160, start: float = 50.0, slope: float = 0.35) -> list[float]:
    """A steady, noiseless uptrend."""
    return [start + slope * i for i in range(n)]


def wavy_uptrend(
    n: int = 160, start: float = 50.0, slope: float = 0.35,
    amp: float = 3.0, period: int = 20,
) -> list[float]:
    """An uptrend that pulls back periodically.

    A straight line has no local maxima, so it has no swing highs and therefore
    no target level. Real charts oscillate; this generates the pivots the
    target-finder needs.
    """
    return [
        start + slope * i + amp * np.sin(2 * np.pi * i / period) for i in range(n)
    ]


class UniverseGates(unittest.TestCase):
    def test_rejects_cheap_stock(self):
        closes = [c / 30 for c in uptrend()]  # same shape, ~$1.70 price
        row = dw.evaluate("CHEAP", make_frame(closes), CONFIG)
        self.assertIsNone(row)

    def test_rejects_illiquid_stock(self):
        row = dw.evaluate("THIN", make_frame(uptrend(), volume=50_000), CONFIG)
        self.assertIsNone(row)

    def test_rejects_low_dollar_volume(self):
        # Passes the share-volume floor but not the dollar-volume floor.
        frame = make_frame(uptrend(start=6.0, slope=0.02), volume=1_200_000)
        self.assertIsNone(dw.evaluate("SMALL", frame, CONFIG))

    def test_rejects_low_adr(self):
        # Liquid and trending, but a 1% daily range: too quiet for a tight stop.
        row = dw.evaluate("QUIET", make_frame(uptrend(), range_pct=0.01), CONFIG)
        self.assertIsNone(row)

    def test_rejects_downtrend(self):
        closes = [100.0 - 0.4 * i for i in range(160)]
        self.assertIsNone(dw.evaluate("DOWN", make_frame(closes), CONFIG))

    def test_rejects_short_history(self):
        self.assertIsNone(dw.evaluate("NEW", make_frame(uptrend(n=40)), CONFIG))


class Buckets(unittest.TestCase):
    def test_extended_name_is_dropped(self):
        """A vertical spike far above the 8 EMA is a chase, not a watchlist name."""
        closes = uptrend(n=155) + [104.0, 118.0, 133.0, 150.0, 170.0]
        row = dw.evaluate("SPIKE", make_frame(closes), CONFIG)
        if row is not None:
            self.fail(f"extended name should be dropped, got {row.bucket}")

    def test_forming_near_ema8(self):
        """Trending and resting on the 8 EMA, no reclaim yet -> FORMING."""
        closes = uptrend(n=160)
        row = dw.evaluate("FORM", make_frame(closes), CONFIG)
        self.assertIsNotNone(row)
        self.assertEqual(row.bucket, "FORMING")
        self.assertEqual(row.setup, "ema8_pullback")
        self.assertIsNone(row.entry)
        self.assertLessEqual(abs(row.dist_ema8_adr), dw.FORMING_MAX_ADR_ABOVE_EMA8)

    def test_triggered_undercut_reclaim(self):
        """Final bar dips under the prior low then closes back above it."""
        closes = wavy_uptrend(n=158)
        closes.append(closes[-1] - 2.0)   # a pullback bar; its low becomes the PDL
        closes.append(closes[-1] + 2.5)   # the reclaim bar
        last = len(closes) - 1
        prior_low = closes[last - 1] - closes[last - 1] * 0.04
        # The reclaim bar undercuts the prior day's low but closes strong.
        frame = make_frame(closes, lows={last: prior_low - 1.5})
        row = dw.evaluate("SNIPE", frame, CONFIG)
        self.assertIsNotNone(row)
        self.assertEqual(row.bucket, "TRIGGERED")
        self.assertTrue(row.setup.startswith("unr_"))
        self.assertIsNotNone(row.entry)
        self.assertIsNotNone(row.stop)
        self.assertIsNotNone(row.target)
        self.assertLess(row.stop, row.entry, "stop must sit below the entry")
        self.assertGreater(row.target, row.entry, "target must sit above the entry")
        self.assertGreaterEqual(row.rr, dw.MIN_RR)
        self.assertLessEqual(row.risk_pct, dw.MAX_RISK_PCT_OF_PRICE)


class Determinism(unittest.TestCase):
    def test_same_input_same_output(self):
        frame = make_frame(uptrend())
        first = dw.evaluate("SAME", frame, CONFIG)
        second = dw.evaluate("SAME", frame, CONFIG)
        self.assertEqual(first, second)

    def test_future_bars_do_not_change_todays_call(self):
        """No look-ahead: appending later bars must not alter an earlier call."""
        closes = uptrend(n=170)
        full = make_frame(closes)
        truncated = full.iloc[:160]
        self.assertEqual(
            dw.evaluate("PIT", truncated, CONFIG),
            dw.evaluate("PIT", full.iloc[:160], CONFIG),
        )


class Rendering(unittest.TestCase):
    def _rows(self) -> list[dw.WatchRow]:
        return [
            dw.WatchRow(
                symbol="AAA", bucket="TRIGGERED", setup="unr_pdl", close=100.0,
                adr_pct=7.0, dist_ema8_adr=0.1, perf_1w=3.0, perf_1m=12.0,
                perf_3m=40.0, avg_dollar_volume=5e8, entry=99.0, stop=95.0,
                target=110.0, risk_pct=4.0, rr=2.8, rank_score=2.8,
            ),
            dw.WatchRow(
                symbol="BBB", bucket="FORMING", setup="ema8_pullback", close=50.0,
                adr_pct=6.0, dist_ema8_adr=0.3, perf_1w=1.0, perf_1m=8.0,
                perf_3m=25.0, avg_dollar_volume=1e8, entry=None, stop=None,
                target=None, risk_pct=None, rr=None, rank_score=0.78,
            ),
        ]

    def test_markdown_has_both_sections_and_names(self):
        stats = {
            "run_date": "2026-09-08", "as_of": "2026-09-05", "universe_count": 5000,
            "with_data": 4800, "candidates": 2, "triggered_count": 1,
            "forming_count": 1, "runtime": "4m 10s",
        }
        md = dw.render_markdown(self._rows(), stats)
        self.assertIn("## Triggered", md)
        self.assertIn("## Forming", md)
        self.assertIn("AAA", md)
        self.assertIn("BBB", md)
        self.assertIn("The rules", md)
        self.assertNotIn("None today", md)

    def test_empty_day_still_renders(self):
        """A day with zero setups must still produce a valid report, not crash."""
        stats = {
            "run_date": "2026-09-08", "as_of": "2026-09-05", "universe_count": 5000,
            "with_data": 4800, "candidates": 0, "triggered_count": 0,
            "forming_count": 0, "runtime": "4m 02s",
        }
        md = dw.render_markdown([], stats)
        self.assertIn("_None today._", md)
        self.assertIn("Daily Watchlist", md)

    def test_save_outputs_writes_both_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = dw.save_outputs(self._rows(), "# hi", tmp)
            self.assertTrue(paths["report"].endswith(".md"))
            frame = pd.read_csv(paths["csv"])
            self.assertEqual(list(frame["symbol"]), ["AAA", "BBB"])

    def test_save_outputs_on_empty_watchlist(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = dw.save_outputs([], "# hi", tmp)
            frame = pd.read_csv(paths["csv"])
            self.assertEqual(len(frame), 0)
            self.assertIn("symbol", frame.columns)


class BatchSplitting(unittest.TestCase):
    def test_splits_multiindex_download(self):
        index = pd.bdate_range("2024-01-01", periods=5)
        columns = pd.MultiIndex.from_product(
            [["AAA", "BBB"], ["Open", "High", "Low", "Close", "Volume"]]
        )
        raw = pd.DataFrame(
            np.random.default_rng(0).uniform(10, 20, (5, 10)), index=index, columns=columns
        )
        frames = dw._split_batch(raw, ["AAA", "BBB", "MISSING"])
        self.assertEqual(sorted(frames), ["AAA", "BBB"])
        self.assertEqual(list(frames["AAA"].columns), dw.OHLCV)

    def test_handles_empty_download(self):
        self.assertEqual(dw._split_batch(pd.DataFrame(), ["AAA"]), {})


if __name__ == "__main__":
    unittest.main()


class Funnel(unittest.TestCase):
    """The funnel is the tuning dial; it has to count honestly."""

    def test_records_each_drop_stage(self):
        funnel: dict[str, int] = {}
        cases = {
            "fail_history": make_frame(uptrend(n=40)),
            "fail_price": make_frame([c / 30 for c in uptrend()]),
            "fail_volume": make_frame(uptrend(), volume=50_000),
            "fail_adr": make_frame(uptrend(), range_pct=0.01),
            "fail_trend": make_frame([100.0 - 0.4 * i for i in range(160)]),
        }
        for expected, frame in cases.items():
            single: dict[str, int] = {}
            dw.evaluate("X", frame, CONFIG, funnel=single)
            self.assertEqual(single.get(expected), 1, f"{expected} not recorded: {single}")
            dw.evaluate("X", frame, CONFIG, funnel=funnel)
        self.assertEqual(funnel["evaluated"], len(cases))

    def test_counts_reconcile(self):
        """Terminal stages partition the evaluated set.

        reclaim_* keys are deliberately excluded: they are near-miss markers
        that overlap a terminal stage (a reclaim with no target can still be
        FORMING), so they are not part of the partition.
        """
        funnel: dict[str, int] = {}
        frames = [make_frame(uptrend()) for _ in range(4)]
        frames.append(make_frame(uptrend(), volume=50_000))
        for frame in frames:
            dw.evaluate("X", frame, CONFIG, funnel=funnel)
        terminal = sum(
            v for k, v in funnel.items()
            if k != "evaluated" and not k.startswith("reclaim_")
        )
        self.assertEqual(
            terminal, funnel["evaluated"],
            f"terminal stages must partition the evaluated set: {funnel}",
        )

    def test_funnel_is_optional(self):
        self.assertIsNotNone(dw.evaluate("OK", make_frame(uptrend()), CONFIG))

    def test_markdown_renders_funnel(self):
        stats = {
            "run_date": "2026-09-08", "as_of": "2026-09-05", "universe_count": 10,
            "with_data": 10, "candidates": 0, "triggered_count": 0,
            "forming_count": 0, "runtime": "1m",
            "funnel": {"evaluated": 10, "fail_adr": 7, "fail_trend": 3},
        }
        md = dw.render_markdown([], stats)
        self.assertIn("Where names dropped out", md)
        self.assertIn("ADR% too low", md)
        self.assertIn("evaluated: 10", md)

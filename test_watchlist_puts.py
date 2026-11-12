"""The put side is the call side on a mirrored chart.

Run from the repo root:  python -m unittest test_watchlist_puts
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import daily_watchlist as dw
import watchlist_charts as wc
import watchlist_email as we
import watchlist_ranking as wr
from backtest.adr import adr_pct
from test_daily_watchlist import CONFIG, make_frame, wavy_uptrend


def _call_reclaim_frame() -> pd.DataFrame:
    """The TRIGGERED call fixture from test_daily_watchlist."""
    closes = wavy_uptrend(n=158)
    closes.append(closes[-1] - 2.0)
    closes.append(closes[-1] + 2.5)
    last = len(closes) - 1
    prior_low = closes[last - 1] - closes[last - 1] * 0.04
    return make_frame(closes, lows={last: prior_low - 1.5})


def _closes(path) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=len(path))
    return pd.DataFrame({"Close": np.asarray(path, float), "Volume": 2e6}, index=idx)


class MirrorTest(unittest.TestCase):
    def test_mirror_keeps_last_close_and_adr(self):
        frame = _call_reclaim_frame()
        mirrored = dw.mirror_frame(frame)
        self.assertAlmostEqual(mirrored["Close"].iloc[-1], frame["Close"].iloc[-1])
        self.assertAlmostEqual(adr_pct(mirrored, 20), adr_pct(frame, 20))
        self.assertTrue((mirrored["High"] >= mirrored["Low"]).all())
        pd.testing.assert_frame_equal(dw.mirror_frame(mirrored), frame[mirrored.columns],
                                      check_exact=False)

    def test_mirrored_call_setup_is_a_put_with_mirrored_levels(self):
        call_frame = _call_reclaim_frame()
        call = dw.evaluate("X", call_frame, CONFIG)
        put = dw.evaluate_put("X", dw.mirror_frame(call_frame), CONFIG)
        self.assertEqual(call.bucket, "TRIGGERED")
        self.assertEqual(put.bucket, "TRIGGERED")
        self.assertEqual(put.side, "put")
        self.assertTrue(put.setup.startswith("onr_"))
        self.assertGreater(put.stop, put.entry)
        self.assertLess(put.target, put.entry)
        k = float(call_frame["Close"].iloc[-1]) ** 2
        self.assertAlmostEqual(put.entry, k / call.entry, delta=0.05)
        self.assertAlmostEqual(put.stop, k / call.stop, delta=0.05)
        self.assertAlmostEqual(put.target, k / call.target, delta=0.05)
        self.assertGreaterEqual(put.rr, dw.MIN_RR)

    def test_an_uptrend_is_never_a_put(self):
        self.assertIsNone(dw.evaluate_put("UP", _call_reclaim_frame(), CONFIG))


class SideTest(unittest.TestCase):
    def test_trade_side_follows_the_50_ema(self):
        up = _closes(np.linspace(100, 150, 120))
        down = _closes(np.linspace(150, 100, 120))
        self.assertEqual(wr.trade_side({"SPY": up, "QQQ": up}), "call")
        self.assertEqual(wr.trade_side({"SPY": down, "QQQ": down}), "put")
        self.assertEqual(wr.trade_side({"SPY": up, "QQQ": down}), "both")
        self.assertEqual(wr.market_regime({"SPY": down, "QQQ": down}, "put").level, 3)

    def test_put_side_picks_the_weakest_sectors_and_keeps_real_returns(self):
        n = 120
        frames = {etf: _closes(np.linspace(100, 100 * (1 + g), n))
                  for etf, g in zip(wr.SECTOR_ETFS.values(),
                                    [0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3, -0.4, -0.5])}
        calls = wr.sector_leadership(frames, "call")
        puts = wr.sector_leadership(frames, "put")
        names = list(wr.SECTOR_ETFS)
        self.assertEqual(list(calls.index[calls["leading"]]), names[:3])
        self.assertEqual(list(puts.index[puts["leading"]]), names[::-1][:3])
        self.assertLess(puts.loc[names[-1], "3M"], 0)   # shown as the real (negative) return

    def test_rs_for_puts_is_weakness(self):
        returns = pd.DataFrame({"1W": [1, 2, 3], "1M": [5, 10, 20], "3M": [10, 20, 40]},
                               index=["LOW", "MID", "HIGH"], dtype=float)
        rw = wr.rs_ratings(returns, "put")
        self.assertGreater(rw["LOW"], rw["HIGH"])


class PutPresentationTest(unittest.TestCase):
    def _put_row(self):
        frame = dw.mirror_frame(_call_reclaim_frame())
        return dw.evaluate_put("PUTX", frame, CONFIG), frame

    def test_chart_draws_a_put_plan(self):
        row, frame = self._put_row()
        with tempfile.TemporaryDirectory() as tmp:
            path = wc.render_chart("PUTX", frame, row, Path(tmp) / "p.png")
            self.assertGreater(path.stat().st_size, 10_000)

    def test_email_shows_the_put_section(self):
        row, _ = self._put_row()
        row.group, row.rs = "Energy", 95
        sectors = wr.overlap_leaders(pd.DataFrame(
            {"1W": [-3.0, 1.0], "1M": [-6.0, 2.0], "3M": [-9.0, 3.0]},
            index=["Energy", "Technology"]) * -1, 3)
        sectors[list(wr.WINDOWS)] *= -1
        lead = wr.Leadership(sectors=sectors, themes=pd.DataFrame(), regime=None,
                             rs=pd.Series({"PUTX": 95}), side="put")
        stats = {"as_of": "2026-11-11", "funnel": {}, "leadership": lead, "side": "put",
                 "sides": [("put", lead)]}
        html = we.render_html([row], stats, {"PUTX"}, dw.rules_lines())
        self.assertIn("Today: PUTS.", html)
        self.assertIn("PUTS: lagging sectors today", html)
        self.assertIn("Bottom 5 in", html)
        self.assertIn("RW 95", html)
        self.assertIn("STOP +", html)
        self.assertEqual(we.emailed_chart_symbols([row], stats), ["PUTX"])

    def test_both_sides_split_the_chart_budget(self):
        call = dw.evaluate("CALLX", _call_reclaim_frame(), CONFIG)
        put, _ = self._put_row()
        call.group, put.group = "Technology", "Energy"
        table = pd.DataFrame({"1W": [1.0], "1M": [1.0], "3M": [1.0], "top5_count": [3],
                              "rank": [1], "leading": [True]})
        lead_c = wr.Leadership(table.set_axis(["Technology"]), pd.DataFrame(), None,
                               pd.Series(dtype=float))
        lead_p = wr.Leadership(table.set_axis(["Energy"]), pd.DataFrame(), None,
                               pd.Series(dtype=float), side="put")
        stats = {"as_of": "2026-11-11", "funnel": {}, "leadership": lead_c, "side": "both",
                 "sides": [("call", lead_c), ("put", lead_p)]}
        html = we.render_html([call, put], stats, set(), dw.rules_lines())
        self.assertIn("CALLS: leading sectors today", html)
        self.assertIn("PUTS: lagging sectors today", html)
        self.assertEqual(we.emailed_chart_symbols([call, put], stats), ["CALLX", "PUTX"])


if __name__ == "__main__":
    unittest.main()

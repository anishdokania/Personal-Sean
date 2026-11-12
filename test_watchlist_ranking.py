"""Tests for leading groups, relative strength and the ranked order.

Run from the repo root:  python -m unittest test_watchlist_ranking
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import daily_watchlist as dw
import watchlist_email as we
import watchlist_ranking as wr


def _row(symbol, bucket="TRIGGERED", rr=3.0, risk_pct=3.0, adr_pct=6.0, dist=0.2):
    triggered = bucket == "TRIGGERED"
    return dw.WatchRow(
        symbol=symbol, bucket=bucket, setup="unr_pdl" if triggered else "ema8_pullback",
        close=50.0, adr_pct=adr_pct, dist_ema8_adr=dist, perf_1w=1.0, perf_1m=5.0,
        perf_3m=10.0, avg_dollar_volume=1e8,
        entry=49.0 if triggered else None, stop=47.5 if triggered else None,
        target=55.0 if triggered else None,
        risk_pct=risk_pct if triggered else None, rr=rr if triggered else None,
        rank_score=rr if triggered else 0.5,
    )


def _closes(path: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=len(path))
    return pd.DataFrame({"Close": path, "Volume": np.full(len(path), 2e6)}, index=idx)


class OverlapTest(unittest.TestCase):
    def test_top5_overlap_caps_and_tie_breaks(self):
        # Seven groups; A-E are top 5 in all three windows, F/G never.
        table = pd.DataFrame(
            {"1W": [9, 8, 7, 6, 5, 1, 0], "1M": [5, 9, 8, 7, 6, 1, 0],
             "3M": [9, 8, 7, 6, 5, 1, 0]},
            index=list("ABCDEFG"), dtype=float,
        )
        out = wr.overlap_leaders(table, max_leaders=3)
        self.assertEqual(list(out.index[:3]), ["B", "C", "D"])  # 3/3 each; 1M breaks the tie
        self.assertEqual(list(out.index[out["leading"]]), ["B", "C", "D"])
        self.assertEqual(int(out.loc["F", "top5_count"]), 0)

    def test_a_group_in_only_one_window_never_leads(self):
        table = pd.DataFrame({"1W": [9, 1, 1], "1M": [1, 9, 1], "3M": [1, 1, 9]},
                             index=list("ABC"), dtype=float)
        with mock.patch.object(wr, "TOP_N", 1):
            out = wr.overlap_leaders(table, max_leaders=3)
        self.assertFalse(out["leading"].any())


class RegimeTest(unittest.TestCase):
    def test_weaker_index_sets_the_regime(self):
        up = _closes(np.linspace(100, 150, 120))
        down = _closes(np.linspace(150, 100, 120))
        regime = wr.market_regime({"SPY": up, "QQQ": down})
        self.assertEqual(regime.level, 0)
        self.assertEqual(wr.market_regime({"SPY": up, "QQQ": up}).level, 3)


class ThemesOffTest(unittest.TestCase):
    def test_compute_leadership_returns_no_themes(self):
        path = np.linspace(50, 80, 120)
        frames = {f"S{i}": _closes(path * (1 + i / 50)) for i in range(8)}
        imap = {s: ("Technology", "Solar") for s in frames}
        ref = {"XLK": _closes(path), "SPY": _closes(path), "QQQ": _closes(path)}
        lead = wr.compute_leadership(frames, ref, imap)
        self.assertEqual(lead.leading_themes, [])


class RsTest(unittest.TestCase):
    def test_rs_orders_by_recent_weighted_return(self):
        returns = pd.DataFrame(
            {"1W": [1, 2, 3], "1M": [5, 10, 20], "3M": [10, 20, 40]},
            index=["LOW", "MID", "HIGH"], dtype=float,
        )
        rs = wr.rs_ratings(returns)
        self.assertLess(rs["LOW"], rs["MID"])
        self.assertLess(rs["MID"], rs["HIGH"])
        self.assertTrue(rs.between(1, 99).all())


class SetupQualityTest(unittest.TestCase):
    def test_tiny_stop_cannot_win_on_rr(self):
        tiny = _row("T", rr=58.0, risk_pct=0.3, adr_pct=6.8)    # 0.04 ADR stop
        sane = _row("S", rr=3.0, risk_pct=3.0, adr_pct=6.0)     # 0.5 ADR stop
        # 58R from a 0.04-ADR stop scores below 3R from a 0.5-ADR stop.
        self.assertGreater(wr.setup_quality(sane), wr.setup_quality(tiny))


class RankRowsTest(unittest.TestCase):
    def _leadership(self):
        sectors = wr.overlap_leaders(pd.DataFrame(
            {"1W": [3, 2, 1, 0, -1, -2], "1M": [3, 2, 1, 0, -1, -2],
             "3M": [3, 2, 1, 0, -1, -2]},
            index=["Healthcare", "Energy", "Utilities", "Technology", "Industrials",
                   "Real Estate"], dtype=float), wr.LEADING_SECTORS_MAX)
        themes = pd.DataFrame({"1W": [5.0], "1M": [9.0], "3M": [20.0], "members": [8],
                               "top5_count": [3], "rank": [1], "leading": [True]},
                              index=["Solar"])
        rs = pd.Series({"HC1": 90, "HC2": 60, "EN1": 95, "SOL": 80, "TEC": 99})
        return wr.Leadership(sectors=sectors, themes=themes, regime=None, rs=rs)

    def test_order_sectors_then_themes_then_outside(self):
        imap = {"HC1": ("Healthcare", "Biotechnology"), "HC2": ("Healthcare", "Biotechnology"),
                "EN1": ("Energy", "Oil & Gas"), "SOL": ("Technology", "Solar"),
                "TEC": ("Technology", "Software")}
        rows = [_row("TEC"), _row("SOL"), _row("HC2"), _row("EN1"),
                _row("HC1", bucket="FORMING")]
        ranked = wr.rank_rows(rows, self._leadership(), imap)
        self.assertEqual([r.symbol for r in ranked], ["HC2", "HC1", "EN1", "SOL", "TEC"])
        self.assertEqual(ranked[3].group, "Theme: Solar")  # theme rescues it from Technology
        self.assertEqual(ranked[4].group, "")              # RS 99 but lagging group: last
        self.assertEqual(ranked[0].rs, 60)

    def test_grouped_email_renders_groups_and_outside_table(self):
        imap = {"HC1": ("Healthcare", "Biotechnology"), "TEC": ("Technology", "Software")}
        leadership = self._leadership()
        ranked = wr.rank_rows([_row("HC1"), _row("TEC")], leadership, imap)
        stats = {"as_of": "2026-11-11", "funnel": {}, "leadership": leadership}
        html = we.render_html(ranked, stats, {"HC1"}, dw.rules_lines())
        self.assertIn("CALLS: leading sectors today", html)
        self.assertIn("CALLS: outside the leading sectors (1)", html)
        self.assertIn("cid:chart-hc1", html)
        self.assertEqual(we.emailed_chart_symbols(ranked, stats), ["HC1"])


class GroupCapTest(unittest.TestCase):
    def test_each_group_shows_at_most_eight_names(self):
        rows = [_row(f"H{i}") for i in range(12)] + [_row("E1")]
        for r in rows:
            r.group, r.rs = ("Energy" if r.symbol == "E1" else "Healthcare"), 80
        layout, outside = we._grouped(rows)
        healthcare = next(g for g in layout if g[0] == "Healthcare")
        self.assertEqual(len(healthcare[1]) + len(healthcare[2]), we.MAX_NAMES_PER_GROUP)
        self.assertEqual(healthcare[3], 4)
        self.assertEqual([r.symbol for r in healthcare[1]], ["H0", "H1", "H2", "H3"])
        self.assertEqual(outside, [])
        stats = {"as_of": "2026-11-11", "funnel": {}, "leadership": RankRowsTest()._leadership()}
        html = we.render_html(rows, stats, set(), dw.rules_lines())
        self.assertIn("+4 more Healthcare names", html)
        self.assertNotIn("<b>H11</b>", html)


class IndustryCacheTest(unittest.TestCase):
    def test_only_uncached_symbols_are_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "map.csv"
            cache.write_text("symbol,sector,industry\nAAA,Technology,Solar\n")
            with mock.patch.object(wr, "_lookup", return_value=("Energy", "Oil")) as lookup, \
                    mock.patch.object(wr, "FETCH_PAUSE_SECONDS", 0):
                mapping = wr.load_industry_map(["AAA", "BBB"], cache_path=cache)
            lookup.assert_called_once_with("BBB")
            self.assertEqual(mapping["AAA"], ("Technology", "Solar"))
            self.assertIn("BBB,Energy,Oil", cache.read_text())

    def test_failures_are_not_cached_and_fall_back_to_nasdaq_sector(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "map.csv"
            with mock.patch.object(wr, "_lookup", side_effect=[None, wr.RateLimited("429")]), \
                    mock.patch.object(wr, "nasdaq_sectors", return_value={"BBB": "Energy"}), \
                    mock.patch.object(wr, "FETCH_PAUSE_SECONDS", 0):
                mapping = wr.load_industry_map(["AAA", "BBB", "CCC"], cache_path=cache)
            self.assertFalse(cache.exists())        # nothing real came back: nothing cached
            self.assertEqual(mapping, {"BBB": ("Energy", "")})


if __name__ == "__main__":
    unittest.main()

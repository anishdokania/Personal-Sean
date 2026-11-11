"""Tests for the watchlist charts and the HTML email.

Run from the repo root:  python -m unittest test_watchlist_email
"""

from __future__ import annotations

import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import daily_watchlist as dw
import email_report
import watchlist_charts as wc
import watchlist_email as we


def _frame(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(rng.normal(0.002, 0.03, n)))
    high = close * (1 + rng.uniform(0.005, 0.04, n))
    low = close * (1 - rng.uniform(0.005, 0.04, n))
    open_ = np.clip(close * (1 + rng.normal(0, 0.01, n)), low, high)
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float)},
        index=idx,
    )


def _row(symbol: str, bucket: str, close: float) -> dw.WatchRow:
    triggered = bucket == "TRIGGERED"
    return dw.WatchRow(
        symbol=symbol, bucket=bucket, setup="unr_pdl" if triggered else "ema8_pullback",
        close=close, adr_pct=6.0, dist_ema8_adr=0.2, perf_1w=2.0, perf_1m=10.0,
        perf_3m=30.0, avg_dollar_volume=1e8,
        entry=close * 0.98 if triggered else None,
        stop=close * 0.95 if triggered else None,
        target=close * 1.10 if triggered else None,
        risk_pct=3.1 if triggered else None, rr=3.9 if triggered else None,
        rank_score=1.0,
    )


STATS = {"as_of": "2026-11-11", "funnel": {"evaluated": 10, "triggered": 1, "forming": 1},
         "universe_count": 10, "with_data": 10, "runtime": "0m 5s"}


class ChartTest(unittest.TestCase):
    def test_renders_png_for_both_buckets_and_a_pdf(self):
        frames = {"AAA": _frame(seed=1), "BBB": _frame(seed=2)}
        rows = [
            _row("AAA", "TRIGGERED", float(frames["AAA"]["Close"].iloc[-1])),
            _row("BBB", "FORMING", float(frames["BBB"]["Close"].iloc[-1])),
            _row("NODATA", "FORMING", 10.0),  # no frame: skipped, not a crash
        ]
        with tempfile.TemporaryDirectory() as tmp:
            paths = wc.render_charts(rows, frames, tmp, "t")
            self.assertEqual(set(paths), {"AAA", "BBB"})
            for p in paths.values():
                self.assertTrue(p.read_bytes().startswith(b"\x89PNG"))
            pdf = wc.charts_pdf(rows, paths, Path(tmp) / "c.pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertIsNone(wc.charts_pdf([], {}, Path(tmp) / "none.pdf"))


class HtmlTest(unittest.TestCase):
    def test_top_names_get_inline_charts_and_the_rest_a_table(self):
        rows = [_row(f"T{i}", "TRIGGERED", 20.0 + i) for i in range(14)]
        rows += [_row(f"F{i}", "FORMING", 30.0 + i) for i in range(9)]
        inline = we.emailed_chart_symbols(rows)
        self.assertEqual(len(inline), we.TOP_TRIGGERED_WITH_CHARTS + we.TOP_FORMING_WITH_CHARTS)
        html = we.render_html(rows, STATS, set(inline), dw.rules_lines())
        for sym in inline:
            self.assertIn(f"cid:{we.chart_cid(sym)}", html)
        self.assertNotIn("cid:chart-t13", html)  # past the top 10: table only
        self.assertIn("T13", html)
        self.assertIn("Everything else (7)", html)

    def test_large_list_stays_under_gmail_clipping(self):
        rows = [_row(f"T{i}", "TRIGGERED", 20.0) for i in range(60)]
        rows += [_row(f"F{i}", "FORMING", 30.0) for i in range(90)]
        html = we.render_html(rows, STATS, set(we.emailed_chart_symbols(rows)), dw.rules_lines())
        self.assertLess(len(html.encode("utf-8")), 100_000)

    def test_empty_list(self):
        html = we.render_html([], STATS, set(), dw.rules_lines())
        self.assertIn("Nothing passed the rules today", html)


class SendTest(unittest.TestCase):
    def test_html_with_inline_images_and_attachment(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def starttls(self, **kwargs): pass
            def login(self, *args): pass
            def send_message(self, message): sent["m"] = message

        config = email_report.EmailConfig(
            smtp_host="h", smtp_port=587, smtp_username="u", smtp_password="p",
            sender="a@example.com", recipient="b@example.com",
            use_tls=True, use_ssl=False, timeout_seconds=5,
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "x.png"
            png.write_bytes(b"\x89PNG fake")
            csv = Path(tmp) / "x.csv"
            csv.write_text("a,b\n")
            with mock.patch.object(smtplib, "SMTP", FakeSMTP):
                email_report.send_email(
                    "subj", "plain", attachments=[csv], config=config,
                    html='<img src="cid:chart-x">', inline_images={"chart-x": png},
                )
        types = [p.get_content_type() for p in sent["m"].walk()]
        self.assertEqual(types[:5], ["multipart/mixed", "multipart/alternative", "text/plain",
                                     "multipart/related", "text/html"])
        self.assertIn("image/png", types)
        cids = [p.get("Content-ID") for p in sent["m"].walk() if p.get_content_type() == "image/png"]
        self.assertEqual(cids, ["<chart-x>"])


if __name__ == "__main__":
    unittest.main()

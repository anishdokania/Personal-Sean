"""Build a single scrollable HTML gallery of all rendered trade charts.

Scans backtest/output/trade_charts/ for the PNGs produced by trade_charts.py,
groups them by setup type, tallies win/loss, and writes an index.html you can
open in a browser to flip through every trade.

    venv/bin/python -m backtest.gallery
    open backtest/output/trade_charts/index.html
"""

from __future__ import annotations

import html
import os
from collections import defaultdict

CHART_DIR = os.path.join(os.path.dirname(__file__), "output", "trade_charts")


def _parse(name: str) -> dict:
    # SYMBOL_YYYY-MM-DD_setup_type_OUTCOME.png
    base = name[:-4] if name.endswith(".png") else name
    outcome = "WIN" if base.endswith("_WIN") else ("LOSS" if base.endswith("_LOSS") else "?")
    core = base.rsplit("_", 1)[0]
    parts = core.split("_", 2)
    symbol = parts[0] if parts else "?"
    date = parts[1] if len(parts) > 1 else "?"
    setup = parts[2] if len(parts) > 2 else "unknown"
    return {"file": name, "symbol": symbol, "date": date, "setup": setup, "outcome": outcome}


def main() -> None:
    if not os.path.isdir(CHART_DIR):
        raise SystemExit(f"No chart dir: {CHART_DIR}")
    pngs = sorted(f for f in os.listdir(CHART_DIR) if f.endswith(".png"))
    if not pngs:
        raise SystemExit("No charts found. Run: python -m backtest.trade_charts --limit 0")

    by_setup: dict[str, list[dict]] = defaultdict(list)
    for name in pngs:
        info = _parse(name)
        by_setup[info["setup"]].append(info)

    # Order setups by trade count, biggest first.
    setups = sorted(by_setup, key=lambda s: -len(by_setup[s]))

    parts: list[str] = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Backtest trade charts</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}",
        "header{padding:16px 24px;background:#161a22;position:sticky;top:0;border-bottom:1px solid #2a2f3a;z-index:5}",
        "h1{font-size:18px;margin:0 0 6px}",
        "nav a{color:#6ab0ff;margin-right:14px;text-decoration:none;font-size:13px}",
        "section{padding:8px 24px 32px}",
        "h2{font-size:15px;border-left:4px solid #6ab0ff;padding-left:10px;margin:24px 0 4px}",
        ".sub{color:#9aa4b2;font-size:13px;margin:0 0 12px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:14px}",
        ".card{background:#161a22;border:1px solid #2a2f3a;border-radius:8px;overflow:hidden}",
        ".card img{width:100%;display:block;cursor:zoom-in}",
        ".cap{padding:6px 10px;font-size:12px;display:flex;justify-content:space-between}",
        ".WIN{color:#4ec86b}.LOSS{color:#e0555a}",
        "</style>",
    ]

    total = len(pngs)
    wins = sum(1 for f in pngs if f.endswith("_WIN.png"))
    parts.append("<header>")
    parts.append(f"<h1>Backtest trade charts — {total} trades, {wins} wins "
                 f"({100*wins/total:.1f}%)</h1>")
    parts.append("<nav>" + "".join(
        f"<a href='#{html.escape(s)}'>{html.escape(s)} ({len(by_setup[s])})</a>"
        for s in setups
    ) + "</nav></header>")

    for s in setups:
        items = sorted(by_setup[s], key=lambda d: (d["symbol"], d["date"]))
        w = sum(1 for d in items if d["outcome"] == "WIN")
        parts.append(f"<section id='{html.escape(s)}'>")
        parts.append(f"<h2>{html.escape(s)}</h2>")
        parts.append(f"<p class='sub'>{len(items)} trades — {w} wins "
                     f"({100*w/len(items):.0f}%)</p>")
        parts.append("<div class='grid'>")
        for d in items:
            cap = (f"<span>{html.escape(d['symbol'])} · {html.escape(d['date'])}</span>"
                   f"<span class='{d['outcome']}'>{d['outcome']}</span>")
            parts.append(
                f"<div class='card'><a href='{html.escape(d['file'])}' target='_blank'>"
                f"<img loading='lazy' src='{html.escape(d['file'])}'></a>"
                f"<div class='cap'>{cap}</div></div>"
            )
        parts.append("</div></section>")

    out = os.path.join(CHART_DIR, "index.html")
    with open(out, "w") as fh:
        fh.write("\n".join(parts))
    print(f"Gallery written to {out}")
    print(f"Open it with:  open {out}")


if __name__ == "__main__":
    main()

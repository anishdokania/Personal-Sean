"""
Daily charts for the watchlist, with the trade plan drawn on them.

Plain matplotlib on purpose: this module must not import the LLM chart stack
(chart_generator / mplfinance), so the daily job stays free of it.

Every chart is ~6 months of daily candles with the 8 / 21 / 50 EMAs and a
volume panel. On top of that:

  TRIGGERED  - the undercut-and-reclaim bar is marked, and the plan is drawn
               to the right of the last bar the way a long-position tool shows
               it: entry line, red risk box down to the stop, green reward box
               up to the target, each labelled with price and % from entry.
  FORMING    - no plan exists yet, so the chart shows what to watch instead:
               the 8 EMA and today's low (tomorrow's prior-day low). An
               undercut of either that closes back above it is the trigger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

CHART_BARS = 84           # ~4 months: enough context, recent bars stay readable
PLAN_PAD_BARS = 14        # empty space right of the last bar for the plan boxes
FIG_SIZE = (8.0, 5.0)     # inches; 100 dpi -> 800x500 px, scaled to phone width
DPI = 100

UP = "#26a69a"
DOWN = "#ef5350"
EMA_COLORS = {8: "#2962ff", 21: "#f23645", 50: "#9e9e9e"}
ENTRY_COLOR = "#1e3a8a"
STOP_COLOR = "#d32f2f"
TARGET_COLOR = "#2e7d32"
WATCH_COLOR = "#f57c00"

SETUP_LABELS = {
    "unr_pdl": "Undercut & reclaim of the prior-day low",
    "unr_ema8": "Undercut & reclaim of the 8 EMA",
    "unr_ema21": "Undercut & reclaim of the 21 EMA",
    "ema8_pullback": "Pulling into the 8 EMA",
}


def setup_label(setup: str) -> str:
    return SETUP_LABELS.get(setup, setup)


def _draw_candles(ax, frame: pd.DataFrame) -> None:
    x = np.arange(len(frame))
    o, h, l, c = (frame[col].to_numpy(float) for col in ("Open", "High", "Low", "Close"))
    colors = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, colors=colors, linewidth=0.8, zorder=2)
    body_low = np.minimum(o, c)
    body_height = np.maximum(np.abs(c - o), (h - l).mean() * 0.01)
    ax.bar(x, body_height, bottom=body_low, width=0.65, color=colors, zorder=3, linewidth=0)


def _place_labels(ax, x_line: float, x_text: float, labels, y_lo: float, y_hi: float) -> None:
    """Draw price labels right of the plan, nudged apart so none overlap.

    ``labels`` is (level, text, color). Tight stops put entry and stop within a
    fraction of a percent of each other, so labels are spread to a minimum gap
    and tied back to their true level with a thin leader line.
    """
    gap = 0.075 * (y_hi - y_lo)
    placed: list[float] = []
    for level, _, _ in sorted(labels, key=lambda item: -item[0]):
        y = level if not placed else min(level, placed[-1] - gap)
        placed.append(y)
    for (level, text, color), y in zip(sorted(labels, key=lambda item: -item[0]), placed):
        ax.plot([x_line, x_text - 0.2], [level, y], color=color, linewidth=0.8, zorder=5)
        ax.text(
            x_text, y, text, color="white", fontsize=10.5, fontweight="bold", va="center",
            ha="left", zorder=6,
            bbox=dict(boxstyle="round,pad=0.25", facecolor=color, edgecolor="none"),
        )


def render_chart(symbol: str, df: pd.DataFrame, row, path: str | Path) -> Path:
    """Draw one watchlist chart to ``path`` (PNG) and return the path."""
    path = Path(path)
    closes = df["Close"]
    emas = {span: closes.ewm(span=span, adjust=False).mean() for span in EMA_COLORS}
    frame = df.iloc[-CHART_BARS:]
    n = len(frame)
    x = np.arange(n)
    last = n - 1

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=FIG_SIZE, dpi=DPI, sharex=True,
        gridspec_kw={"height_ratios": [3.4, 1], "hspace": 0.04},
    )
    for a in (ax, axv):
        a.set_facecolor("white")
        a.grid(True, color="#eeeeee", linewidth=0.6, zorder=0)
        a.tick_params(labelsize=9.5)
        for side in ("top", "right"):
            a.spines[side].set_visible(False)

    _draw_candles(ax, frame)
    for span, series in emas.items():
        ax.plot(x, series.iloc[-n:].to_numpy(float), color=EMA_COLORS[span],
                linewidth=1.1, label=f"{span} EMA", zorder=4)

    lows = frame["Low"].to_numpy(float)
    highs = frame["High"].to_numpy(float)
    y_lo, y_hi = float(lows.min()), float(highs.max())
    right = last + PLAN_PAD_BARS
    box_x0, box_w = last + 1.5, PLAN_PAD_BARS - 6

    if row.bucket == "TRIGGERED" and row.entry and row.stop and row.target:
        entry, stop, target = row.entry, row.stop, row.target
        ax.add_patch(Rectangle((box_x0, stop), box_w, entry - stop,
                               facecolor=STOP_COLOR, alpha=0.18, zorder=1))
        ax.add_patch(Rectangle((box_x0, entry), box_w, target - entry,
                               facecolor=TARGET_COLOR, alpha=0.15, zorder=1))
        for level, color, style in ((entry, ENTRY_COLOR, "-"), (stop, STOP_COLOR, "--"),
                                    (target, TARGET_COLOR, "--")):
            ax.hlines(level, last - 8, box_x0 + box_w, colors=color,
                      linestyles=style, linewidth=1.2, zorder=5)
        y_lo, y_hi = min(y_lo, stop), max(y_hi, target)
        _place_labels(ax, box_x0 + box_w, box_x0 + box_w + 1.5, [
            (target, f"Target {target:.2f}  +{100 * (target / entry - 1):.1f}%", TARGET_COLOR),
            (entry, f"Entry {entry:.2f}  (limit)", ENTRY_COLOR),
            (stop, f"Stop {stop:.2f}  -{100 * (1 - stop / entry):.1f}%", STOP_COLOR),
        ], y_lo, y_hi)
        ax.annotate("UnR bar", xy=(last, lows[-1]),
                    xytext=(last - 12, lows[-1] - 0.12 * (y_hi - y_lo)),
                    fontsize=10, fontweight="bold", color=ENTRY_COLOR,
                    arrowprops=dict(arrowstyle="->", color=ENTRY_COLOR, lw=1.2), zorder=6)
    else:
        ema8_now = float(emas[8].iloc[-1])
        watch = ((ema8_now, "8 EMA"), (float(lows[-1]), "Today's low"))
        for level, _ in watch:
            ax.hlines(level, last - 8, box_x0 + box_w, colors=WATCH_COLOR, linestyles="--",
                      linewidth=1.1, zorder=5)
        y_lo, y_hi = min(y_lo, *(w[0] for w in watch)), max(y_hi, *(w[0] for w in watch))
        _place_labels(ax, box_x0 + box_w, box_x0 + box_w + 1.5,
                      [(level, f"{name} {level:.2f}", WATCH_COLOR) for level, name in watch],
                      y_lo, y_hi)
        ax.text(box_x0 + 1.5, y_hi, "Trigger: undercut one of these,\nthen close back above it",
                fontsize=9.5, color=WATCH_COLOR, va="top", ha="left", zorder=6)

    pad = 0.06 * (y_hi - y_lo)
    ax.set_ylim(y_lo - pad, y_hi + pad)
    ax.set_xlim(-1, right + 13)
    ax.legend(loc="upper left", fontsize=9, frameon=False, ncol=3)

    vol = frame["Volume"].to_numpy(float)
    vcolors = np.where(frame["Close"].to_numpy(float) >= frame["Open"].to_numpy(float), UP, DOWN)
    axv.bar(x, vol, color=vcolors, width=0.65, alpha=0.75, zorder=2)
    axv.plot(x, df["Volume"].rolling(20).mean().iloc[-n:].to_numpy(float),
             color="#555555", linewidth=1.0, label="20d avg", zorder=3)
    axv.legend(loc="upper left", fontsize=7, frameon=False)
    axv.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: f"{v / 1e6:.0f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"))

    ticks = list(range(0, n, 21))
    axv.set_xticks(ticks)
    axv.set_xticklabels([frame.index[i].strftime("%b %d") for i in ticks])

    # Short title: the email card above the chart already carries the details,
    # but the PDF pages need to say which name and setup they are.
    ax.set_title(f"{symbol}  ·  {setup_label(row.setup)}", fontsize=12.5, loc="left",
                 fontweight="bold")

    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def render_charts(
    rows: Iterable, frames: dict[str, pd.DataFrame], output_dir: str | Path, stamp: str
) -> dict[str, Path]:
    """Render a PNG for every row that has data. Returns symbol -> path."""
    out_dir = Path(output_dir) / f"charts_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for row in rows:
        frame = frames.get(row.symbol)
        if frame is None or frame.empty:
            continue
        try:
            paths[row.symbol] = render_chart(row.symbol, frame, row, out_dir / f"{row.symbol}.png")
        except Exception as exc:  # one bad chart must not kill the email
            print(f"  ! chart for {row.symbol} skipped: {exc}", flush=True)
    return paths


def charts_pdf(rows: Iterable, chart_paths: dict[str, Path], pdf_path: str | Path) -> Optional[Path]:
    """One chart per page, in watchlist order. Returns None if there are none."""
    ordered = [chart_paths[r.symbol] for r in rows if r.symbol in chart_paths]
    if not ordered:
        return None
    pdf_path = Path(pdf_path)
    with PdfPages(pdf_path) as pdf:
        for png in ordered:
            img = plt.imread(png)
            h, w = img.shape[:2]
            fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
            fig.figimage(img)
            pdf.savefig(fig)
            plt.close(fig)
    return pdf_path

"""
HTML body for the daily watchlist email.

Built for a phone at 6 AM: the plan for the best names first, each with its
chart, then a compact table of everything else, then the diagnostics (funnel
and rules) at the bottom where they are out of the way.

Email clients ignore <style> blocks and most CSS, so everything is inline and
laid out with tables. Charts are referenced as cid: images; the sender attaches
them as related parts so they show inline instead of as attachments.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any, Optional

import pandas as pd

from watchlist_charts import setup_label

TOP_TRIGGERED_WITH_CHARTS = 10
TOP_FORMING_WITH_CHARTS = 6
# Grouped layout (when leading groups are known).
CARDS_PER_GROUP = 4          # chart cards per group; the rest of the group is a table
MAX_NAMES_PER_GROUP = 8      # one hot sector must not bury the others; overflow is in CSV/PDF
MAX_INLINE_CHARTS = 16       # whole email; keeps it ~1-2 MB

FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
INK = "#1f2937"
MUTED = "#6b7280"
LINE = "#e5e7eb"
ENTRY = "#1e3a8a"
STOP = "#d32f2f"
TARGET = "#2e7d32"
WATCH = "#f57c00"


def chart_cid(symbol: str) -> str:
    return f"chart-{symbol.lower()}"


def _money(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.2f}"


def _pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _level_cell(label: str, value: str, color: str) -> str:
    return (
        f'<td width="33%" style="padding:6px 4px;border-top:3px solid {color};text-align:center">'
        f'<div style="font-size:11px;color:{MUTED}">{label}</div>'
        f'<div style="font-size:16px;font-weight:700;color:{color}">{value}</div></td>'
    )


def _is_put(row) -> bool:
    return getattr(row, "side", "call") == "put"


def _card(row, rank: int, has_chart: bool) -> str:
    head = (
        f'<div style="font-size:18px;font-weight:700;color:{INK}">'
        f'{rank}. {escape(row.symbol)} '
        f'<span style="font-size:13px;font-weight:400;color:{MUTED}">'
        f'{escape(setup_label(row.setup))}</span></div>'
        f'<div style="font-size:12px;color:{MUTED};margin:2px 0 8px">'
        + (f"{'RW' if _is_put(row) else 'RS'} {row.rs} · " if getattr(row, "rs", None) else "")
        + f'Close {_money(row.close)} · ADR {_pct(row.adr_pct, signed=False)} · '
        + (f"R:R {row.rr:.1f} · " if row.rr is not None else "")
        + f'1W {_pct(row.perf_1w)} · 1M {_pct(row.perf_1m)}'
        + (f" · {escape(row.industry)}" if getattr(row, "industry", "") else "")
        + "</div>"
    )
    if row.bucket == "TRIGGERED":
        target_pct = 100.0 * (row.target / row.entry - 1) if row.entry and row.target else None
        levels = (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="border-collapse:collapse;background:#f9fafb;margin-bottom:8px"><tr>'
            + _level_cell("ENTRY (limit)", _money(row.entry), ENTRY)
            + _level_cell(f"STOP {'+' if _is_put(row) else '−'}{_pct(row.risk_pct, signed=False)}",
                          _money(row.stop), STOP)
            + _level_cell(f"TARGET {_pct(target_pct)}", _money(row.target), TARGET)
            + "</tr></table>"
        )
    else:
        levels = (
            f'<div style="font-size:13px;color:{WATCH};margin-bottom:8px">'
            f'{row.dist_ema8_adr:+.2f} ADR from the 8 EMA. '
            + ("Trigger: push above the 8 EMA or today's high, then close back below it."
               if _is_put(row) else
               "Trigger: undercut the 8 EMA or today's low, then close back above it.")
            + "</div>"
        )
    chart = (
        f'<img src="cid:{chart_cid(row.symbol)}" alt="{escape(row.symbol)} chart" width="600" '
        'style="display:block;width:100%;max-width:600px;height:auto;border:1px solid '
        f'{LINE}">'
        if has_chart
        else ""
    )
    return (
        f'<div style="padding:14px 0;border-bottom:1px solid {LINE}">{head}{levels}{chart}</div>'
    )


def _section(title: str, subtitle: str) -> str:
    return (
        f'<div style="margin:22px 0 4px;font-size:15px;font-weight:700;color:{INK};'
        f'text-transform:uppercase;letter-spacing:0.5px">{title}</div>'
        f'<div style="font-size:12px;color:{MUTED}">{subtitle}</div>'
    )


SHORT_SETUP = {
    "unr_pdl": "UnR PDL",
    "unr_ema8": "UnR 8EMA",
    "unr_ema21": "UnR 21EMA",
    "ema8_pullback": "Forming",
    "onr_pdh": "OnR PDH",
    "onr_ema8": "OnR 8EMA",
    "onr_ema21": "OnR 21EMA",
    "ema8_rally": "Forming",
}


def _compact_table(head: list[str], body: list[list[str]]) -> str:
    # Styles sit on the table and rows, not on every cell: with ~60 rows,
    # per-cell inline CSS pushes the email past Gmail's ~102 KB clipping limit.
    out = [
        '<table role="presentation" cellspacing="0" cellpadding="5" width="100%" '
        f'style="border-collapse:collapse;font-size:12px;color:{INK}">',
        f'<tr style="color:{MUTED};font-size:11px;text-align:left">'
        + "".join(f"<th>{h}</th>" for h in head) + "</tr>",
    ]
    for i, cells in enumerate(body):
        shade = ' style="background:#f6f7f9"' if i % 2 == 0 else ""
        out.append(f"<tr{shade}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _rest_tables(triggered: list, forming: list) -> str:
    """Six columns at most: what fits a phone without sideways scrolling."""
    parts = []
    if triggered:
        parts.append(_compact_table(
            ["Triggered", "Setup", "Entry", "Stop", "Target", "R:R"],
            [[f"<b>{escape(r.symbol)}</b>", escape(SHORT_SETUP.get(r.setup, r.setup)),
              _money(r.entry), _money(r.stop), _money(r.target),
              f"{r.rr:.1f}" if r.rr is not None else "-"] for r in triggered],
        ))
    if forming:
        parts.append('<div style="height:12px"></div>' if parts else "")
        parts.append(_compact_table(
            ["Forming", "Close", "From 8 EMA", "ADR", "1W", "1M"],
            [[f"<b>{escape(r.symbol)}</b>", _money(r.close), f"{r.dist_ema8_adr:+.2f} ADR",
              _pct(r.adr_pct, signed=False), _pct(r.perf_1w), _pct(r.perf_1m)]
             for r in forming],
        ))
    return "".join(parts)


FUNNEL_LABELS = [
    ("evaluated", "evaluated"),
    ("fail_history", "not enough history"),
    ("fail_price", "price too low"),
    ("fail_volume", "share volume too low"),
    ("fail_dollar_volume", "dollar volume too low"),
    ("fail_adr", "ADR% too low"),
    ("fail_trend", "trend on the wrong side"),
    ("fail_extended", "extended from the 8 EMA"),
    ("fail_not_near_ema8", "healthy but not near the 8 EMA"),
    ("triggered", "→ triggered"),
    ("forming", "→ forming"),
]


def _footer(stats: dict[str, Any], rules: list[str]) -> str:
    funnel = stats.get("funnel", {}) or {}
    items = [f"{label}: {funnel[key]:,}" for key, label in FUNNEL_LABELS if key in funnel]
    return (
        f'<div style="margin-top:26px;padding-top:12px;border-top:2px solid {LINE};'
        f'font-size:11px;color:{MUTED};line-height:1.6">'
        f"<b>Where names dropped out</b><br>{' · '.join(items)}<br><br>"
        f"<b>The rules</b><br>{'<br>'.join(escape(r) for r in rules)}<br><br>"
        f"Universe {stats.get('universe_count', 0):,} · with data "
        f"{stats.get('with_data', 0):,} · runtime {escape(str(stats.get('runtime', '')))}<br>"
        "Attached: every chart (PDF) and the full list (CSV). Research only; no orders are "
        "placed.</div>"
    )


def _grouped(rows: list, budget: int = MAX_INLINE_CHARTS
             ) -> tuple[list[tuple[str, list, list, int]], list]:
    """Split ranked rows into (group, card_rows, table_rows, hidden) plus the outside list.

    Rows arrive ordered by watchlist_ranking.rank_rows, so groups come out
    strongest first and names inside a group best first. Each group shows at
    most MAX_NAMES_PER_GROUP names; ``hidden`` counts the rest.
    """
    groups: dict[str, list] = {}
    outside = []
    for row in rows:
        if getattr(row, "group", ""):
            groups.setdefault(row.group, []).append(row)
        else:
            outside.append(row)
    layout = []
    for name, members in groups.items():
        shown = members[:MAX_NAMES_PER_GROUP]
        take = max(0, min(CARDS_PER_GROUP, budget, len(shown)))
        budget -= take
        layout.append((name, shown[:take], shown[take:], len(members) - len(shown)))
    return layout, outside


def _is_grouped(rows: list, stats: dict[str, Any]) -> bool:
    return stats.get("leadership") is not None and any(getattr(r, "group", "") for r in rows)


def _regime_banner(regime) -> str:
    if regime is None:
        return ""
    color = {3: "#2e7d32", 2: "#558b2f", 1: WATCH, 0: STOP}[regime.level]
    detail = " · ".join(f"{k} {escape(v.lower())}" for k, v in regime.detail.items())
    return (
        f'<div style="margin:12px 0 0;padding:10px 12px;border-left:5px solid {color};'
        f'background:#f9fafb"><div style="font-size:14px;font-weight:700;color:{color}">'
        f"Market: {escape(regime.label)}</div>"
        f'<div style="font-size:12px;color:{INK}">{escape(regime.advice)}</div>'
        f'<div style="font-size:11px;color:{MUTED}">{detail}</div></div>'
    )


def _leaders_table(leadership, layout) -> str:
    counts = {name: len(cards) + len(rest) + hidden for name, cards, rest, hidden in layout}
    body = []
    for table, prefix in ((leadership.sectors, ""), (leadership.themes, "Theme: ")):
        if table is None or table.empty:
            continue
        for name, r in table[table["leading"]].iterrows():
            group = f"{prefix}{name}"
            body.append([
                f"<b>{escape(group)}</b>", f"{int(r['top5_count'])}/3",
                _pct(r["1W"]), _pct(r["1M"]), _pct(r["3M"]), str(counts.get(group, 0)),
            ])
    if not body:
        return ""
    put = getattr(leadership, "side", "call") == "put"
    return _compact_table(["Lagging sector" if put else "Leading group",
                           "Bottom 5 in" if put else "Top 5 in", "1W", "1M", "3M", "Names"], body)


def _group_header(name: str, leadership) -> str:
    table, key = (leadership.themes, name.removeprefix("Theme: ")) if name.startswith(
        "Theme: ") else (leadership.sectors, name)
    sub = ""
    if table is not None and key in table.index:
        r = table.loc[key]
        five = "Bottom 5" if getattr(leadership, "side", "call") == "put" else "Top 5"
        sub = (f"{five} in {int(r['top5_count'])}/3 windows · 1W {_pct(r['1W'])} · "
               f"1M {_pct(r['1M'])} · 3M {_pct(r['3M'])}")
        if "members" in r and not pd.isna(r.get("members")):
            sub += f" · {int(r['members'])} liquid members"
    return _section(escape(name), sub)


def _group_rest_table(rows: list) -> str:
    return _compact_table(
        ["Symbol", "Setup", "Entry", "Stop", "Target", "RS"],
        [[f"<b>{escape(r.symbol)}</b>", escape(SHORT_SETUP.get(r.setup, r.setup)),
          _money(r.entry), _money(r.stop), _money(r.target), str(r.rs or "-")] for r in rows],
    )


def _outside_table(rows: list) -> str:
    return _compact_table(
        ["Symbol", "Setup", "Sector / industry", "RS"],
        [[f"<b>{escape(r.symbol)}</b>", escape(SHORT_SETUP.get(r.setup, r.setup)),
          escape(getattr(r, "industry", "") or getattr(r, "sector", "") or "unknown"),
          str(r.rs or "-")] for r in rows],
    )


SIDE_BANNER = {
    "call": ("CALLS", "SPY and QQQ are above the 50 EMA: calls in the strongest sectors."),
    "put": ("PUTS", "SPY and QQQ are below the 50 EMA: puts in the weakest sectors."),
    "both": ("CALLS + PUTS", "SPY and QQQ split on the 50 EMA: both sides, small size."),
}


def _side_sections(rows: list, stats: dict[str, Any]) -> list[tuple[str, list, Any, int]]:
    """(side, rows, leadership, chart budget) for each side shown today."""
    sides = stats.get("sides") or [(getattr(rows[0], "side", "call") if rows else "call",
                                    stats.get("leadership"))]
    budget = MAX_INLINE_CHARTS // max(1, len(sides))
    return [(side, [r for r in rows if getattr(r, "side", "call") == side], lead, budget)
            for side, lead in sides]


def _render_grouped(rows: list, leadership, charted: set[str], budget: int,
                    side: str = "call") -> list[str]:
    layout, outside = _grouped(rows, budget)
    put = side == "put"
    word = "Lagging" if put else "Leading"
    parts = [_regime_banner(leadership.regime)]
    parts.append(_section(
        f"{'PUTS' if put else 'CALLS'}: {word.lower()} sectors today",
        f"SPDR sectors in the {'bottom' if put else 'top'} 5 over 1W / 1M / 3M, overlapping "
        "in at least 2 windows (max 3). Grouped for reading: the backtest found no edge from "
        "sector or RS alone.",
    ))
    parts.append(_leaders_table(leadership, layout))
    if not layout:
        parts.append(f'<p style="font-size:14px">No names in the {word.lower()} sectors '
                     "today.</p>")
    for name, cards, rest, hidden in layout:
        parts.append(_group_header(name, leadership))
        parts += [_card(r, i, r.symbol in charted) for i, r in enumerate(cards, 1)]
        if rest:
            parts.append('<div style="height:8px"></div>' + _group_rest_table(rest))
        if hidden:
            parts.append(
                f'<div style="font-size:12px;color:{MUTED};margin-top:6px">+{hidden} more '
                f"{escape(name)} names, lower ranked: in the attached CSV and PDF.</div>"
            )
    if outside:
        parts.append(_section(
            f"{'PUTS' if put else 'CALLS'}: outside the {word.lower()} sectors ({len(outside)})",
            "Shown while the sector filter is being validated. Charts are in the PDF.",
        ))
        parts.append(_outside_table(outside))
    return parts


def render_html(
    rows: list, stats: dict[str, Any], charted: set[str], rules: list[str]
) -> str:
    """The full email body. ``charted`` is the set of symbols with a chart image."""
    if _is_grouped(rows, stats):
        parts = []
        for side, side_rows, lead, budget in _side_sections(rows, stats):
            if lead is not None:
                parts += _render_grouped(side_rows, lead, charted, budget, side)
        return _wrap(rows, stats, parts, rules)
    triggered = [r for r in rows if r.bucket == "TRIGGERED"]
    forming = [r for r in rows if r.bucket == "FORMING"]
    top_t = triggered[:TOP_TRIGGERED_WITH_CHARTS]
    top_f = forming[:TOP_FORMING_WITH_CHARTS]
    rest_t, rest_f = triggered[len(top_t):], forming[len(top_f):]

    parts = []
    if top_t:
        parts.append(_section(
            f"Triggered: top {len(top_t)}",
            "Reclaim fired on the last bar. Plan: a limit back at the reclaimed level for the "
            "next session.",
        ))
        parts += [_card(r, i, r.symbol in charted) for i, r in enumerate(top_t, 1)]
    if top_f:
        parts.append(_section(
            f"Forming: top {len(top_f)}",
            "Pulling into the 8 EMA, not reclaimed yet. Watch these for tomorrow's trigger.",
        ))
        parts += [_card(r, i, r.symbol in charted) for i, r in enumerate(top_f, 1)]
    if rest_t or rest_f:
        parts.append(_section(
            f"Everything else ({len(rest_t) + len(rest_f)})",
            "Charts for these are in the attached PDF.",
        ))
        parts.append(_rest_tables(rest_t, rest_f))
    return _wrap(rows, stats, parts, rules)


def _wrap(rows: list, stats: dict[str, Any], body: list[str], rules: list[str]) -> str:
    """Header (title, date, counts) + body + footer."""
    try:
        day = date.fromisoformat(stats.get("as_of", "")).strftime("%A, %b %d")
    except ValueError:
        day = stats.get("as_of", "")
    triggered = sum(1 for r in rows if r.bucket == "TRIGGERED")
    forming = sum(1 for r in rows if r.bucket == "FORMING")
    head = [
        f'<div style="font-family:{FONT};color:{INK};max-width:640px;margin:0 auto;'
        'padding:8px 12px">',
        '<div style="font-size:22px;font-weight:800">Daily Watchlist</div>',
        f'<div style="font-size:13px;color:{MUTED};margin-bottom:10px">'
        f"Data as of the close on {escape(day)}</div>",
        '<table role="presentation" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="background:{ENTRY};color:#fff;padding:6px 12px;font-size:14px;'
        f'font-weight:700">{triggered} triggered</td><td width="8"></td>'
        f'<td style="background:{WATCH};color:#fff;padding:6px 12px;font-size:14px;'
        f'font-weight:700">{forming} forming</td></tr></table>',
    ]
    if stats.get("side"):
        chip, text = SIDE_BANNER.get(stats["side"], SIDE_BANNER["call"])
        head.append(
            f'<div style="margin-top:10px;font-size:14px;color:{INK}"><b>Today: {chip}.</b> '
            f"{escape(text)}</div>")
    if not rows:
        head.append('<p style="font-size:14px">Nothing passed the rules today.</p>')
    return "".join(head + body + [_footer(stats, rules), "</div>"])


def emailed_chart_symbols(rows: list, stats: Optional[dict[str, Any]] = None) -> list[str]:
    """Symbols whose charts go inline in the body (the rest live in the PDF)."""
    if stats is not None and _is_grouped(rows, stats):
        out = []
        for _, side_rows, lead, budget in _side_sections(rows, stats):
            if lead is not None:
                layout, _ = _grouped(side_rows, budget)
                out += [r.symbol for _, cards, _, _ in layout for r in cards]
        return out
    triggered = [r for r in rows if r.bucket == "TRIGGERED"][:TOP_TRIGGERED_WITH_CHARTS]
    forming = [r for r in rows if r.bucket == "FORMING"][:TOP_FORMING_WITH_CHARTS]
    return [r.symbol for r in triggered + forming]

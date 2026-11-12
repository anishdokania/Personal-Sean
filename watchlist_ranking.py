"""
Leading groups and relative strength: the Blueprint's "leading names in
leading sectors", made mechanical.

The eBook's scan (pp. 64-73), step by step:

  1. Market first. SPY / QQQ against the 8, 21 and 50 EMA set the regime;
     under all three means no new longs.
  2. Leading sectors. Take the top 5 sectors over 1 week, 1 month and
     3 months and trade the ones that overlap. Literally applied to 11
     sectors this often returns 4-5, so ties break on 1M then 3M return and
     at most LEADING_SECTORS_MAX are kept.
  3. Themes. The newer material trades themes (AI, uranium, solar) that sit
     inside sectors. The same top-5 overlap rule runs on industries outside
     the leading sectors, measured from our own liquid universe (median
     member return), so a leading theme in a lagging sector still counts.
  4. Strong names first. Inside the allowed groups, names rank on relative
     strength, with setup quality as the smaller term: "strong names with
     average setups are better than weaker names with amazing setups."

Sector and industry labels come from Yahoo (it groups solar, software, etc.
correctly; the Nasdaq screener does not). They are cached in
data/industry_map.csv so a daily run only looks up tickers it has not seen.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}
WINDOWS = {"1W": 5, "1M": 21, "3M": 63}  # the Blueprint's three FinViz periods
TOP_N = 5                     # "top 5 performers" per window [Blueprint]
MIN_WINDOWS_IN_TOP = 2        # "see if any of them overlap"
LEADING_SECTORS_MAX = 3       # the 2-3 sectors the eBook ends up trading
LEADING_THEMES_MAX = 5
MIN_INDUSTRY_MEMBERS = 5      # an "industry" of 2 stocks is noise, not a theme

# Relative strength: percentile of a recent-weighted return across the liquid
# universe. Weighted to 1M/3M because the scan looks for recent leaders.
RS_WEIGHTS = {"1W": 0.2, "1M": 0.4, "3M": 0.4}

# Liquid universe for RS and industry strength: same floors as the watchlist.
LIQUID_MIN_PRICE = 5.0
LIQUID_MIN_AVG_VOLUME = 1_000_000

# Final score inside the allowed groups: name strength outweighs the setup.
WEIGHT_RS = 0.7
WEIGHT_SETUP = 0.3
RR_CAP = 5.0                  # beyond this R:R only means a tiny stop
STOP_ADR_MIN = 0.25           # stops tighter than this sit inside normal noise
STOP_ADR_MAX = 1.5

INDUSTRY_CACHE = Path("data/industry_map.csv")
FETCH_PAUSE_SECONDS = 0.4

REGIMES = {
    3: ("Above 8, 21 and 50 EMA", "Momentum: breakouts follow through. Full size allowed."),
    2: ("Below 8, above 21 EMA", "Trend intact, digesting. Half size."),
    1: ("Below 21, above 50 EMA", "Buyers need to step up. Small size, dip-buys at the 50."),
    0: ("Below 8, 21 and 50 EMA", "No new longs. De-risk until the EMAs are reclaimed."),
}


# --------------------------------------------------------------------------- #
# Sector / industry labels
# --------------------------------------------------------------------------- #
def _read_cache(path: Path) -> dict[str, tuple[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            r["symbol"]: (r["sector"], r["industry"])
            for r in csv.DictReader(handle)
            if r.get("symbol")
        }


def _write_cache(path: Path, mapping: dict[str, tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "sector", "industry"])
        for symbol in sorted(mapping):
            writer.writerow([symbol, *mapping[symbol]])


class RateLimited(Exception):
    """Yahoo answered 429. Stop looking things up for this run."""


def _lookup(symbol: str) -> Optional[tuple[str, str]]:
    """(sector, industry) from Yahoo; None if the lookup failed (not cached)."""
    try:
        info = yf.Ticker(symbol).info or {}
    except YFRateLimitError as exc:
        raise RateLimited(str(exc)) from exc
    except Exception:
        return None
    return str(info.get("sector") or ""), str(info.get("industry") or "")


def load_industry_map(
    symbols: Iterable[str],
    cache_path: Path = INDUSTRY_CACHE,
    max_fetch: Optional[int] = 150,
) -> dict[str, tuple[str, str]]:
    """Sector and industry for ``symbols``, looking up only uncached ones.

    Lookups are sequential with a pause, and the first rate-limit ends them for
    this run: Yahoo throttles parallel ``.info`` calls hard, and shared CI IPs
    worse. Only real answers are cached; a failed lookup is retried next run.
    Anything still unknown gets a sector from the Nasdaq screener (one request)
    so it can still join a leading sector, though not an industry theme.
    """
    mapping = _read_cache(cache_path)
    wanted = list(dict.fromkeys(symbols))
    missing = [s for s in wanted if s not in mapping]
    if max_fetch is not None:
        missing = missing[:max_fetch]
    fetched = 0
    if missing:
        print(f"Looking up sector/industry for {len(missing)} new symbols...", flush=True)
        try:
            for symbol in missing:
                labels = _lookup(symbol)
                if labels is not None:
                    mapping[symbol] = labels
                    fetched += 1
                time.sleep(FETCH_PAUSE_SECONDS)
        except RateLimited:
            print(f"  ! Yahoo rate-limited after {fetched} lookups; the rest wait for "
                  "the next run.", flush=True)
        if fetched:
            _write_cache(cache_path, mapping)

    result = {s: mapping[s] for s in wanted if s in mapping and mapping[s][0]}
    unknown = [s for s in wanted if s not in result]
    if unknown:
        fallback = nasdaq_sectors()
        for symbol in unknown:
            if fallback.get(symbol):
                result[symbol] = (fallback[symbol], "")
    return result


NASDAQ_TO_SECTOR = {
    "Technology": "Technology",
    "Health Care": "Healthcare",
    "Finance": "Financial Services",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Basic Materials": "Basic Materials",
    "Telecommunications": "Communication Services",
}
NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true"
)


def nasdaq_sectors() -> dict[str, str]:
    """Symbol -> sector (in Yahoo's names) for every Nasdaq-screener listing."""
    try:
        response = requests.get(
            NASDAQ_SCREENER_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30
        )
        response.raise_for_status()
        rows = response.json()["data"]["rows"]
    except Exception as exc:
        print(f"  ! Nasdaq sector fallback unavailable: {exc}", flush=True)
        return {}
    return {
        str(r.get("symbol", "")).strip(): NASDAQ_TO_SECTOR.get(str(r.get("sector", "")), "")
        for r in rows
    }


# --------------------------------------------------------------------------- #
# Returns, leadership, regime, RS
# --------------------------------------------------------------------------- #
def _ret(closes: pd.Series, bars: int) -> float:
    closes = closes.dropna()
    if len(closes) <= bars or closes.iloc[-1 - bars] <= 0:
        return np.nan
    return 100.0 * (closes.iloc[-1] / closes.iloc[-1 - bars] - 1.0)


def overlap_leaders(returns: pd.DataFrame, max_leaders: int) -> pd.DataFrame:
    """Blueprint top-5 overlap on a (group x window) table of % returns.

    Adds ``top5_count`` (in how many windows the group is top 5), ``rank`` and
    ``leading``. Ties break on 1M, then 3M return.
    """
    out = returns.copy()
    out["top5_count"] = 0
    for window in WINDOWS:
        top = out[window].nlargest(TOP_N).index
        out.loc[top, "top5_count"] += 1
    out = out.sort_values(["top5_count", "1M", "3M"], ascending=False)
    out["rank"] = range(1, len(out) + 1)
    out["leading"] = (out["top5_count"] >= MIN_WINDOWS_IN_TOP) & (out["rank"] <= max_leaders)
    return out


def sector_leadership(etf_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {}
    for sector, etf in SECTOR_ETFS.items():
        frame = etf_frames.get(etf)
        if frame is None or frame.empty:
            continue
        rows[sector] = {w: _ret(frame["Close"], b) for w, b in WINDOWS.items()}
    table = pd.DataFrame(rows).T.dropna()
    return overlap_leaders(table, LEADING_SECTORS_MAX)


def is_liquid(frame: pd.DataFrame) -> bool:
    if frame is None or len(frame) < WINDOWS["3M"] + 1:
        return False
    close = float(frame["Close"].iloc[-1])
    return close > LIQUID_MIN_PRICE and float(frame["Volume"].tail(20).mean()) > LIQUID_MIN_AVG_VOLUME


def stock_returns(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """1W / 1M / 3M % returns for every liquid symbol."""
    data = {
        s: {w: _ret(f["Close"], b) for w, b in WINDOWS.items()}
        for s, f in frames.items()
        if is_liquid(f)
    }
    return pd.DataFrame(data).T.dropna()


def rs_ratings(returns: pd.DataFrame) -> pd.Series:
    """RS 1-99: percentile of the weighted 1W/1M/3M return across the liquid universe."""
    if returns.empty:
        return pd.Series(dtype=float)
    blended = sum(returns[w] * wt for w, wt in RS_WEIGHTS.items())
    return (blended.rank(pct=True) * 98 + 1).round().astype(int)


def industry_leadership(
    returns: pd.DataFrame,
    industry_map: dict[str, tuple[str, str]],
    exclude_sectors: Iterable[str] = (),
) -> pd.DataFrame:
    """Top-5 overlap on industries, each measured by its members' median return.

    Industries whose (majority) sector is already leading are left out: their
    names are in through the sector, and a theme slot is for bringing in a
    strong group from a sector that is not leading.
    """
    labels = pd.DataFrame(
        {s: industry_map.get(s, ("", "")) for s in returns.index}, index=["sector", "industry"]
    ).T
    labelled = returns.join(labels).loc[lambda d: d["industry"] != ""]
    grouped = labelled.groupby("industry")
    medians = grouped[list(WINDOWS)].median()
    medians["members"] = grouped.size()
    medians["sector"] = grouped["sector"].agg(lambda s: s.mode().iat[0] if not s.mode().empty else "")
    medians = medians[medians["members"] >= MIN_INDUSTRY_MEMBERS]
    medians = medians[~medians["sector"].isin(set(exclude_sectors))]
    if medians.empty:
        return medians
    return overlap_leaders(medians, LEADING_THEMES_MAX)


@dataclass
class Regime:
    level: int                  # 3 = above all EMAs ... 0 = below all
    label: str
    advice: str
    detail: dict[str, str]      # index -> its own state


def _ema_state(closes: pd.Series) -> int:
    close = float(closes.iloc[-1])
    emas = [float(closes.ewm(span=s, adjust=False).mean().iloc[-1]) for s in (8, 21, 50)]
    if close > emas[0] and close > emas[1] and close > emas[2]:
        return 3
    if close > emas[1] and close > emas[2]:
        return 2
    if close > emas[2]:
        return 1
    return 0


def market_regime(index_frames: dict[str, pd.DataFrame]) -> Optional[Regime]:
    """The weaker of SPY and QQQ sets the regime: both must back a long."""
    states = {
        name: _ema_state(frame["Close"])
        for name, frame in index_frames.items()
        if frame is not None and len(frame) > 60
    }
    if not states:
        return None
    level = min(states.values())
    label, advice = REGIMES[level]
    return Regime(level, label, advice, {k: REGIMES[v][0] for k, v in states.items()})


# --------------------------------------------------------------------------- #
# Ranking the watchlist
# --------------------------------------------------------------------------- #
def setup_quality(row) -> float:
    """0..1. Triggered: capped R:R and a stop sized like a real stop. Forming:
    closeness to the 8 EMA."""
    if row.bucket == "TRIGGERED":
        rr_part = min(row.rr or 0.0, RR_CAP) / RR_CAP
        stop_adr = (row.risk_pct or 0.0) / row.adr_pct if row.adr_pct else 0.0
        stop_part = 1.0 if STOP_ADR_MIN <= stop_adr <= STOP_ADR_MAX else 0.3
        return 0.5 * rr_part + 0.5 * stop_part
    return max(0.0, 1.0 - abs(row.dist_ema8_adr))


@dataclass
class Leadership:
    sectors: pd.DataFrame
    themes: pd.DataFrame
    regime: Optional[Regime]
    rs: pd.Series

    @property
    def leading_sectors(self) -> list[str]:
        return list(self.sectors.index[self.sectors["leading"]]) if not self.sectors.empty else []

    @property
    def leading_themes(self) -> list[str]:
        return list(self.themes.index[self.themes["leading"]]) if not self.themes.empty else []


def compute_leadership(
    frames: dict[str, pd.DataFrame],
    reference_frames: dict[str, pd.DataFrame],
    industry_map: dict[str, tuple[str, str]],
) -> Leadership:
    returns = stock_returns(frames)
    sectors = sector_leadership(reference_frames)
    leading_sectors = list(sectors.index[sectors["leading"]]) if not sectors.empty else []
    return Leadership(
        sectors=sectors,
        themes=industry_leadership(returns, industry_map, exclude_sectors=leading_sectors),
        regime=market_regime({k: reference_frames.get(k) for k in ("SPY", "QQQ")}),
        rs=rs_ratings(returns),
    )


def rank_rows(rows: list, leadership: Leadership, industry_map: dict[str, tuple[str, str]]) -> list:
    """Tag each row with its group and RS, and return them in the new order.

    Order: leading sectors (strongest first), then leading themes from
    outside those sectors, then everything else. Inside a group: triggered before forming, then
    by score. Rows are annotated in place (sector, industry, group, rs, score).
    """
    sector_rank = {s: i for i, s in enumerate(leadership.leading_sectors)}
    theme_rank = {t: i for i, t in enumerate(leadership.leading_themes)}
    for row in rows:
        sector, industry = industry_map.get(row.symbol, ("", ""))
        row.sector, row.industry = sector, industry
        row.rs = int(leadership.rs.get(row.symbol, 0)) or None
        # Leading sectors are the primary route [Blueprint]; a leading industry
        # is the exception that lets a theme in from a lagging sector.
        if sector in sector_rank:
            row.group, row.group_order = sector, (0, sector_rank[sector])
        elif industry in theme_rank:
            row.group, row.group_order = f"Theme: {industry}", (1, theme_rank[industry])
        else:
            row.group, row.group_order = "", (2, 0)
        rs_part = (row.rs or 0) / 99.0
        row.score = round(WEIGHT_RS * rs_part + WEIGHT_SETUP * setup_quality(row), 4)
    return sorted(
        rows, key=lambda r: (r.group_order, r.group, r.bucket != "TRIGGERED", -r.score)
    )


def download_reference(history_days: int = 200) -> dict[str, pd.DataFrame]:
    """Sector ETFs plus SPY and QQQ, in one request."""
    tickers = list(SECTOR_ETFS.values()) + ["SPY", "QQQ"]
    raw = yf.download(
        tickers, period=f"{history_days}d", interval="1d", auto_adjust=True,
        progress=False, group_by="ticker", threads=True,
    )
    frames = {}
    for t in tickers:
        try:
            frame = raw[t].dropna(how="all")
        except KeyError:
            continue
        if not frame.empty:
            frames[t] = frame
    return frames

"""
Shared data models for the post-primary setup detector layer.

The detector layer is intentionally tag based. Interest rank is only a sort
helper; chart eligibility is driven by detector hits plus obvious rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_ORDER = {
    CONFIDENCE_LOW: 1,
    CONFIDENCE_MEDIUM: 2,
    CONFIDENCE_HIGH: 3,
}

HIGH_VALUE_TAGS = {
    "INSIDE_DAY_NEAR_HIGHS",
    "BIG_BASE_NEAR_HIGHS",
    "RIGHT_SIDE_OF_BASE",
    "POSSIBLE_ACCUMULATION_BASE",
    "POWER_EARNINGS_GAP",
    "CATALYST_GAP",
    "POST_GAP_FLAG",
    "BREAKOUT_RETEST",
    "HIGH_VOLUME_BREAKOUT",
    "FAILED_BREAKDOWN_RECLAIM",
    "LEADING_NAME_NEAR_TRIGGER",
    "HIGH_RVOL_RECLAIM",
    "DAILY_BREAKOUT_CONFIRMED",
}

WARNING_TAGS = {
    "MILD_EXTENSION",
    "OVEREXTENDED",
    "CHASE_RISK",
    "NO_CLEAR_TRIGGER",
    "TRIGGER_TOO_FAR",
    "STOP_TOO_WIDE",
    "RESISTANCE_TOO_CLOSE",
    "FAILED_BREAKOUT",
    "SHOOTING_STAR_WARNING",
    "UPPER_WICK_SUPPLY",
    "BREAKOUT_FAILURE",
    "DO_NOT_CHASE",
    "HEAVY_RED_VOLUME",
}

CSV_COLUMNS = [
    "ticker",
    "company",
    "sector",
    "close",
    "volume",
    "avg_volume",
    "rel_volume",
    "detector_count",
    "detector_names",
    "detector_confidence",
    "detector_tags",
    "high_value_tags",
    "warning_tags",
    "setup_family",
    "trigger_level",
    "stop_reference",
    "interest_rank",
    "chart_needed",
    "reject_reason",
    "notes",
    "chart_6m_path",
    "chart_1y_path",
]


def join_tags(values: Iterable[Any]) -> str:
    """Return stable semicolon-delimited text for tags and notes."""
    cleaned = sorted({str(value).strip() for value in values if str(value).strip()})
    return "; ".join(cleaned)


def join_preserving_order(values: Iterable[Any]) -> str:
    """Return semicolon-delimited text without reordering distinct values."""
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return "; ".join(output)


@dataclass
class DetectorHit:
    """One loose setup detector firing for a ticker."""

    name: str
    confidence: str
    tags: list[str] = field(default_factory=list)
    setup_family: Optional[str] = None
    trigger_level: Optional[float] = None
    stop_reference: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "setup_family": self.setup_family,
            "trigger_level": self.trigger_level,
            "stop_reference": self.stop_reference,
            "notes": list(self.notes),
        }


@dataclass
class DetectorCandidate:
    """Detector audit result for one primary-gated ticker."""

    ticker: str
    company: str = ""
    sector: str = ""
    close: Optional[float] = None
    volume: Optional[float] = None
    avg_volume: Optional[float] = None
    rel_volume: Optional[float] = None
    hits: list[DetectorHit] = field(default_factory=list)
    detector_tags: set[str] = field(default_factory=set)
    high_value_tags: set[str] = field(default_factory=set)
    warning_tags: set[str] = field(default_factory=set)
    setup_family: str = "Unclassified"
    trigger_level: Optional[float] = None
    stop_reference: Optional[float] = None
    interest_rank: float = 0.0
    chart_needed: bool = False
    reject_reason: str = ""
    notes: list[str] = field(default_factory=list)
    chart_6m_path: str = ""
    chart_1y_path: str = ""
    source_error: str = ""

    def add_hit(self, hit: DetectorHit) -> None:
        """Attach a detector hit and update tag summaries."""
        self.hits.append(hit)
        for tag in hit.tags:
            if not tag:
                continue
            self.detector_tags.add(tag)
            if tag in HIGH_VALUE_TAGS:
                self.high_value_tags.add(tag)
            if tag in WARNING_TAGS:
                self.warning_tags.add(tag)

        if hit.trigger_level is not None and self.trigger_level is None:
            self.trigger_level = hit.trigger_level
        if hit.stop_reference is not None and self.stop_reference is None:
            self.stop_reference = hit.stop_reference
        if hit.setup_family and self.setup_family == "Unclassified":
            self.setup_family = hit.setup_family
        for note in hit.notes:
            if note:
                self.notes.append(note)

    @property
    def detector_count(self) -> int:
        return len(self.hits)

    @property
    def detector_names(self) -> list[str]:
        return [hit.name for hit in self.hits]

    @property
    def detector_confidence_summary(self) -> str:
        parts = [f"{hit.name}={hit.confidence}" for hit in self.hits]
        return join_preserving_order(parts)

    def to_record(self) -> dict[str, Any]:
        """Return a CSV-ready row."""
        return {
            "ticker": self.ticker,
            "company": self.company,
            "sector": self.sector,
            "close": self.close,
            "volume": self.volume,
            "avg_volume": self.avg_volume,
            "rel_volume": self.rel_volume,
            "detector_count": self.detector_count,
            "detector_names": join_preserving_order(self.detector_names),
            "detector_confidence": self.detector_confidence_summary,
            "detector_tags": join_tags(self.detector_tags),
            "high_value_tags": join_tags(self.high_value_tags),
            "warning_tags": join_tags(self.warning_tags),
            "setup_family": self.setup_family,
            "trigger_level": self.trigger_level,
            "stop_reference": self.stop_reference,
            "interest_rank": self.interest_rank,
            "chart_needed": self.chart_needed,
            "reject_reason": self.reject_reason,
            "notes": join_preserving_order(self.notes),
            "chart_6m_path": self.chart_6m_path,
            "chart_1y_path": self.chart_1y_path,
        }

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly detector result."""
        return {
            **self.to_record(),
            "detector_names": self.detector_names,
            "detector_tags": sorted(self.detector_tags),
            "high_value_tags": sorted(self.high_value_tags),
            "warning_tags": sorted(self.warning_tags),
            "notes": list(dict.fromkeys(self.notes)),
            "hits": [hit.as_dict() for hit in self.hits],
            "source_error": self.source_error,
        }

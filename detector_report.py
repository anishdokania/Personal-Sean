"""
CSV, JSON, and Markdown output for the setup detector layer.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from detector_models import CSV_COLUMNS, DetectorCandidate, join_preserving_order


FAMILY_GROUPS = [
    "Leaders near highs",
    "Right-side/base setups",
    "Inside-day compression",
    "Breakout/retest",
    "Possible accumulation/emerging reclaim",
    "Power gap/catalyst gap",
    "High RVOL unusual activity",
    "Trend/reclaim",
    "Unclassified",
]


def _format_price(value: Any) -> str:
    """Format an optional price level."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "None"
    if pd.isna(numeric):
        return "None"
    return f"{numeric:.2f}"


def _candidate_reason(candidate: DetectorCandidate) -> str:
    """Build one concise reason string for report rows."""
    tags = sorted(candidate.high_value_tags) or sorted(candidate.detector_tags)
    tag_text = ", ".join(tags[:5]) if tags else "detector tags"
    warning_text = (
        f" Warning: {', '.join(sorted(candidate.warning_tags)[:3])}."
        if candidate.warning_tags
        else ""
    )
    return (
        f"{candidate.ticker}: {tag_text}. "
        f"Trigger {_format_price(candidate.trigger_level)}; "
        f"stop/ref {_format_price(candidate.stop_reference)}."
        f"{warning_text}"
    )


def detector_candidates_to_dataframe(
    candidates: list[DetectorCandidate],
) -> pd.DataFrame:
    """Convert detector candidates into the requested CSV schema."""
    rows = [candidate.to_record() for candidate in candidates]
    frame = pd.DataFrame(rows)
    for column in CSV_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame.loc[:, CSV_COLUMNS]


def build_detector_report_markdown(
    candidates: list[DetectorCandidate],
    primary_gated_count: int,
    detector_failures: Optional[dict[str, str]] = None,
    generated_at: Optional[datetime] = None,
) -> str:
    """Build a grouped Markdown audit report for detector candidates."""
    generated_at = generated_at or datetime.now()
    detector_failures = detector_failures or {}
    detector_hits_count = sum(1 for candidate in candidates if candidate.detector_count > 0)
    rejected_count = sum(1 for candidate in candidates if candidate.reject_reason)
    chart_needed_count = sum(1 for candidate in candidates if candidate.chart_needed)

    lines = [
        "# Post-Primary Setup Detector Report",
        "",
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Primary-gated stocks | {primary_gated_count} |",
        f"| Stocks evaluated by detectors | {len(candidates)} |",
        f"| Stocks with detector hits | {detector_hits_count} |",
        f"| Chart review candidates | {chart_needed_count} |",
        f"| Rejected by obvious reject conditions | {rejected_count} |",
        f"| Detector data failures | {len(detector_failures)} |",
        "",
        "The detector layer is a high-recall retrieval stage. It keeps names for visual chart review when any high-value detector fires or when multiple medium-value detectors cluster. Interest rank is used only for sorting.",
        "",
        "## Top Candidates By Setup Family",
        "",
    ]

    kept = [candidate for candidate in candidates if candidate.chart_needed]
    kept.sort(key=lambda item: item.interest_rank, reverse=True)

    for family in FAMILY_GROUPS:
        family_candidates = [
            candidate for candidate in kept if candidate.setup_family == family
        ]
        lines.append(f"### {family}")
        lines.append("")
        if not family_candidates:
            lines.append("- None")
            lines.append("")
            continue

        for candidate in family_candidates[:25]:
            tags = sorted(candidate.high_value_tags) or sorted(candidate.detector_tags)
            warnings = (
                f" | Warnings: {', '.join(sorted(candidate.warning_tags)[:3])}"
                if candidate.warning_tags
                else ""
            )
            lines.append(
                "- "
                f"{candidate.ticker} | rank {candidate.interest_rank:g} | "
                f"tags: {', '.join(tags[:6]) or 'None'} | "
                f"trigger: {_format_price(candidate.trigger_level)} | "
                f"stop/ref: {_format_price(candidate.stop_reference)}"
                f"{warnings} | why: {_candidate_reason(candidate)}"
            )
        lines.append("")

    rejected = [candidate for candidate in candidates if candidate.reject_reason]
    rejected.sort(key=lambda item: item.interest_rank, reverse=True)
    lines.extend(
        [
            "## Rejected Or Deferred",
            "",
        ]
    )
    if rejected:
        for candidate in rejected[:75]:
            lines.append(
                "- "
                f"{candidate.ticker} | {candidate.reject_reason} | "
                f"tags: {join_preserving_order(sorted(candidate.detector_tags)) or 'None'}"
            )
    else:
        lines.append("- None")

    if detector_failures:
        lines.extend(["", "## Detector Data Failures", ""])
        for ticker, failure in sorted(detector_failures.items()):
            lines.append(f"- {ticker}: {failure}")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `chart_needed = true` means the ticker should be considered for visual chart review.",
            "- `reject_reason` is limited to obvious issues such as no meaningful setup tags, major failed breakout behavior, severe extension without a fresh catalyst, or no actionable trigger cluster.",
            "- `interest_rank` is not a strict setup score and should not be used as a standalone rejection rule.",
        ]
    )
    return "\n".join(lines) + "\n"


def save_detector_outputs(
    candidates: list[DetectorCandidate],
    output_dir: str = "reports",
    primary_gated_count: Optional[int] = None,
    detector_failures: Optional[dict[str, str]] = None,
    include_json: bool = True,
) -> dict[str, str]:
    """Save detector CSV, Markdown report, and optional JSON output."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now()
    timestamp = generated_at.strftime("%Y-%m-%d_%H%M")
    primary_count = len(candidates) if primary_gated_count is None else primary_gated_count

    csv_path = output_path / f"detector_candidates_{timestamp}.csv"
    report_path = output_path / f"detector_report_{timestamp}.md"
    json_path = output_path / f"detector_candidates_{timestamp}.json"

    detector_candidates_to_dataframe(candidates).to_csv(csv_path, index=False)
    report_path.write_text(
        build_detector_report_markdown(
            candidates,
            primary_gated_count=primary_count,
            detector_failures=detector_failures,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    paths = {
        "csv": str(csv_path),
        "report": str(report_path),
    }
    if include_json:
        payload = {
            "generated_at": generated_at.isoformat(timespec="minutes"),
            "primary_gated_count": primary_count,
            "detector_failures": detector_failures or {},
            "candidates": [candidate.to_json_dict() for candidate in candidates],
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        paths["json"] = str(json_path)

    return paths

"""
Daily scanner email job.

Default behavior:
1. Run the default chart-AI scanner path.
2. Find the generated Markdown report.
3. Email a compact run summary with the full report attached.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from email_report import send_email, send_report_email


REPORT_PATH_RE = re.compile(
    r"(?:Final output path|Final output location|Final report location|Report path|Saved report):\s*"
    r"(?P<path>.+(?:haiku_chart_triage_report|premarket_report)_[^\s]+\.md)"
)
COUNT_PATTERNS = {
    "raw_universe_count": re.compile(r"Raw universe count:\s*(?P<value>\d+)"),
    "scanned_count": re.compile(r"(?:Symbols scanned from universe|Scanned):\s*(?P<value>\d+)"),
    "hard_gate_survivors": re.compile(r"Hard gate survivors:\s*(?P<value>\d+)"),
    "hard_gate_rejected": re.compile(r"Hard gate rejected:\s*(?P<value>\d+)"),
    "detector_annotations_evaluated": re.compile(
        r"Detector annotations evaluated:\s*(?P<value>\d+)"
    ),
    "detector_hits": re.compile(r"Detector hits:\s*(?P<value>\d+)"),
    "detector_chart_needed_hints": re.compile(
        r"Detector chart-needed hints:\s*(?P<value>\d+)"
    ),
    "qualified_after_focus_gates": re.compile(
        r"Qualified after focus gates:\s*(?P<value>\d+)"
    ),
    "selected_for_claude": re.compile(r"Selected for Claude:\s*(?P<value>\d+)"),
    "claude_calls": re.compile(r"(?:Full Claude calls|Claude calls):\s*(?P<value>\d+)"),
    "total_runtime": re.compile(r"Total runtime:\s*(?P<value>.+)"),
}


@dataclass(frozen=True)
class ScannerRun:
    command: list[str]
    returncode: int
    output: str
    log_path: Path
    report_path: Optional[Path]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_int(value: Optional[str], name: str) -> Optional[int]:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive when provided.")
    return parsed


def _build_scanner_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "main.py",
        "--chart-ai-scan",
        "--universe",
        args.universe,
        "--output-dir",
        args.output_dir,
    ]

    max_universe_size = _optional_int(args.max_universe_size, "max universe size")
    if max_universe_size is not None:
        command.extend(["--max-universe-size", str(max_universe_size)])

    if args.scanner_dry_run:
        command.append("--dry-run")
    if args.haiku_triage_limit is not None:
        command.extend(
            [
                "--haiku-triage-limit",
                str(_optional_int(args.haiku_triage_limit, "Haiku triage limit")),
            ]
        )
    if args.haiku_workers is not None:
        command.extend(
            ["--haiku-workers", str(_optional_int(args.haiku_workers, "Haiku workers"))]
        )
    if args.skip_haiku_cache:
        command.append("--skip-haiku-cache")
    return command


def _extract_counts(output: str) -> dict[str, str]:
    counts: dict[str, str] = {}
    for line in output.splitlines():
        for key, pattern in COUNT_PATTERNS.items():
            match = pattern.search(line)
            if match:
                counts[key] = match.group("value").strip()
    return counts


def _extract_report_path(output: str, repo_root: Path) -> Optional[Path]:
    report_path: Optional[Path] = None
    for match in REPORT_PATH_RE.finditer(output):
        path_text = match.group("path").strip()
        candidate = Path(path_text)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        report_path = candidate
    return report_path


def _latest_report(output_dir: Path, start_time: datetime) -> Optional[Path]:
    if not output_dir.exists():
        return None
    reports = [
        path
        for pattern in ("haiku_chart_triage_report_*.md", "premarket_report_*.md")
        for path in output_dir.glob(pattern)
        if datetime.fromtimestamp(path.stat().st_mtime) >= start_time
    ]
    if not reports:
        return None
    return max(reports, key=lambda path: path.stat().st_mtime)


def run_scanner(args: argparse.Namespace) -> ScannerRun:
    repo_root = _repo_root()
    output_dir = repo_root / args.output_dir
    log_dir = output_dir / "email_job_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now()
    log_path = log_dir / f"daily_report_email_{started_at.strftime('%Y-%m-%d_%H%M%S')}.log"
    command = _build_scanner_command(args)

    print("Running scanner command:", " ".join(command), flush=True)
    collected_lines: list[str] = []
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("Command: " + " ".join(command) + "\n\n")
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log_file.write(line)
            collected_lines.append(line)
        returncode = process.wait()
        log_file.write(f"\nExit code: {returncode}\n")

    output = "".join(collected_lines)
    report_path = _extract_report_path(output, repo_root)
    if report_path is None or not report_path.exists():
        report_path = _latest_report(output_dir, started_at)

    return ScannerRun(
        command=command,
        returncode=returncode,
        output=output,
        log_path=log_path,
        report_path=report_path,
    )


def _report_summary_block(report_path: Path, max_chars: int = 6000) -> str:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    start = text.find("## Summary")
    if start == -1:
        return text[:max_chars].strip()

    end_markers = ["## Market Context", "## Sector Leadership", "## Ticker Reports"]
    end_positions = [text.find(marker, start + 1) for marker in end_markers]
    end_positions = [position for position in end_positions if position != -1]
    end = min(end_positions) if end_positions else min(len(text), start + max_chars)
    summary = text[start:end].strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "\n\n[Summary truncated]"
    return summary


def _tail(text: str, max_lines: int = 80) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def build_success_email(run: Optional[ScannerRun], report_path: Path) -> tuple[str, str]:
    date_text = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = os.getenv("REPORT_EMAIL_SUBJECT_PREFIX", "Daily Chart AI Outlook")
    subject = f"{subject_prefix} - {date_text}"
    counts = _extract_counts(run.output if run else "")

    lines = [
        "Status: SUCCESS",
        f"Report: {report_path.name}",
    ]
    if run:
        lines.extend(
            [
                f"Command: {' '.join(run.command)}",
                f"Log: {run.log_path}",
            ]
        )
    if counts:
        lines.extend(
            [
                "",
                "Run counts:",
                f"- Raw universe: {counts.get('raw_universe_count', 'N/A')}",
                f"- Symbols scanned: {counts.get('scanned_count', 'N/A')}",
                f"- Hard gate survivors: {counts.get('hard_gate_survivors', 'N/A')}",
                f"- Hard gate rejected: {counts.get('hard_gate_rejected', 'N/A')}",
                "- Detector annotations evaluated: "
                f"{counts.get('detector_annotations_evaluated', 'N/A')}",
                f"- Detector hits: {counts.get('detector_hits', 'N/A')}",
                "- Detector chart-needed hints: "
                f"{counts.get('detector_chart_needed_hints', 'N/A')}",
                f"- Runtime: {counts.get('total_runtime', 'N/A')}",
            ]
        )

    lines.extend(["", "Report summary:", "", _report_summary_block(report_path)])
    lines.append("")
    lines.append("Full chart-AI Markdown report is attached.")
    return subject, "\n".join(lines)


def build_failure_email(run: ScannerRun) -> tuple[str, str]:
    date_text = datetime.now().strftime("%Y-%m-%d")
    subject_prefix = os.getenv("REPORT_EMAIL_SUBJECT_PREFIX", "Daily Chart AI Outlook")
    subject = f"{subject_prefix} FAILED - {date_text}"
    body = "\n".join(
        [
            "Status: FAILURE",
            f"Exit code: {run.returncode}",
            f"Command: {' '.join(run.command)}",
            f"Log: {run.log_path}",
            "",
            "Log tail:",
            _tail(run.output),
        ]
    )
    return subject, body


def send_or_preview(
    subject: str,
    body: str,
    attachments: list[Path],
    dry_run_email: bool,
) -> None:
    if dry_run_email:
        print("\n--- EMAIL PREVIEW ---")
        print(f"Subject: {subject}")
        print("")
        print(body)
        print("")
        print("Attachments:")
        for attachment in attachments:
            print(f"- {attachment}")
        print("--- END EMAIL PREVIEW ---")
        return

    if attachments:
        send_report_email(
            report_path=attachments[0],
            subject=subject,
            body=body,
            extra_attachments=attachments[1:],
        )
    else:
        send_email(subject=subject, body=body)


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run the trading scanner and email the generated report."
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Send an existing Markdown report instead of running the scanner.",
    )
    parser.add_argument(
        "--dry-run-email",
        action="store_true",
        help="Print the email preview instead of sending through SMTP.",
    )
    parser.add_argument(
        "--scanner-dry-run",
        action="store_true",
        help="Pass --dry-run to main.py so Claude is not called.",
    )
    parser.add_argument(
        "--universe",
        choices=["sp500", "us_listed"],
        default=os.getenv("DAILY_REPORT_UNIVERSE", "us_listed"),
        help="Universe passed to main.py.",
    )
    parser.add_argument(
        "--max-universe-size",
        default=os.getenv("DAILY_REPORT_MAX_UNIVERSE_SIZE"),
        help="Optional universe size cap passed to main.py.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("DAILY_REPORT_OUTPUT_DIR", "reports"),
        help="Report output directory passed to main.py.",
    )
    parser.add_argument(
        "--haiku-triage-limit",
        default=os.getenv("DAILY_REPORT_HAIKU_TRIAGE_LIMIT"),
        help="Optional Haiku chart review cap passed to main.py.",
    )
    parser.add_argument(
        "--haiku-workers",
        default=os.getenv("DAILY_REPORT_HAIKU_WORKERS"),
        help="Optional Haiku worker count passed to main.py.",
    )
    parser.add_argument(
        "--skip-haiku-cache",
        action="store_true",
        default=_env_bool("DAILY_REPORT_SKIP_HAIKU_CACHE", False),
        help="Pass --skip-haiku-cache to main.py.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.report_path:
        report_path = Path(args.report_path).expanduser()
        if not report_path.is_absolute():
            report_path = _repo_root() / report_path
        if not report_path.exists():
            raise FileNotFoundError(f"Report not found: {report_path}")
        subject, body = build_success_email(None, report_path)
        send_or_preview(subject, body, [report_path], args.dry_run_email)
        return

    run = run_scanner(args)
    if run.returncode != 0:
        subject, body = build_failure_email(run)
        send_or_preview(subject, body, [run.log_path], args.dry_run_email)
        raise SystemExit(run.returncode)

    if run.report_path is None or not run.report_path.exists():
        subject, body = build_failure_email(
            ScannerRun(
                command=run.command,
                returncode=1,
                output=run.output + "\nNo generated chart-AI Markdown report was found.",
                log_path=run.log_path,
                report_path=None,
            )
        )
        send_or_preview(subject, body, [run.log_path], args.dry_run_email)
        raise SystemExit(1)

    subject, body = build_success_email(run, run.report_path)
    send_or_preview(subject, body, [run.report_path], args.dry_run_email)


if __name__ == "__main__":
    main()

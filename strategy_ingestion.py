"""
One-time strategy source ingestion for chart triage prompts.

This does not train Claude permanently. It extracts local strategy documents,
optionally asks Claude once to compress them into a durable project prompt, and
writes that prompt to prompts/STRATEGY_MASTER.md for later cached triage calls.
"""

from __future__ import annotations

import argparse
import os
import re
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from claude_analyzer import DEFAULT_CLAUDE_MODEL, load_anthropic_client


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "strategy_sources"
DEFAULT_PROMPT_OUTPUT = PROJECT_ROOT / "prompts" / "STRATEGY_MASTER.md"
DEFAULT_RAW_OUTPUT = DEFAULT_SOURCE_DIR / "blueprint_extracted.txt"
DEFAULT_INGEST_MODEL = os.getenv("ANTHROPIC_STRATEGY_INGEST_MODEL", DEFAULT_CLAUDE_MODEL)
DEFAULT_CHUNK_CHARS = 45_000


def _clean_text(value: str) -> str:
    """Normalize extracted document text without changing meaning."""
    text = value.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _to_ascii_prompt(value: str) -> str:
    """Normalize generated prompts to repository-safe ASCII text."""
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2192": "->",
        "\u2248": "~",
        "\u2713": "-",
        "\u2717": "-",
        "\u26a0": "-",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00a0": " ",
    }
    text = value
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)

    return text.encode("ascii", errors="ignore").decode("ascii")


def extract_pdf_text(pdf_path: str | Path, max_pages: Optional[int] = None) -> str:
    """Extract text from a local PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "pypdf is required for PDF ingestion. Install requirements.txt first."
        ) from exc

    path = Path(pdf_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF path is not a file: {path}")

    reader = PdfReader(str(path))
    page_count = len(reader.pages)
    limit = page_count if max_pages is None else min(max_pages, page_count)
    page_text: list[str] = []

    for index in range(limit):
        text = reader.pages[index].extract_text() or ""
        if text.strip():
            page_text.append(f"\n\n--- Page {index + 1} ---\n{text.strip()}")

    extracted = _clean_text("\n".join(page_text))
    if not extracted:
        raise ValueError(
            "No text could be extracted from the PDF. The file may be image-only."
        )

    return extracted


def read_transcript_files(paths: Iterable[str | Path]) -> str:
    """Read optional transcript/source text files."""
    chunks: list[str] = []
    for source in paths:
        path = Path(source).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Transcript/source file not found: {path}")
        chunks.append(f"\n\n--- Source: {path.name} ---\n{path.read_text(encoding='utf-8')}")

    return _clean_text("\n".join(chunks))


def split_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split text into rough paragraph-preserving chunks."""
    cleaned = _clean_text(text)
    if len(cleaned) <= max_chars:
        return [cleaned]

    paragraphs = cleaned.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        paragraph_size = len(paragraph) + 2
        if current and current_size + paragraph_size > max_chars:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += paragraph_size

    if current:
        chunks.append("\n\n".join(current).strip())

    return chunks


def _extract_response_text(message: Any) -> str:
    """Extract text from an Anthropic response."""
    parts = []
    for block in getattr(message, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _call_claude(prompt: str, model: str, max_tokens: int) -> str:
    """Call Claude once for strategy ingestion."""
    client = load_anthropic_client()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    response = _extract_response_text(message)
    if not response:
        raise ValueError("Claude returned an empty ingestion response.")
    return response.strip()


def build_chunk_prompt(chunk: str, chunk_number: int, chunk_count: int) -> str:
    """Build the prompt for one source chunk summary."""
    return f"""
You are extracting reusable trading-strategy knowledge from a private local
source document for a chart-triage scanner.

Task:
- Summarize only the actionable strategy/rubric details from this source chunk.
- Focus on chart setup definitions, entry triggers, invalidation, risk/reward,
  volume/candle rules, market/sector context, and avoid/disqualifier rules.
- Do not quote long passages.
- Do not add generic trading education not supported by the source.
- Keep the output concise but specific enough for a vision model to judge charts.

Chunk {chunk_number} of {chunk_count}:
<source_chunk>
{chunk}
</source_chunk>
""".strip()


def build_final_prompt(chunk_summaries: list[str]) -> str:
    """Build the final strategy master generation prompt."""
    summaries = "\n\n".join(
        f"--- Chunk Summary {index + 1} ---\n{summary}"
        for index, summary in enumerate(chunk_summaries)
    )
    return f"""
Create the final compressed STRATEGY_MASTER.md for a Claude Haiku visual chart
triage stage using Sean / The Options Cartel Blueprint-style strategy.

Requirements:
- This is not trade advice and not a buy/sell recommendation system.
- Write a durable prompt file that can be included or prompt-cached for every
  chart triage request.
- Do not mention that the text came from chunks.
- Do not quote the source verbatim except short unavoidable labels.
- Keep it compact enough for repeated model calls.
- Make it actionable for visual chart judgment.

Include these sections:
- Core philosophy.
- Market regime.
- Scanning and liquidity.
- Setup families.
- Volume rules.
- Candle rules.
- Entries and triggers.
- Retests.
- Risk and invalidation.
- Avoid/reject conditions.
- Visual triage decision guidance for KEEP, MAYBE, and REJECT.

Source-derived summaries:
<summaries>
{summaries}
</summaries>
""".strip()


def build_deterministic_strategy_master(source_text: str) -> str:
    """Fallback prompt when Claude summarization is intentionally skipped."""
    excerpt = _clean_text(source_text)[:2500]
    return textwrap.dedent(
        f"""
        You are judging charts through a Sean / The Options Cartel Blueprint strategy lens.

        This prompt was generated from local strategy source text without a Claude
        summarization pass. Review the source excerpt below only as compressed
        context; refine with `strategy_ingestion.py --pdf ...` without `--no-claude`
        when available.

        Source excerpt:
        {excerpt}

        Core rules:
        - Prefer strong market, strong sector, strong/liquid stock.
        - Prefer compression before expansion.
        - Prefer clear triggers, clear invalidation, and good risk/reward.
        - Volume should confirm price action.
        - Reject sloppy, extended, failed, illiquid, or unclear charts.
        """
    ).strip()


def generate_strategy_master_with_claude(
    source_text: str,
    model: str = DEFAULT_INGEST_MODEL,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> str:
    """Use Claude once per chunk plus one merge call to create STRATEGY_MASTER."""
    chunks = split_text(source_text, max_chars=chunk_chars)
    print(f"Strategy source chunks: {len(chunks)}", flush=True)

    summaries: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"Summarizing strategy chunk {index}/{len(chunks)}...", flush=True)
        summary = _call_claude(
            build_chunk_prompt(chunk, index, len(chunks)),
            model=model,
            max_tokens=2500,
        )
        summaries.append(summary)

    print("Building final STRATEGY_MASTER.md...", flush=True)
    strategy_master = _call_claude(
        build_final_prompt(summaries),
        model=model,
        max_tokens=4500,
    )
    return strategy_master.strip()


def ingest_strategy_sources(
    pdf_path: Optional[str] = None,
    transcript_paths: Optional[list[str]] = None,
    output_path: str | Path = DEFAULT_PROMPT_OUTPUT,
    raw_output_path: str | Path = DEFAULT_RAW_OUTPUT,
    model: str = DEFAULT_INGEST_MODEL,
    no_claude: bool = False,
    max_pages: Optional[int] = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, Any]:
    """Extract strategy sources and write a durable STRATEGY_MASTER prompt."""
    start = time.perf_counter()
    transcript_paths = transcript_paths or []
    source_parts: list[str] = []

    if pdf_path:
        print(f"Extracting PDF strategy source: {pdf_path}", flush=True)
        source_parts.append(extract_pdf_text(pdf_path, max_pages=max_pages))

    if transcript_paths:
        print(f"Reading transcript/source files: {len(transcript_paths)}", flush=True)
        source_parts.append(read_transcript_files(transcript_paths))

    if not source_parts:
        raise ValueError("Provide at least one --pdf or --transcript source.")

    source_text = _clean_text("\n\n".join(source_parts))
    raw_path = Path(raw_output_path).expanduser()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(source_text, encoding="utf-8")
    print(f"Extracted strategy text saved: {raw_path}", flush=True)

    if no_claude:
        strategy_master = build_deterministic_strategy_master(source_text)
        ingestion_mode = "local_extraction_only"
    else:
        strategy_master = generate_strategy_master_with_claude(
            source_text,
            model=model,
            chunk_chars=chunk_chars,
        )
        ingestion_mode = "claude_summarized"

    generated_header = (
        f"<!-- Generated by strategy_ingestion.py on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | mode={ingestion_mode} | "
        f"model={model if not no_claude else 'none'} -->\n\n"
    )
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        _to_ascii_prompt(generated_header + strategy_master.strip()) + "\n",
        encoding="utf-8",
    )
    print(f"Strategy master prompt saved: {output}", flush=True)

    elapsed = time.perf_counter() - start
    return {
        "output_path": str(output),
        "raw_output_path": str(raw_path),
        "source_chars": len(source_text),
        "ingestion_mode": ingestion_mode,
        "model": model if not no_claude else None,
        "elapsed_seconds": round(elapsed, 3),
    }


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="One-time PDF/transcript ingestion for STRATEGY_MASTER.md."
    )
    parser.add_argument("--pdf", default=None, help="Local Blueprint PDF path.")
    parser.add_argument(
        "--transcript",
        action="append",
        default=[],
        help="Optional transcript/source text file. Can be passed multiple times.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_PROMPT_OUTPUT),
        help="Prompt output path. Defaults to prompts/STRATEGY_MASTER.md.",
    )
    parser.add_argument(
        "--raw-output",
        default=str(DEFAULT_RAW_OUTPUT),
        help="Extracted raw text output path. Defaults to strategy_sources/blueprint_extracted.txt.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_INGEST_MODEL,
        help="Claude model for one-time summarization.",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Only extract text and write a basic local prompt; do not call Claude.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional PDF page cap for testing extraction.",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=DEFAULT_CHUNK_CHARS,
        help="Approximate source characters per Claude summarization chunk.",
    )
    return parser.parse_args()


def main() -> None:
    """Run one-time strategy ingestion."""
    args = parse_args()
    result = ingest_strategy_sources(
        pdf_path=args.pdf,
        transcript_paths=args.transcript,
        output_path=args.output,
        raw_output_path=args.raw_output,
        model=args.model,
        no_claude=args.no_claude,
        max_pages=args.max_pages,
        chunk_chars=args.chunk_chars,
    )
    print("Strategy ingestion complete:", flush=True)
    for key, value in result.items():
        print(f"- {key}: {value}", flush=True)


if __name__ == "__main__":
    main()

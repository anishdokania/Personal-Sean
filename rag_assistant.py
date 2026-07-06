"""Small, local RAG assistant over this repository.

Indexes the project's own docs and source (Markdown, Python, text), retrieves
the most relevant chunks with a fully local TF-IDF index (no embedding API),
and asks Claude to answer grounded in those chunks with citations.

Usage:
    python rag_assistant.py index                 # build/refresh the local index
    python rag_assistant.py ask "your question"   # one-shot question
    python rag_assistant.py chat                  # interactive session

The index lives in ``.rag_index/`` (git-ignored). Only answer generation
touches the network; retrieval is entirely local.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import anthropic
except ImportError:  # pragma: no cover - anthropic is a project dependency
    anthropic = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a project dependency
    load_dotenv = None


REPO_ROOT = Path(__file__).resolve().parent
INDEX_DIR = REPO_ROOT / ".rag_index"
CHUNKS_PATH = INDEX_DIR / "chunks.json"
MATRIX_PATH = INDEX_DIR / "tfidf.npz"

DEFAULT_MODEL = os.getenv("RAG_MODEL", "claude-sonnet-5")

# What to index and what to leave out.
INCLUDE_SUFFIXES = {".md", ".py", ".txt"}
EXCLUDE_DIRS = {
    ".git",
    ".rag_index",
    "__pycache__",
    ".venv",
    "venv",
    "reports",
    "charts",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
# Files under strategy_sources/ are user-private (git-ignored) except the
# README; only index what is safe and tracked.
EXCLUDE_FILES = {".env", ".env.example"}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
TOP_K = 6

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


# --------------------------------------------------------------------------- #
# Document discovery and chunking
# --------------------------------------------------------------------------- #
@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str


def _is_excluded(path: Path) -> bool:
    parts = set(path.relative_to(REPO_ROOT).parts)
    if parts & EXCLUDE_DIRS:
        return True
    if path.name in EXCLUDE_FILES:
        return True
    # strategy_sources/ is git-ignored except README.md
    rel = path.relative_to(REPO_ROOT)
    if rel.parts and rel.parts[0] == "strategy_sources" and path.name != "README.md":
        return True
    return False


def discover_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in INCLUDE_SUFFIXES:
            continue
        if _is_excluded(path):
            continue
        files.append(path)
    return sorted(files)


def chunk_file(path: Path) -> Iterable[Chunk]:
    """Split a file into overlapping, line-aligned chunks."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    lines = text.splitlines(keepends=True)
    rel = str(path.relative_to(REPO_ROOT))

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    start_line = 1
    line_no = 0

    for line_no, line in enumerate(lines, start=1):
        buf.append(line)
        buf_len += len(line)
        if buf_len >= CHUNK_CHARS:
            chunk_text = "".join(buf).strip()
            if chunk_text:
                chunks.append(Chunk(rel, start_line, line_no, chunk_text))
            # Build overlap tail for the next chunk.
            overlap: list[str] = []
            overlap_len = 0
            for prev in reversed(buf):
                if overlap_len >= CHUNK_OVERLAP:
                    break
                overlap.insert(0, prev)
                overlap_len += len(prev)
            buf = overlap
            buf_len = overlap_len
            start_line = max(1, line_no - len(overlap) + 1)

    tail = "".join(buf).strip()
    if tail:
        chunks.append(Chunk(rel, start_line, max(line_no, start_line), tail))
    return chunks


# --------------------------------------------------------------------------- #
# Local TF-IDF index
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_index() -> None:
    files = discover_files()
    if not files:
        print("No indexable files found.", file=sys.stderr)
        sys.exit(1)

    chunks: list[Chunk] = []
    for path in files:
        chunks.extend(chunk_file(path))

    if not chunks:
        print("No content to index.", file=sys.stderr)
        sys.exit(1)

    # Build vocabulary and document frequencies.
    tokenized = [tokenize(c.text) for c in chunks]
    df: dict[str, int] = {}
    for tokens in tokenized:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1

    vocab = {token: i for i, token in enumerate(sorted(df))}
    n_docs = len(chunks)
    idf = np.zeros(len(vocab), dtype=np.float32)
    for token, i in vocab.items():
        idf[i] = math.log((1 + n_docs) / (1 + df[token])) + 1.0

    matrix = np.zeros((n_docs, len(vocab)), dtype=np.float32)
    for row, tokens in enumerate(tokenized):
        if not tokens:
            continue
        counts: dict[int, int] = {}
        for token in tokens:
            counts[vocab[token]] = counts.get(vocab[token], 0) + 1
        max_count = max(counts.values())
        for col, count in counts.items():
            matrix[row, col] = (count / max_count) * idf[col]

    # L2-normalize rows so retrieval is a plain dot product.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms

    INDEX_DIR.mkdir(exist_ok=True)
    np.savez_compressed(MATRIX_PATH, matrix=matrix, idf=idf)
    payload = {
        "vocab": vocab,
        "chunks": [
            {
                "path": c.path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "text": c.text,
            }
            for c in chunks
        ],
    }
    CHUNKS_PATH.write_text(json.dumps(payload), encoding="utf-8")

    print(
        f"Indexed {n_docs} chunks from {len(files)} files "
        f"({len(vocab)} terms) -> {INDEX_DIR.relative_to(REPO_ROOT)}/"
    )


def load_index() -> tuple[np.ndarray, np.ndarray, dict[str, int], list[Chunk]]:
    if not CHUNKS_PATH.exists() or not MATRIX_PATH.exists():
        print(
            "No index found. Run `python rag_assistant.py index` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    data = np.load(MATRIX_PATH)
    matrix = data["matrix"]
    idf = data["idf"]
    payload = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    vocab = payload["vocab"]
    chunks = [Chunk(**c) for c in payload["chunks"]]
    return matrix, idf, vocab, chunks


def embed_query(query: str, idf: np.ndarray, vocab: dict[str, int]) -> np.ndarray:
    vec = np.zeros(len(vocab), dtype=np.float32)
    counts: dict[int, int] = {}
    for token in tokenize(query):
        col = vocab.get(token)
        if col is not None:
            counts[col] = counts.get(col, 0) + 1
    if not counts:
        return vec
    max_count = max(counts.values())
    for col, count in counts.items():
        vec[col] = (count / max_count) * idf[col]
    norm = np.linalg.norm(vec)
    if norm:
        vec /= norm
    return vec


def retrieve(query: str, top_k: int = TOP_K) -> list[tuple[Chunk, float]]:
    matrix, idf, vocab, chunks = load_index()
    qvec = embed_query(query, idf, vocab)
    if not qvec.any():
        return []
    scores = matrix @ qvec
    order = np.argsort(scores)[::-1][:top_k]
    return [(chunks[i], float(scores[i])) for i in order if scores[i] > 0]


# --------------------------------------------------------------------------- #
# Answer generation
# --------------------------------------------------------------------------- #
def load_client() -> "anthropic.Anthropic":
    if anthropic is None:
        print("The `anthropic` package is required. pip install anthropic", file=sys.stderr)
        sys.exit(1)
    if load_dotenv is not None:
        load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set (see .env.example).", file=sys.stderr)
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key.strip())


def build_prompt(question: str, hits: list[tuple[Chunk, float]]) -> str:
    context_blocks = []
    for i, (chunk, _score) in enumerate(hits, start=1):
        location = f"{chunk.path}:{chunk.start_line}-{chunk.end_line}"
        context_blocks.append(f"[{i}] {location}\n{chunk.text}")
    context = "\n\n".join(context_blocks)
    return f"""You are a helpful assistant answering questions about this codebase.
Use only the context below. If the answer is not in the context, say so plainly.
Cite the sources you use with their bracket numbers, e.g. [1], [3].

Context:
{context}

Question: {question}

Answer:"""


def answer(question: str, top_k: int = TOP_K, model: str = DEFAULT_MODEL) -> None:
    hits = retrieve(question, top_k=top_k)
    if not hits:
        print("No relevant content found in the index for that question.")
        return

    client = load_client()
    prompt = build_prompt(question, hits)
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    print(text.strip())
    print("\nSources:")
    for i, (chunk, score) in enumerate(hits, start=1):
        print(f"  [{i}] {chunk.path}:{chunk.start_line}-{chunk.end_line}  (score {score:.3f})")


def chat(top_k: int = TOP_K, model: str = DEFAULT_MODEL) -> None:
    print("Local RAG assistant. Ask about this repo. Ctrl-C or 'exit' to quit.\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break
        answer(question, top_k=top_k, model=model)
        print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Local RAG assistant over this repository.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="Build or refresh the local index.")

    ask = sub.add_parser("ask", help="Ask a single question.")
    ask.add_argument("question", nargs="+", help="The question to ask.")
    ask.add_argument("-k", "--top-k", type=int, default=TOP_K, help="Chunks to retrieve.")
    ask.add_argument("-m", "--model", default=DEFAULT_MODEL, help="Claude model to use.")

    chat_cmd = sub.add_parser("chat", help="Interactive question/answer session.")
    chat_cmd.add_argument("-k", "--top-k", type=int, default=TOP_K, help="Chunks to retrieve.")
    chat_cmd.add_argument("-m", "--model", default=DEFAULT_MODEL, help="Claude model to use.")

    args = parser.parse_args()

    if args.command == "index":
        build_index()
    elif args.command == "ask":
        answer(" ".join(args.question), top_k=args.top_k, model=args.model)
    elif args.command == "chat":
        chat(top_k=args.top_k, model=args.model)


if __name__ == "__main__":
    main()

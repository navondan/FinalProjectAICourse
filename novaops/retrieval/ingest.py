"""Markdown front-matter parsing and deterministic document chunking."""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    source: str
    title: str
    text: str
    audience: str | None
    metadata: dict[str, Any]
    chunk_index: int


def parse_frontmatter(text: str, *, source: str = "<document>") -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    closing_line = next(
        (index for index in range(1, len(lines)) if lines[index].strip() in {"---", "..."}),
        None,
    )
    if closing_line is None:
        raise ValueError(f"{source}: front matter is missing its closing delimiter")

    raw_metadata = "\n".join(lines[1:closing_line])
    try:
        metadata = yaml.safe_load(raw_metadata) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: invalid YAML front matter") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"{source}: front matter must be a YAML mapping")

    return _json_compatible(metadata), "\n".join(lines[closing_line + 1 :]).strip()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def chunk_text(text: str, *, chunk_words: int, overlap_words: int) -> list[str]:
    if chunk_words <= 0:
        raise ValueError("chunk_words must be positive")
    if overlap_words < 0 or overlap_words >= chunk_words:
        raise ValueError("overlap_words must be non-negative and smaller than chunk_words")

    words = text.split()
    if not words:
        return []

    step = chunk_words - overlap_words
    return [
        " ".join(words[start : start + chunk_words])
        for start in range(0, len(words), step)
        if words[start : start + chunk_words]
    ]


def load_document_chunks(
    documents_dir: Path, *, chunk_words: int, overlap_words: int
) -> list[DocumentChunk]:
    if not documents_dir.is_dir():
        raise FileNotFoundError(f"Document directory does not exist: {documents_dir}")

    chunks: list[DocumentChunk] = []
    for path in sorted(documents_dir.rglob("*.md")):
        raw_text = path.read_text(encoding="utf-8")
        source = path.relative_to(documents_dir.parent).as_posix()
        metadata, body = parse_frontmatter(raw_text, source=source)
        title = next(
            (line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("# ")),
            path.stem,
        )
        audience_value = metadata.get("audience")
        audience = str(audience_value).strip().lower() if audience_value is not None else None
        if audience == "":
            audience = None

        for index, content in enumerate(
            chunk_text(body, chunk_words=chunk_words, overlap_words=overlap_words)
        ):
            chunks.append(
                DocumentChunk(
                    source=source,
                    title=title,
                    text=content,
                    audience=audience,
                    metadata=metadata,
                    chunk_index=index,
                )
            )
    return chunks

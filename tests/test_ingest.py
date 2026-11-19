from pathlib import Path

import pytest

from novaops.retrieval.ingest import chunk_text, load_document_chunks, parse_frontmatter


def test_frontmatter_is_parsed_and_dates_become_json_safe() -> None:
    metadata, body = parse_frontmatter(
        "---\nlast_updated: 2026-05-06\naudience: manager\n---\n# Guide\nText.",
        source="guide.md",
    )

    assert metadata == {"last_updated": "2026-05-06", "audience": "manager"}
    assert body == "# Guide\nText."


def test_frontmatter_rejects_malformed_documents() -> None:
    with pytest.raises(ValueError, match="closing delimiter"):
        parse_frontmatter("---\naudience: manager\n# No end marker", source="bad.md")

    with pytest.raises(ValueError, match="YAML mapping"):
        parse_frontmatter("---\n- a\n- b\n---\nbody", source="bad.md")


def test_chunking_overlaps_without_losing_words() -> None:
    chunks = chunk_text(
        " ".join(f"w{index}" for index in range(10)),
        chunk_words=6,
        overlap_words=2,
    )

    assert chunks == [
        "w0 w1 w2 w3 w4 w5",
        "w4 w5 w6 w7 w8 w9",
        "w8 w9",
    ]


def test_all_supplied_documents_are_loaded_with_audience_metadata() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    chunks = load_document_chunks(
        repository_root / "novaops-enterprise-agent-dataset" / "documents",
        chunk_words=320,
        overlap_words=80,
    )

    sources = {chunk.source for chunk in chunks}
    assert len(sources) == 102
    assert any(chunk.source.endswith("manager_playbook/giving-feedback.md") for chunk in chunks)
    assert all(
        chunk.audience == "manager"
        for chunk in chunks
        if "manager_playbook/" in chunk.source
    )

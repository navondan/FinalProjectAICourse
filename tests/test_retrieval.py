from pathlib import Path

from novaops.retrieval.filters import audience_is_allowed
from novaops.retrieval.ingest import DocumentChunk
from novaops.retrieval.store import FaissDocumentStore


class FakeEmbedder:
    model_id = "test-embedder"

    def embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [
            float(lowered.count("laptop")),
            float(lowered.count("manager")),
            float(lowered.count("webex")),
            1.0,
        ]


def _chunk(source: str, text: str, audience: str | None = None) -> DocumentChunk:
    return DocumentChunk(
        source=source,
        title=source.rsplit("/", 1)[-1],
        text=text,
        audience=audience,
        metadata={},
        chunk_index=0,
    )


def test_audience_filter_allows_unrestricted_and_fails_closed() -> None:
    assert audience_is_allowed(None, is_manager=False)
    assert audience_is_allowed("all", is_manager=False)
    assert audience_is_allowed("manager", is_manager=True)
    assert not audience_is_allowed("manager", is_manager=False)
    assert not audience_is_allowed("unknown", is_manager=True)


def test_search_filters_manager_documents_before_returning_results() -> None:
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            _chunk(
                "documents/handbook/managing-work-devices.md",
                "Company laptop policy for employees.",
                "all",
            ),
            _chunk(
                "documents/manager_playbook/giving-feedback.md",
                "Manager feedback playbook and coaching guidance.",
                "manager",
            ),
            _chunk(
                "documents/policies/remote_work_policy.md",
                "Remote laptop and equipment policy.",
            ),
        ],
        embedder,
    )

    employee_results = store.search("manager laptop policy", embedder, limit=3)
    manager_results = store.search(
        "manager laptop policy", embedder, is_manager=True, limit=3
    )

    assert all("manager_playbook/" not in result.citation for result in employee_results)
    assert any("manager_playbook/" in result.citation for result in manager_results)


def test_faiss_index_round_trips_metadata_and_checks_model_id(tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [_chunk("documents/it_kb/laptop.md", "Laptop provisioning and inventory.")],
        embedder,
    )
    store.save(tmp_path / "index")

    loaded = FaissDocumentStore.load(
        tmp_path / "index", embedding_model_id=embedder.model_id
    )
    results = loaded.search("laptop", embedder)

    assert results[0].citation == "documents/it_kb/laptop.md"
    assert results[0].chunk.text == "Laptop provisioning and inventory."

    try:
        FaissDocumentStore.load(tmp_path / "index", embedding_model_id="different-model")
    except ValueError as exc:
        assert "rebuild the index" in str(exc)
    else:
        raise AssertionError("Mismatched embedding model should be rejected")

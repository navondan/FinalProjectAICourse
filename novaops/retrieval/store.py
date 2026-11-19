"""Local FAISS vector store with BM25 and reciprocal-rank fusion."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import faiss
import numpy as np

from novaops.retrieval.filters import audience_is_allowed
from novaops.retrieval.ingest import DocumentChunk

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    model_id: str

    def embed(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunk: DocumentChunk
    score: float

    @property
    def citation(self) -> str:
        return self.chunk.source


class FaissDocumentStore:
    def __init__(
        self,
        chunks: list[DocumentChunk],
        index: faiss.Index,
        *,
        embedding_model_id: str,
    ) -> None:
        if not chunks:
            raise ValueError("The document index cannot be empty")
        if index.ntotal != len(chunks):
            raise ValueError("FAISS index row count does not match document metadata")
        self.chunks = chunks
        self.index = index
        self.embedding_model_id = embedding_model_id
        self._tokens = [_tokenize(chunk.text) for chunk in chunks]

    @classmethod
    def build(cls, chunks: list[DocumentChunk], embedder: Embedder) -> FaissDocumentStore:
        if not chunks:
            raise ValueError("No document chunks were supplied for indexing")

        vectors = np.asarray([embedder.embed(chunk.text) for chunk in chunks], dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(chunks) or vectors.shape[1] == 0:
            raise ValueError("Embedder returned vectors with an invalid shape")
        if not np.isfinite(vectors).all():
            raise ValueError("Embedder returned a non-finite vector")

        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        return cls(chunks, index, embedding_model_id=embedder.model_id)

    @classmethod
    def load(cls, index_dir: Path, *, embedding_model_id: str) -> FaissDocumentStore:
        metadata_path = index_dir / "chunks.json"
        index_path = index_dir / "documents.faiss"
        if not metadata_path.is_file() or not index_path.is_file():
            raise FileNotFoundError(
                f"No document index found at {index_dir}; run `python -m novaops.cli ingest` first"
            )

        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        stored_model_id = payload.get("embedding_model_id")
        if stored_model_id != embedding_model_id:
            raise ValueError(
                "The saved index uses embedding model "
                f"{stored_model_id!r}, but the current configuration uses {embedding_model_id!r}; "
                "rebuild the index"
            )
        chunks = [DocumentChunk(**row) for row in payload["chunks"]]
        index = faiss.read_index(str(index_path))
        return cls(chunks, index, embedding_model_id=stored_model_id)

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        index_path = index_dir / "documents.faiss"
        metadata_path = index_dir / "chunks.json"
        temporary_index = index_dir / "documents.faiss.tmp"
        temporary_metadata = index_dir / "chunks.json.tmp"

        faiss.write_index(self.index, str(temporary_index))
        temporary_index.replace(index_path)
        payload = {
            "embedding_model_id": self.embedding_model_id,
            "chunks": [
                {
                    "source": chunk.source,
                    "title": chunk.title,
                    "text": chunk.text,
                    "audience": chunk.audience,
                    "metadata": chunk.metadata,
                    "chunk_index": chunk.chunk_index,
                }
                for chunk in self.chunks
            ],
        }
        temporary_metadata.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        temporary_metadata.replace(metadata_path)

    def search(
        self,
        query: str,
        embedder: Embedder,
        *,
        is_manager: bool = False,
        limit: int = 5,
        source_prefix: str | None = None,
    ) -> list[RetrievalResult]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not query.strip():
            raise ValueError("query must not be empty")
        allowed = [
            index
            for index, chunk in enumerate(self.chunks)
            if audience_is_allowed(chunk.audience, is_manager=is_manager)
            and (source_prefix is None or chunk.source.startswith(source_prefix))
        ]
        if not allowed:
            return []

        query_vector = np.asarray([embedder.embed(query)], dtype=np.float32)
        if query_vector.shape[1] != self.index.d:
            raise ValueError("Query embedding dimension does not match the saved FAISS index")
        if not np.isfinite(query_vector).all():
            raise ValueError("Embedder returned a non-finite query vector")
        faiss.normalize_L2(query_vector)
        _, vector_indices = self.index.search(query_vector, self.index.ntotal)
        allowed_set = set(allowed)
        vector_order = [
            index for index in vector_indices[0].tolist() if index in allowed_set
        ]

        lexical_scores = _bm25_scores(
            query,
            self._tokens,
            allowed_indices=allowed,
        )
        lexical_order = sorted(
            allowed, key=lambda index: (-lexical_scores[index], index)
        )
        fused: dict[int, float] = {}
        for rank, index in enumerate(vector_order, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (60 + rank)
        for rank, index in enumerate(lexical_order, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (60 + rank)

        results: list[RetrievalResult] = []
        for index in sorted(fused, key=lambda item: (-fused[item], item))[:limit]:
            results.append(RetrievalResult(self.chunks[index], fused[index]))
        return results


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.casefold())


def _bm25_scores(
    query: str,
    corpus: list[list[str]],
    *,
    allowed_indices: list[int],
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[int, float]:
    terms = _tokenize(query)
    if not terms:
        return {index: 0.0 for index in allowed_indices}

    allowed_count = len(allowed_indices)
    average_length = (
        sum(len(corpus[index]) for index in allowed_indices) / allowed_count
        if allowed_count
        else 0.0
    )
    document_frequencies: dict[str, int] = {}
    term_frequencies: dict[int, dict[str, int]] = {}
    for index in allowed_indices:
        frequencies: dict[str, int] = {}
        for token in corpus[index]:
            frequencies[token] = frequencies.get(token, 0) + 1
        term_frequencies[index] = frequencies
        for token in frequencies:
            document_frequencies[token] = document_frequencies.get(token, 0) + 1

    scores: dict[int, float] = {}
    for index in allowed_indices:
        document_length = len(corpus[index])
        score = 0.0
        for term in terms:
            frequency = term_frequencies[index].get(term, 0)
            if not frequency:
                continue
            document_frequency = document_frequencies.get(term, 0)
            inverse_frequency = math.log(
                1.0 + (allowed_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            normalization = frequency + k1 * (
                1.0 - b + b * document_length / max(average_length, 1.0)
            )
            score += inverse_frequency * frequency * (k1 + 1.0) / normalization
        scores[index] = score
    return scores

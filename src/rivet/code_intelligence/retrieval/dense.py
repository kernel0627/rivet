from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rivet.code_intelligence.types import CodeChunk, RetrievedChunk


class EmbeddingModel(Protocol):
    @property
    def dimension(self) -> int:
        ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


@dataclass(frozen=True)
class HashEmbeddingModel:
    """Deterministic offline embedding used for tests and no-provider fallback."""

    dimension: int = 256

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in re.findall(r"[\w.]+", text.casefold(), re.UNICODE):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class InMemoryDenseIndex:
    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self.embedding_model = embedding_model
        self._chunks: dict[str, CodeChunk] = {}
        self._vectors: dict[str, tuple[float, ...]] = {}

    def upsert(self, chunks: Sequence[CodeChunk]) -> None:
        texts = [_embedding_text(chunk) for chunk in chunks]
        vectors = self.embedding_model.embed(texts)
        if len(vectors) != len(chunks):
            raise ValueError("embedding model returned the wrong number of vectors")
        for chunk, vector in zip(chunks, vectors, strict=True):
            if len(vector) != self.embedding_model.dimension:
                raise ValueError("embedding vector dimension does not match the model")
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = tuple(float(value) for value in vector)

    def delete_file(self, workspace_id: str, file_path: str) -> int:
        matches = [
            chunk_id
            for chunk_id, chunk in self._chunks.items()
            if chunk.workspace_id == workspace_id and chunk.file_path == file_path
        ]
        for chunk_id in matches:
            self._chunks.pop(chunk_id, None)
            self._vectors.pop(chunk_id, None)
        return len(matches)

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query_vector = tuple(self.embedding_model.embed([query])[0])
        scores: list[tuple[float, str]] = []
        for chunk_id, vector in self._vectors.items():
            chunk = self._chunks[chunk_id]
            if workspace_id is not None and chunk.workspace_id != workspace_id:
                continue
            score = sum(
                left * right
                for left, right in zip(query_vector, vector, strict=True)
            )
            scores.append((score, chunk_id))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [
            RetrievedChunk(
                chunk=self._chunks[chunk_id],
                score=score,
                source="dense",
                rank=rank,
                component_scores={"cosine": score},
            )
            for rank, (score, chunk_id) in enumerate(scores[:limit], start=1)
        ]


def _embedding_text(chunk: CodeChunk) -> str:
    return "\n".join(
        part
        for part in (
            chunk.file_path,
            chunk.qualified_name,
            chunk.symbol,
            chunk.kind,
            chunk.content,
        )
        if part
    )

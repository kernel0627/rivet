from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rivet.code_intelligence.types import CodeChunk, RetrievedChunk


class Retriever(Protocol):
    def search(self, query: str, *, limit: int) -> Sequence[RetrievedChunk]:
        ...


class ChunkIndex(Protocol):
    def upsert(self, chunks: Sequence[CodeChunk]) -> None:
        ...

    def delete_file(self, workspace_id: str, file_path: str) -> int:
        ...


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        limit: int,
    ) -> Sequence[RetrievedChunk]:
        ...

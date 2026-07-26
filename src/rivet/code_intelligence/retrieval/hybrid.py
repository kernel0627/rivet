from __future__ import annotations

from dataclasses import dataclass

from rivet.code_intelligence.retrieval.fusion import reciprocal_rank_fusion
from rivet.code_intelligence.retrieval.protocols import Reranker, Retriever
from rivet.code_intelligence.types import RetrievedChunk


@dataclass
class HybridRetriever:
    sparse: Retriever | None
    dense: Retriever | None
    reranker: Reranker | None = None
    candidate_limit: int = 30
    rrf_k: int = 60

    def search(self, query: str, *, limit: int) -> list[RetrievedChunk]:
        rankings: dict[str, list[RetrievedChunk]] = {}
        if self.sparse is not None:
            rankings["sparse"] = list(
                self.sparse.search(query, limit=self.candidate_limit)
            )
        if self.dense is not None:
            rankings["dense"] = list(
                self.dense.search(query, limit=self.candidate_limit)
            )
        if not rankings:
            return []
        candidates = reciprocal_rank_fusion(
            rankings,
            limit=max(limit, self.candidate_limit),
            k=self.rrf_k,
        )
        if self.reranker is None:
            return candidates[:limit]
        return list(self.reranker.rerank(query, candidates, limit=limit))


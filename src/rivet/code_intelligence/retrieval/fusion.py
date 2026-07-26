from __future__ import annotations

from collections.abc import Mapping, Sequence

from rivet.code_intelligence.types import RetrievedChunk


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[RetrievedChunk]],
    *,
    limit: int,
    k: int = 60,
) -> list[RetrievedChunk]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if k < 1:
        raise ValueError("k must be at least 1")

    by_id: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    for source, results in rankings.items():
        seen: set[str] = set()
        for rank, result in enumerate(results, start=1):
            chunk_id = result.chunk.chunk_id
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            by_id.setdefault(chunk_id, result)
            contribution = 1.0 / (k + rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
            components.setdefault(chunk_id, {})[source] = contribution

    ordered = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], chunk_id),
    )[:limit]
    return [
        RetrievedChunk(
            chunk=by_id[chunk_id].chunk,
            score=scores[chunk_id],
            source="rrf",
            rank=rank,
            component_scores=components[chunk_id],
        )
        for rank, chunk_id in enumerate(ordered, start=1)
    ]


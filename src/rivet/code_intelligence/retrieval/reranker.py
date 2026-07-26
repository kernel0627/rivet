from __future__ import annotations

import re
from collections.abc import Sequence

from rivet.code_intelligence.types import RetrievedChunk


class LexicalReranker:
    """Deterministic fallback reranker used when no cross-encoder is configured."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        limit: int,
    ) -> list[RetrievedChunk]:
        query_tokens = _tokens(query)
        scored: list[tuple[float, RetrievedChunk]] = []
        for candidate in candidates:
            chunk = candidate.chunk
            content_tokens = _tokens(
                " ".join(
                    filter(
                        None,
                        (
                            chunk.symbol,
                            chunk.qualified_name,
                            chunk.file_path,
                            chunk.content,
                        ),
                    )
                )
            )
            overlap = len(query_tokens.intersection(content_tokens))
            coverage = overlap / max(1, len(query_tokens))
            symbol_tokens = _tokens(chunk.symbol or "")
            symbol_overlap = len(query_tokens.intersection(symbol_tokens))
            symbol_bonus = (
                0.5 * symbol_overlap / max(1, len(query_tokens))
                if chunk.symbol
                else 0.0
            )
            score = candidate.score + coverage + symbol_bonus
            scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].chunk.chunk_id))
        return [
            RetrievedChunk(
                chunk=candidate.chunk,
                score=score,
                source="lexical_reranker",
                rank=rank,
                component_scores={
                    **candidate.component_scores,
                    "candidate": candidate.score,
                    "reranker": score,
                },
            )
            for rank, (score, candidate) in enumerate(scored[:limit], start=1)
        ]


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[\w.]+", text, re.UNICODE):
        normalized = token.casefold()
        tokens.add(normalized)
        tokens.update(
            part
            for part in re.split(r"[._]+", normalized)
            if part
        )
    return tokens

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def recall_at_k(
    relevant: set[str],
    ranked: Sequence[str],
    k: int,
) -> float:
    if k < 1:
        raise ValueError("k must be at least 1")
    if not relevant:
        return 1.0
    hits = relevant.intersection(ranked[:k])
    return len(hits) / len(relevant)


def reciprocal_rank(relevant: set[str], ranked: Sequence[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(
    relevance: dict[str, float],
    ranked: Sequence[str],
    k: int,
) -> float:
    if k < 1:
        raise ValueError("k must be at least 1")
    actual = _discounted_gain([relevance.get(item, 0.0) for item in ranked[:k]])
    ideal_scores = sorted(relevance.values(), reverse=True)[:k]
    ideal = _discounted_gain(ideal_scores)
    return actual / ideal if ideal else 1.0


def _discounted_gain(scores: Sequence[float]) -> float:
    total = 0.0
    for index, score in enumerate(scores, start=1):
        if score < 0:
            raise ValueError("relevance scores cannot be negative")
        total += (2**score - 1) / math.log2(index + 1)
    return total


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float

    @classmethod
    def calculate(
        cls,
        *,
        relevant: set[str],
        ranked: Sequence[str],
        k: int,
        graded_relevance: dict[str, float] | None = None,
    ) -> RetrievalMetrics:
        grades = graded_relevance or {item: 1.0 for item in relevant}
        return cls(
            recall_at_k=recall_at_k(relevant, ranked, k),
            reciprocal_rank=reciprocal_rank(relevant, ranked),
            ndcg_at_k=ndcg_at_k(grades, ranked, k),
        )


@dataclass(frozen=True)
class TrajectoryMetrics:
    turns: int
    model_calls: int
    tool_executions: int
    tool_failures: int
    duplicate_actions: int
    permission_denials: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float

    @property
    def tool_error_rate(self) -> float:
        if not self.tool_executions:
            return 0.0
        return self.tool_failures / self.tool_executions


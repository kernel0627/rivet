from __future__ import annotations

import json
import math
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rivet.code_intelligence.indexer import WorkspaceIndexer
from rivet.code_intelligence.retrieval.dense import (
    HashEmbeddingModel,
    InMemoryDenseIndex,
)
from rivet.code_intelligence.retrieval.hybrid import HybridRetriever
from rivet.code_intelligence.retrieval.reranker import LexicalReranker
from rivet.code_intelligence.retrieval.sparse import SqliteSparseIndex
from rivet.code_intelligence.types import RetrievedChunk


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkQuery:
    query: str
    expected_paths: tuple[str, ...]


def load_benchmark_queries(path: Path) -> tuple[RetrievalBenchmarkQuery, ...]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("retrieval benchmark queries must be a non-empty JSON list")
    queries: list[RetrievalBenchmarkQuery] = []
    for ordinal, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"retrieval benchmark query {ordinal} must be an object")
        query = item.get("query")
        expected_paths = item.get("expected_paths")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"retrieval benchmark query {ordinal} has no query text")
        if (
            not isinstance(expected_paths, list)
            or not expected_paths
            or not all(isinstance(value, str) and value for value in expected_paths)
        ):
            raise ValueError(
                f"retrieval benchmark query {ordinal} needs expected_paths"
            )
        queries.append(
            RetrievalBenchmarkQuery(
                query=query,
                expected_paths=tuple(expected_paths),
            )
        )
    return tuple(queries)


def benchmark_retrieval(
    workspace: Path,
    queries: Sequence[RetrievalBenchmarkQuery],
    *,
    repeat: int = 20,
    limit: int = 5,
    embedding_dimension: int = 256,
) -> dict[str, object]:
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"benchmark workspace is not a directory: {root}")
    if not queries:
        raise ValueError("retrieval benchmark needs at least one query")
    if repeat < 1:
        raise ValueError("retrieval benchmark repeat must be positive")
    if limit < 1:
        raise ValueError("retrieval benchmark limit must be positive")
    if embedding_dimension < 8:
        raise ValueError("embedding dimension must be at least 8")

    with tempfile.TemporaryDirectory(prefix="rivet-retrieval-benchmark-") as directory:
        state_root = Path(directory)
        dense = InMemoryDenseIndex(HashEmbeddingModel(dimension=embedding_dimension))
        with SqliteSparseIndex(state_root / "index.sqlite3") as sparse:
            indexer = WorkspaceIndexer(
                workspace_root=root,
                workspace_id="benchmark-workspace",
                sparse_index=sparse,
                additional_indexes=(dense,),
            )
            tracemalloc.start()
            started = time.perf_counter()
            cold_report = indexer.refresh()
            cold_index_ms = _elapsed_ms(started)
            _current_memory, peak_memory = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            files = sparse.file_hashes("benchmark-workspace")
            python_bytes = sum((root / path).stat().st_size for path in files)
            python_lines = sum(
                len((root / path).read_bytes().splitlines()) for path in files
            )

            warm_refresh_ms: list[float] = []
            warm_report = cold_report
            for _ in range(repeat):
                started = time.perf_counter()
                warm_report = indexer.refresh()
                warm_refresh_ms.append(_elapsed_ms(started))

            hybrid = HybridRetriever(
                sparse=sparse,
                dense=dense,
                reranker=LexicalReranker(),
            )
            retrievers: dict[
                str,
                Callable[[str], Sequence[RetrievedChunk]],
            ] = {
                "sparse": lambda query: sparse.search(
                    query,
                    limit=limit,
                    workspace_id="benchmark-workspace",
                ),
                "dense": lambda query: dense.search(
                    query,
                    limit=limit,
                    workspace_id="benchmark-workspace",
                ),
                "hybrid": lambda query: hybrid.search(query, limit=limit),
            }
            retrieval = {
                name: _benchmark_retriever(
                    retrieve,
                    queries,
                    repeat=repeat,
                )
                for name, retrieve in retrievers.items()
            }
            database_bytes = (state_root / "index.sqlite3").stat().st_size
            chunk_count = sparse.count("benchmark-workspace")

    return {
        "schema_version": 1,
        "passed": not cold_report.failed_files,
        "workspace": {
            "path": str(root),
            "python_files": cold_report.scanned_files,
            "python_lines": python_lines,
            "python_bytes": python_bytes,
        },
        "configuration": {
            "repeat": repeat,
            "limit": limit,
            "embedding": "deterministic_hash",
            "embedding_dimension": embedding_dimension,
            "query_count": len(queries),
        },
        "index": {
            "cold_ms": cold_index_ms,
            "peak_memory_mb": round(peak_memory / (1024 * 1024), 3),
            "database_bytes": database_bytes,
            "chunk_count": chunk_count,
            "scanned_files": cold_report.scanned_files,
            "indexed_files": cold_report.indexed_files,
            "failed_files": [
                {"path": path, "error": error}
                for path, error in cold_report.failed_files
            ],
            "warm_refresh_ms": _timing_summary(warm_refresh_ms),
            "warm_unchanged_files": warm_report.unchanged_files,
        },
        "retrieval": retrieval,
    }


def _benchmark_retriever(
    retrieve: Callable[[str], Sequence[RetrievedChunk]],
    queries: Sequence[RetrievalBenchmarkQuery],
    *,
    repeat: int,
) -> dict[str, object]:
    timings: list[float] = []
    samples: list[dict[str, object]] = []
    hits = 0
    for query in queries:
        results = list(retrieve(query.query))
        result_paths = {result.chunk.file_path for result in results}
        hit = bool(result_paths.intersection(query.expected_paths))
        hits += int(hit)
        samples.append(
            {
                "query": query.query,
                "expected_paths": list(query.expected_paths),
                "hit": hit,
                "top_results": [
                    {
                        "path": result.chunk.file_path,
                        "symbol": result.chunk.qualified_name or result.chunk.symbol,
                        "rank": result.rank,
                    }
                    for result in results
                ],
            }
        )
        for _ in range(repeat):
            started = time.perf_counter()
            retrieve(query.query)
            timings.append(_elapsed_ms(started))
    return {
        "hit_at_limit": hits,
        "query_count": len(queries),
        "hit_rate": round(hits / len(queries), 6),
        "timing_ms": _timing_summary(timings),
        "queries": samples,
    }


def _timing_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 3),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000, 3)

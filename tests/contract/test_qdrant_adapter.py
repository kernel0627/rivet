from __future__ import annotations

import math
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from rivet.code_intelligence.python_ast import PythonAstAnalyzer
from rivet.code_intelligence.retrieval.dense import HashEmbeddingModel
from rivet.code_intelligence.retrieval.qdrant import (
    QdrantChunkIndex,
    QdrantConfigurationError,
    chunk_from_payload,
    chunk_to_payload,
    point_id_for_chunk,
)


class _Value:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class _Models:
    Distance = SimpleNamespace(COSINE="cosine")
    PayloadSchemaType = SimpleNamespace(KEYWORD="keyword")
    VectorParams = _Value
    PointStruct = _Value
    Filter = _Value
    FieldCondition = _Value
    MatchValue = _Value
    FilterSelector = _Value


@dataclass
class _Collection:
    size: int

    @property
    def config(self) -> Any:
        return _Value(params=_Value(vectors=_Value(size=self.size)))


class _FakeQdrantClient:
    def __init__(self, *, existing_size: int | None = None) -> None:
        self.size = existing_size
        self.points: dict[str, Any] = {}
        self.payload_indexes: list[str] = []

    def collection_exists(self, *, collection_name: str) -> bool:
        return self.size is not None

    def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        self.size = vectors_config.size

    def get_collection(self, *, collection_name: str) -> _Collection:
        assert self.size is not None
        return _Collection(self.size)

    def create_payload_index(
        self,
        *,
        collection_name: str,
        field_name: str,
        field_schema: str,
        wait: bool,
    ) -> None:
        self.payload_indexes.append(field_name)

    def upsert(
        self,
        *,
        collection_name: str,
        points: list[Any],
        wait: bool,
    ) -> None:
        for point in points:
            self.points[str(point.id)] = point

    def count(
        self,
        *,
        collection_name: str,
        count_filter: Any,
        exact: bool,
    ) -> Any:
        return _Value(count=len(self._matches(count_filter)))

    def delete(
        self,
        *,
        collection_name: str,
        points_selector: Any,
        wait: bool,
    ) -> None:
        for point in self._matches(points_selector.filter):
            self.points.pop(str(point.id), None)

    def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        query_filter: Any,
        limit: int,
        with_payload: bool,
        with_vectors: bool,
    ) -> Any:
        points = self._matches(query_filter)
        scored = [
            _Value(
                payload=point.payload,
                score=sum(
                    left * right
                    for left, right in zip(query, point.vector, strict=True)
                ),
            )
            for point in points
        ]
        scored.sort(key=lambda point: -point.score)
        return _Value(points=scored[:limit])

    def _matches(self, query_filter: Any | None) -> list[Any]:
        if query_filter is None:
            return list(self.points.values())
        conditions = query_filter.must
        return [
            point
            for point in self.points.values()
            if all(
                point.payload[condition.key] == condition.match.value
                for condition in conditions
            )
        ]


class QdrantAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = PythonAstAnalyzer().chunks(
            """\
def refund_invoice(invoice):
    return invoice.refund()

def ship_order(order):
    return order.ship()
""",
            file_path="billing.py",
            workspace_id="workspace",
            index_version="v1",
        )

    def test_create_upsert_query_delete_and_payload_round_trip(self) -> None:
        client = _FakeQdrantClient()
        index = QdrantChunkIndex(
            "rivet_workspace",
            HashEmbeddingModel(dimension=64),
            client=client,
            models=_Models,
        )

        index.upsert(self.chunks)
        results = index.search(
            "refund invoice",
            limit=3,
            workspace_id="workspace",
        )

        self.assertEqual(client.size, 64)
        self.assertEqual(client.payload_indexes, ["workspace_id", "file_path"])
        self.assertTrue(results)
        self.assertIn(
            "refund_invoice",
            {result.chunk.qualified_name for result in results},
        )
        self.assertTrue(math.isfinite(results[0].score))
        self.assertEqual(
            chunk_from_payload(chunk_to_payload(self.chunks[0])),
            self.chunks[0],
        )
        self.assertEqual(
            point_id_for_chunk(self.chunks[0]),
            point_id_for_chunk(self.chunks[0]),
        )
        self.assertEqual(index.delete_file("workspace", "billing.py"), len(self.chunks))
        self.assertFalse(index.search("refund", limit=3))

    def test_existing_collection_dimension_must_match(self) -> None:
        with self.assertRaises(QdrantConfigurationError):
            QdrantChunkIndex(
                "rivet_workspace",
                HashEmbeddingModel(dimension=64),
                client=_FakeQdrantClient(existing_size=32),
                models=_Models,
            )


if __name__ == "__main__":
    unittest.main()

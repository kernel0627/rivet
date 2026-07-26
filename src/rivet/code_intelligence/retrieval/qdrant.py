from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID, uuid5

from rivet.code_intelligence.retrieval.dense import EmbeddingModel
from rivet.code_intelligence.types import CodeChunk, RetrievedChunk

_POINT_NAMESPACE = UUID("b868de16-e267-4f22-8daa-72cd3e1e4543")
_PAYLOAD_SCHEMA_VERSION = 1


class QdrantAdapterError(RuntimeError):
    """Base error for Qdrant adapter configuration and persisted payloads."""


class QdrantDependencyError(QdrantAdapterError):
    """The optional qdrant-client package is required but unavailable."""


class QdrantConfigurationError(QdrantAdapterError):
    """The collection cannot safely store this embedding model."""


class QdrantPayloadError(QdrantAdapterError):
    """A retrieved point does not contain a valid Rivet CodeChunk payload."""


class _ModelsNamespace(Protocol):
    Distance: Any
    VectorParams: Any
    PointStruct: Any
    Filter: Any
    FieldCondition: Any
    MatchValue: Any
    FilterSelector: Any


class QdrantChunkIndex:
    """Qdrant-backed dense ChunkIndex and Retriever.

    qdrant-client is loaded only when no client is injected. Tests and
    embedded callers can provide a compatible client and models namespace
    without installing or importing qdrant-client.
    """

    def __init__(
        self,
        collection_name: str,
        embedding_model: EmbeddingModel,
        *,
        client: Any | None = None,
        models: _ModelsNamespace | None = None,
        client_options: Mapping[str, Any] | None = None,
        batch_size: int = 64,
        wait: bool = True,
        initialize: bool = True,
    ) -> None:
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        if embedding_model.dimension < 1:
            raise ValueError("embedding model dimension must be at least one")
        if batch_size < 1:
            raise ValueError("batch_size must be at least one")
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self.batch_size = batch_size
        self.wait = wait
        self._owns_client = client is None

        if client is None:
            client_class, loaded_models = _load_qdrant_components()
            self.client = client_class(**dict(client_options or {}))
            self.models = loaded_models
        else:
            if models is None:
                raise ValueError("models must be injected with a custom Qdrant client")
            self.client = client
            self.models = models

        if initialize:
            self.initialize_collection()

    def close(self) -> None:
        if self._owns_client:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> QdrantChunkIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize_collection(self) -> bool:
        """Create the collection when absent and validate an existing vector size.

        Returns True when a new collection was created.
        """

        exists = bool(
            self.client.collection_exists(collection_name=self.collection_name)
        )
        if exists:
            get_collection = getattr(self.client, "get_collection", None)
            if callable(get_collection):
                existing_size = _collection_vector_size(
                    get_collection(collection_name=self.collection_name)
                )
                if (
                    existing_size is not None
                    and existing_size != self.embedding_model.dimension
                ):
                    raise QdrantConfigurationError(
                        f"collection {self.collection_name!r} has vector size "
                        f"{existing_size}, expected {self.embedding_model.dimension}"
                    )
            return False

        distance = self.models.Distance.COSINE
        vector_parameters = self.models.VectorParams(
            size=self.embedding_model.dimension,
            distance=distance,
        )
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=vector_parameters,
        )
        self._create_payload_indexes()
        return True

    def _create_payload_indexes(self) -> None:
        create_payload_index = getattr(self.client, "create_payload_index", None)
        payload_schema_type = getattr(self.models, "PayloadSchemaType", None)
        if not callable(create_payload_index) or payload_schema_type is None:
            return
        keyword = payload_schema_type.KEYWORD
        for field_name in ("workspace_id", "file_path"):
            create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=keyword,
                wait=self.wait,
            )

    def upsert(self, chunks: Sequence[CodeChunk]) -> None:
        if not chunks:
            return
        texts = [_embedding_text(chunk) for chunk in chunks]
        vectors = self.embedding_model.embed(texts)
        if len(vectors) != len(chunks):
            raise ValueError("embedding model returned the wrong number of vectors")

        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            normalized_vector = _validate_vector(
                vector,
                expected_dimension=self.embedding_model.dimension,
            )
            points.append(
                self.models.PointStruct(
                    id=point_id_for_chunk(chunk),
                    vector=normalized_vector,
                    payload=chunk_to_payload(chunk),
                )
            )

        for offset in range(0, len(points), self.batch_size):
            self.client.upsert(
                collection_name=self.collection_name,
                points=points[offset : offset + self.batch_size],
                wait=self.wait,
            )

    def delete_file(self, workspace_id: str, file_path: str) -> int:
        if not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if not file_path.strip():
            raise ValueError("file_path must not be empty")
        query_filter = self._payload_filter(
            workspace_id=workspace_id,
            file_path=file_path,
        )
        return self._delete_filter(query_filter)

    def delete_workspace(self, workspace_id: str) -> int:
        if not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        return self._delete_filter(self._payload_filter(workspace_id=workspace_id))

    def _delete_filter(self, query_filter: Any) -> int:
        count_result = self.client.count(
            collection_name=self.collection_name,
            count_filter=query_filter,
            exact=True,
        )
        count = _count_value(count_result)
        if count:
            selector = self.models.FilterSelector(filter=query_filter)
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=selector,
                wait=self.wait,
            )
        return count

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        if limit < 1:
            raise ValueError("limit must be at least one")
        if workspace_id is not None and not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        vectors = self.embedding_model.embed([query])
        if len(vectors) != 1:
            raise ValueError("embedding model must return one query vector")
        query_vector = _validate_vector(
            vectors[0],
            expected_dimension=self.embedding_model.dimension,
        )
        query_filter = (
            self._payload_filter(workspace_id=workspace_id)
            if workspace_id is not None
            else None
        )
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        scored_chunks = []
        for point in _response_points(response):
            payload = _point_value(point, "payload")
            if not isinstance(payload, Mapping):
                raise QdrantPayloadError("Qdrant result is missing its CodeChunk payload")
            chunk = chunk_from_payload(payload)
            score = float(_point_value(point, "score"))
            if not math.isfinite(score):
                raise QdrantPayloadError("Qdrant result contains a non-finite score")
            scored_chunks.append((score, chunk))
        scored_chunks.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            RetrievedChunk(
                chunk=chunk,
                score=score,
                source="qdrant",
                rank=rank,
                component_scores={"cosine": score},
            )
            for rank, (score, chunk) in enumerate(scored_chunks[:limit], start=1)
        ]

    def _payload_filter(
        self,
        *,
        workspace_id: str,
        file_path: str | None = None,
    ) -> Any:
        conditions = [
            self.models.FieldCondition(
                key="workspace_id",
                match=self.models.MatchValue(value=workspace_id),
            )
        ]
        if file_path is not None:
            conditions.append(
                self.models.FieldCondition(
                    key="file_path",
                    match=self.models.MatchValue(value=file_path),
                )
            )
        return self.models.Filter(must=conditions)


def point_id_for_chunk(chunk: CodeChunk) -> str:
    """Return a Qdrant-compatible deterministic UUID for a logical chunk."""

    identity = f"{chunk.workspace_id}\0{chunk.chunk_id}"
    return str(uuid5(_POINT_NAMESPACE, identity))


def chunk_to_payload(chunk: CodeChunk) -> dict[str, Any]:
    payload = {
        "schema_version": _PAYLOAD_SCHEMA_VERSION,
        "chunk_id": chunk.chunk_id,
        "workspace_id": chunk.workspace_id,
        "index_version": chunk.index_version,
        "file_path": chunk.file_path,
        "language": chunk.language,
        "kind": chunk.kind,
        "content": chunk.content,
        "content_hash": chunk.content_hash,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "symbol": chunk.symbol,
        "qualified_name": chunk.qualified_name,
        "parent": chunk.parent,
        "imports": list(chunk.imports),
        "metadata": chunk.metadata,
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise QdrantPayloadError("CodeChunk payload must be JSON-compatible") from error
    return json.loads(serialized)


def chunk_from_payload(payload: Mapping[str, Any]) -> CodeChunk:
    try:
        schema_version = int(payload["schema_version"])
        if schema_version != _PAYLOAD_SCHEMA_VERSION:
            raise QdrantPayloadError(
                f"unsupported CodeChunk payload schema_version {schema_version}"
            )
        metadata = payload.get("metadata", {})
        imports = payload.get("imports", [])
        if not isinstance(metadata, Mapping):
            raise QdrantPayloadError("CodeChunk metadata must be an object")
        if not isinstance(imports, Sequence) or isinstance(imports, (str, bytes)):
            raise QdrantPayloadError("CodeChunk imports must be an array")
        chunk = CodeChunk(
            chunk_id=str(payload["chunk_id"]),
            workspace_id=str(payload["workspace_id"]),
            index_version=str(payload["index_version"]),
            file_path=str(payload["file_path"]),
            language=str(payload["language"]),
            kind=str(payload["kind"]),
            content=str(payload["content"]),
            content_hash=str(payload["content_hash"]),
            start_line=int(payload["start_line"]),
            end_line=int(payload["end_line"]),
            symbol=_optional_string(payload.get("symbol")),
            qualified_name=_optional_string(payload.get("qualified_name")),
            parent=_optional_string(payload.get("parent")),
            imports=tuple(str(item) for item in imports),
            metadata=dict(metadata),
        )
    except QdrantPayloadError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise QdrantPayloadError("invalid CodeChunk payload") from error
    if chunk.start_line < 1 or chunk.end_line < chunk.start_line:
        raise QdrantPayloadError("CodeChunk payload contains an invalid line range")
    return chunk


def _load_qdrant_components() -> tuple[Any, Any]:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models
    except ImportError as error:
        raise QdrantDependencyError(
            "Qdrant support requires the optional 'qdrant-client' package"
        ) from error
    return QdrantClient, models


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


def _validate_vector(
    vector: Sequence[float],
    *,
    expected_dimension: int,
) -> list[float]:
    if len(vector) != expected_dimension:
        raise ValueError("embedding vector dimension does not match the model")
    result = [float(value) for value in vector]
    if any(not math.isfinite(value) for value in result):
        raise ValueError("embedding vector contains a non-finite value")
    return result


def _collection_vector_size(collection: Any) -> int | None:
    value = collection
    for attribute in ("config", "params", "vectors"):
        value = _object_value(value, attribute)
        if value is None:
            return None
    if isinstance(value, Mapping):
        if "size" in value:
            return int(value["size"])
        return None
    size = _object_value(value, "size")
    return int(size) if size is not None else None


def _response_points(response: Any) -> Sequence[Any]:
    points = _object_value(response, "points")
    if (
        points is None
        or isinstance(points, (str, bytes))
        or not isinstance(points, Sequence)
    ):
        raise QdrantPayloadError("Qdrant query_points returned an invalid response")
    return points


def _point_value(point: Any, name: str) -> Any:
    value = _object_value(point, name)
    if value is None:
        raise QdrantPayloadError(f"Qdrant point is missing {name}")
    return value


def _count_value(result: Any) -> int:
    value = _object_value(result, "count")
    if value is None:
        raise QdrantPayloadError("Qdrant count returned an invalid response")
    count = int(value)
    if count < 0:
        raise QdrantPayloadError("Qdrant count cannot be negative")
    return count


def _object_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rivet.code_intelligence.types import CodeChunk, RetrievedChunk

_TOKEN_PATTERN = re.compile(r"[\w.]+", re.UNICODE)


class SqliteSparseIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteSparseIndex:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    index_version TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    symbol TEXT,
                    qualified_name TEXT,
                    parent TEXT,
                    imports_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    content,
                    symbol,
                    qualified_name,
                    file_path,
                    tokenize = 'unicode61'
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_file "
                "ON chunks(workspace_id, file_path)"
            )

    def upsert(self, chunks: Sequence[CodeChunk]) -> None:
        with self._connection:
            for chunk in chunks:
                self._connection.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?",
                    (chunk.chunk_id,),
                )
                self._connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, workspace_id, index_version, file_path,
                        language, kind, content, content_hash, start_line,
                        end_line, symbol, qualified_name, parent,
                        imports_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        workspace_id=excluded.workspace_id,
                        index_version=excluded.index_version,
                        file_path=excluded.file_path,
                        language=excluded.language,
                        kind=excluded.kind,
                        content=excluded.content,
                        content_hash=excluded.content_hash,
                        start_line=excluded.start_line,
                        end_line=excluded.end_line,
                        symbol=excluded.symbol,
                        qualified_name=excluded.qualified_name,
                        parent=excluded.parent,
                        imports_json=excluded.imports_json,
                        metadata_json=excluded.metadata_json
                    """,
                    _chunk_row(chunk),
                )
                self._connection.execute(
                    """
                    INSERT INTO chunks_fts(
                        chunk_id, content, symbol, qualified_name, file_path
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.content,
                        chunk.symbol or "",
                        chunk.qualified_name or "",
                        chunk.file_path,
                    ),
                )

    def delete_file(self, workspace_id: str, file_path: str) -> int:
        rows = self._connection.execute(
            "SELECT chunk_id FROM chunks WHERE workspace_id = ? AND file_path = ?",
            (workspace_id, file_path),
        ).fetchall()
        chunk_ids = [row["chunk_id"] for row in rows]
        with self._connection:
            for chunk_id in chunk_ids:
                self._connection.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?",
                    (chunk_id,),
                )
            cursor = self._connection.execute(
                "DELETE FROM chunks WHERE workspace_id = ? AND file_path = ?",
                (workspace_id, file_path),
            )
        return cursor.rowcount

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        expression = _fts_expression(query)
        if not expression:
            return []
        parameters: list[object] = [expression]
        workspace_clause = ""
        if workspace_id is not None:
            workspace_clause = "AND c.workspace_id = ?"
            parameters.append(workspace_id)
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT c.*, bm25(chunks_fts, 0.0, 1.0, 2.0, 2.0, 0.5) AS raw_score
            FROM chunks_fts
            JOIN chunks AS c ON c.chunk_id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
            {workspace_clause}
            ORDER BY raw_score ASC, c.chunk_id ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [
            RetrievedChunk(
                chunk=_row_to_chunk(row),
                score=1.0 / (1.0 + max(0.0, float(row["raw_score"]))),
                source="sparse",
                rank=rank,
                component_scores={"bm25_raw": float(row["raw_score"])},
            )
            for rank, row in enumerate(rows, start=1)
        ]

    def file_hashes(self, workspace_id: str) -> dict[str, set[str]]:
        rows = self._connection.execute(
            "SELECT file_path, content_hash FROM chunks WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row["file_path"], set()).add(row["content_hash"])
        return result


def _fts_expression(query: str) -> str:
    tokens = _TOKEN_PATTERN.findall(query)
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _chunk_row(chunk: CodeChunk) -> tuple[object, ...]:
    return (
        chunk.chunk_id,
        chunk.workspace_id,
        chunk.index_version,
        chunk.file_path,
        chunk.language,
        chunk.kind,
        chunk.content,
        chunk.content_hash,
        chunk.start_line,
        chunk.end_line,
        chunk.symbol,
        chunk.qualified_name,
        chunk.parent,
        json.dumps(chunk.imports, ensure_ascii=False),
        json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
    )


def _row_to_chunk(row: sqlite3.Row) -> CodeChunk:
    return CodeChunk(
        chunk_id=row["chunk_id"],
        workspace_id=row["workspace_id"],
        index_version=row["index_version"],
        file_path=row["file_path"],
        language=row["language"],
        kind=row["kind"],
        content=row["content"],
        content_hash=row["content_hash"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        symbol=row["symbol"],
        qualified_name=row["qualified_name"],
        parent=row["parent"],
        imports=tuple(json.loads(row["imports_json"])),
        metadata=json.loads(row["metadata_json"]),
    )

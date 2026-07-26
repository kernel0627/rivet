from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rivet.code_intelligence.python_ast import PythonAnalysisError, PythonAstAnalyzer
from rivet.code_intelligence.retrieval.protocols import ChunkIndex
from rivet.code_intelligence.retrieval.sparse import SqliteSparseIndex

_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class IndexReport:
    scanned_files: int
    indexed_files: int
    unchanged_files: int
    deleted_files: int
    failed_files: tuple[tuple[str, str], ...]
    index_version: str


class WorkspaceIndexer:
    def __init__(
        self,
        *,
        workspace_root: Path,
        workspace_id: str,
        sparse_index: SqliteSparseIndex,
        additional_indexes: Sequence[ChunkIndex] = (),
        analyzer: PythonAstAnalyzer | None = None,
        max_file_bytes: int = 2_000_000,
    ) -> None:
        self.root = workspace_root.expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace is not a directory: {self.root}")
        self.workspace_id = workspace_id
        self.sparse_index = sparse_index
        self.additional_indexes = tuple(additional_indexes)
        self.analyzer = analyzer or PythonAstAnalyzer()
        self.max_file_bytes = max_file_bytes
        self._last_report: IndexReport | None = None

    @property
    def last_report(self) -> IndexReport | None:
        return self._last_report

    def refresh(self) -> IndexReport:
        candidates = self._python_files()
        existing = self.sparse_index.file_hashes(self.workspace_id)
        current_paths = {path for path, _resolved in candidates}
        deleted = sorted(set(existing) - current_paths)
        for file_path in deleted:
            self.sparse_index.delete_file(self.workspace_id, file_path)
            for index in self.additional_indexes:
                index.delete_file(self.workspace_id, file_path)

        indexed = 0
        unchanged = 0
        failures: list[tuple[str, str]] = []
        source_hashes: list[str] = []
        pending: list[tuple[str, Path, str, str, bool]] = []
        for display_path, resolved in candidates:
            try:
                raw = resolved.read_bytes()
                if len(raw) > self.max_file_bytes:
                    raise ValueError(
                        f"file exceeds index limit of {self.max_file_bytes} bytes"
                    )
                source = raw.decode("utf-8")
                source_hash = hashlib.sha256(raw).hexdigest()
                source_hashes.append(f"{display_path}:{source_hash}")
                changed = source_hash not in existing.get(display_path, set())
                if not changed:
                    unchanged += 1
                    if not self.additional_indexes:
                        continue
                pending.append((display_path, resolved, source, source_hash, changed))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                failures.append((display_path, str(exc)))

        index_version = hashlib.sha256(
            "\n".join(sorted(source_hashes)).encode("utf-8")
        ).hexdigest()
        for display_path, _resolved, source, _source_hash, changed in pending:
            try:
                chunks = self.analyzer.chunks(
                    source,
                    file_path=display_path,
                    workspace_id=self.workspace_id,
                    index_version=index_version,
                )
                if changed:
                    self.sparse_index.delete_file(self.workspace_id, display_path)
                    self.sparse_index.upsert(chunks)
                    indexed += 1
                for index in self.additional_indexes:
                    index.delete_file(self.workspace_id, display_path)
                    index.upsert(chunks)
            except PythonAnalysisError as exc:
                failures.append((display_path, str(exc)))

        report = IndexReport(
            scanned_files=len(candidates),
            indexed_files=indexed,
            unchanged_files=unchanged,
            deleted_files=len(deleted),
            failed_files=tuple(failures),
            index_version=index_version,
        )
        self._last_report = report
        return report

    def _python_files(self) -> list[tuple[str, Path]]:
        files: list[tuple[str, Path]] = []
        for path in self.root.rglob("*.py"):
            relative = path.relative_to(self.root)
            if any(part in _IGNORED_DIRECTORIES for part in relative.parts):
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError:
                continue
            if resolved.is_file():
                files.append((relative.as_posix(), resolved))
        files.sort(key=lambda item: item[0])
        return files

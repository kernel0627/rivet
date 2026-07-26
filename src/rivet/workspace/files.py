from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from rivet.workspace.boundary import ResolvedPath, WorkspaceBoundary, WorkspaceViolation

DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".rivet",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)


@dataclass(frozen=True)
class DirectoryListing:
    entries: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class FileRead:
    text: str
    start_line: int
    end_line: int
    bytes_read: int
    truncated: bool
    encoding: str
    sha256: str | None
    hash_complete: bool


def list_directory(
    boundary: WorkspaceBoundary,
    target: ResolvedPath,
    *,
    max_depth: int,
    max_entries: int,
    include_hidden: bool = False,
    ignored_directories: frozenset[str] = DEFAULT_IGNORED_DIRECTORIES,
) -> DirectoryListing:
    current = boundary.revalidate(target)
    if not current.path.is_dir():
        raise WorkspaceViolation(f"not a directory: {current.relative_path}")

    rows: list[str] = []
    truncated = False

    def visit(directory: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise WorkspaceViolation(f"cannot list directory: {directory}") from exc
        for entry in entries:
            if len(rows) >= max_entries:
                truncated = True
                return
            if entry.name in ignored_directories:
                continue
            if not include_hidden and entry.name.startswith("."):
                continue
            path = Path(entry.path)
            relative = path.relative_to(boundary.root).as_posix()
            if entry.is_symlink():
                rows.append(f"{relative}@")
                continue
            if entry.is_dir(follow_symlinks=False):
                rows.append(f"{relative}/")
                if depth < max_depth:
                    visit(path, depth + 1)
            else:
                rows.append(relative)

    visit(current.path, 1)
    return DirectoryListing(entries=tuple(rows), truncated=truncated)


def read_text_file(
    boundary: WorkspaceBoundary,
    target: ResolvedPath,
    *,
    start_line: int,
    end_line: int | None,
    max_chars: int,
    max_bytes: int,
    max_hash_bytes: int = 8 * 1024 * 1024,
) -> FileRead:
    current = boundary.revalidate(target)
    if not current.path.is_file():
        raise WorkspaceViolation(f"not a file: {current.relative_path}")

    with current.path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    byte_truncated = len(raw) > max_bytes
    if byte_truncated:
        raw = raw[:max_bytes]
    if b"\x00" in raw[:8192]:
        raise WorkspaceViolation("binary files are not supported")

    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    final_line = len(all_lines) if end_line is None else min(end_line, len(all_lines))
    selected_lines = all_lines[start_line - 1 : final_line] if start_line <= len(all_lines) else []
    rendered = "\n".join(
        f"{number}: {line}" for number, line in enumerate(selected_lines, start=start_line)
    )
    char_truncated = len(rendered) > max_chars
    if char_truncated:
        rendered = rendered[:max_chars]

    digest: str | None = None
    hash_complete = current.path.stat().st_size <= max_hash_bytes
    if hash_complete:
        hasher = hashlib.sha256()
        with current.path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()

    range_truncated = end_line is not None and end_line < len(all_lines)
    truncated = byte_truncated or char_truncated or range_truncated
    return FileRead(
        text=rendered,
        start_line=start_line,
        end_line=(start_line + len(selected_lines) - 1 if selected_lines else start_line - 1),
        bytes_read=len(raw),
        truncated=truncated,
        encoding="utf-8",
        sha256=digest,
        hash_complete=hash_complete,
    )

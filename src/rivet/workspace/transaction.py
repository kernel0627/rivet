from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rivet.workspace.boundary import ResolvedPath, WorkspaceBoundary, WorkspaceChanged


class AtomicWriteConflict(WorkspaceChanged):
    pass


@dataclass(frozen=True)
class AtomicWriteResult:
    path: str
    before_sha256: str | None
    after_sha256: str
    bytes_written: int


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_write_bytes(
    boundary: WorkspaceBoundary,
    target: ResolvedPath,
    content: bytes,
    *,
    expected_sha256: str | None,
    mode: int | None = None,
) -> AtomicWriteResult:
    current = boundary.revalidate(target, require_unchanged=True)
    before_sha256 = file_sha256(current.path) if current.existed else None
    if before_sha256 != expected_sha256:
        raise AtomicWriteConflict(f"content hash changed before write: {current.relative_path}")

    parent = current.path.parent
    parent_target = boundary.resolve(parent)
    boundary.revalidate(parent_target, require_unchanged=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{current.path.name}.rivet-",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is None and current.existed:
            mode = current.path.stat().st_mode & 0o7777
        if mode is not None:
            os.chmod(temporary, mode)
        boundary.revalidate(current, require_unchanged=True)
        os.replace(temporary, current.path)
        _fsync_directory(parent)
    finally:
        temporary.unlink(missing_ok=True)

    after_sha256 = file_sha256(current.path)
    return AtomicWriteResult(
        path=current.relative_path,
        before_sha256=before_sha256,
        after_sha256=after_sha256,
        bytes_written=len(content),
    )


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

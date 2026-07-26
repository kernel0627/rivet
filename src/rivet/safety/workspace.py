from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when a tool path escapes the configured workspace."""


@dataclass(frozen=True)
class WorkspaceBoundary:
    root: Path

    def __post_init__(self) -> None:
        root = self.root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"workspace does not exist or is not a directory: {root}")
        object.__setattr__(self, "root", root)

    def resolve(self, path: str | Path = ".", *, must_exist: bool = True) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=must_exist)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolation(
                f"path escapes workspace {self.root}: {path}"
            ) from exc
        return resolved

    def display(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix() or "."


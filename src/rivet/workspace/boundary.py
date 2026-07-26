from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when a path is unsafe or outside the configured workspace."""


class WorkspaceChanged(RuntimeError):
    """Raised when a prepared target no longer identifies the same object."""


@dataclass(frozen=True)
class PathIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> PathIdentity:
        info = path.stat(follow_symlinks=False)
        return cls(
            device=info.st_dev,
            inode=info.st_ino,
            mode=info.st_mode,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        )

    @property
    def object_key(self) -> tuple[int, int, int]:
        return (self.device, self.inode, stat.S_IFMT(self.mode))


@dataclass(frozen=True)
class ResolvedPath:
    requested_path: str
    path: Path
    relative_path: str
    existed: bool
    identity: PathIdentity | None
    parent_identity: PathIdentity
    final_component_was_symlink: bool = False

    def revision_payload(self) -> str:
        identity = (
            (
                self.identity.device,
                self.identity.inode,
                self.identity.mode,
                self.identity.size,
                self.identity.mtime_ns,
            )
            if self.identity is not None
            else None
        )
        return repr(
            (
                self.relative_path,
                self.existed,
                identity,
                (
                    self.parent_identity.device,
                    self.parent_identity.inode,
                    self.parent_identity.mode,
                    self.parent_identity.size,
                    self.parent_identity.mtime_ns,
                ),
            )
        )


class WorkspaceBoundary:
    def __init__(self, root: str | Path) -> None:
        requested_root = Path(root).expanduser()
        lexical_root = Path(os.path.abspath(os.fspath(requested_root)))
        if requested_root.is_symlink():
            raise WorkspaceViolation("workspace root cannot be a symlink")
        try:
            canonical_root = requested_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceViolation(f"cannot resolve workspace root: {root}") from exc
        if not canonical_root.is_dir():
            raise WorkspaceViolation(
                f"workspace does not exist or is not a directory: {canonical_root}"
            )
        self._root = canonical_root
        self._lexical_root = lexical_root
        self._root_identity = PathIdentity.from_path(canonical_root)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(
        self,
        requested: str | Path = ".",
        *,
        must_exist: bool = True,
        for_write: bool = False,
        allow_final_symlink: bool = True,
    ) -> ResolvedPath:
        requested_text = os.fspath(requested)
        if "\x00" in requested_text:
            raise WorkspaceViolation("path cannot contain a null byte")

        expanded = Path(requested_text).expanduser()
        if expanded.is_absolute():
            absolute = Path(os.path.abspath(os.fspath(expanded)))
            try:
                relative = absolute.relative_to(self._lexical_root)
            except ValueError:
                lexical = absolute
            else:
                lexical = self.root / relative
        else:
            lexical = self.root / expanded
        lexical = Path(os.path.abspath(os.fspath(lexical)))
        self._assert_within(lexical, requested_text, lexical=True)
        self._check_components(lexical, requested_text)

        final_is_symlink = lexical.is_symlink()
        if final_is_symlink and not allow_final_symlink:
            raise WorkspaceViolation(
                f"symlink target is not allowed for this operation: {requested}"
            )
        if final_is_symlink and not lexical.exists():
            raise WorkspaceViolation(f"broken symlink is not allowed: {requested}")

        try:
            canonical = lexical.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise WorkspaceViolation(f"path does not exist: {requested}") from exc
        except (OSError, RuntimeError) as exc:
            raise WorkspaceViolation(f"cannot safely resolve path: {requested}") from exc
        self._assert_within(canonical, requested_text, lexical=False)

        existed = canonical.exists()
        if must_exist and not existed:
            raise WorkspaceViolation(f"path does not exist: {requested}")
        if for_write and final_is_symlink:
            raise WorkspaceViolation(f"refusing to replace a symlink: {requested}")

        parent = canonical.parent if canonical != self.root else self.root
        if not parent.exists() or not parent.is_dir():
            raise WorkspaceViolation(f"parent directory does not exist: {requested}")
        self._assert_within(parent.resolve(strict=True), requested_text, lexical=False)

        relative = canonical.relative_to(self.root).as_posix() or "."
        return ResolvedPath(
            requested_path=requested_text,
            path=canonical,
            relative_path=relative,
            existed=existed,
            identity=PathIdentity.from_path(canonical) if existed else None,
            parent_identity=PathIdentity.from_path(parent),
            final_component_was_symlink=final_is_symlink,
        )

    def revalidate(
        self,
        prepared: ResolvedPath,
        *,
        require_unchanged: bool = False,
    ) -> ResolvedPath:
        current = self.resolve(
            prepared.requested_path,
            must_exist=prepared.existed,
            for_write=not prepared.final_component_was_symlink and require_unchanged,
            allow_final_symlink=prepared.final_component_was_symlink,
        )
        if current.path != prepared.path:
            raise WorkspaceChanged(f"resolved target changed: {prepared.relative_path}")
        if current.existed != prepared.existed:
            raise WorkspaceChanged(f"target existence changed: {prepared.relative_path}")
        if prepared.identity and current.identity:
            if current.identity.object_key != prepared.identity.object_key:
                raise WorkspaceChanged(f"target object changed: {prepared.relative_path}")
            if require_unchanged and current.identity != prepared.identity:
                raise WorkspaceChanged(f"target content metadata changed: {prepared.relative_path}")
        if current.parent_identity.object_key != prepared.parent_identity.object_key:
            raise WorkspaceChanged(f"target parent changed: {prepared.relative_path}")
        return current

    def display(self, path: str | Path) -> str:
        resolved = Path(path).resolve(strict=False)
        self._assert_within(resolved, os.fspath(path), lexical=False)
        return resolved.relative_to(self.root).as_posix() or "."

    def revision(self, *targets: ResolvedPath) -> str:
        digest = hashlib.sha256()
        digest.update(os.fspath(self.root).encode("utf-8"))
        digest.update(repr(self._root_identity.object_key).encode("ascii"))
        for target in sorted(targets, key=lambda item: item.relative_path):
            try:
                current = self.resolve(
                    target.requested_path,
                    must_exist=target.existed,
                    allow_final_symlink=target.final_component_was_symlink,
                )
                payload = current.revision_payload()
            except (WorkspaceViolation, WorkspaceChanged):
                payload = f"{target.relative_path}:changed"
            digest.update(payload.encode("utf-8"))
        return digest.hexdigest()

    def _assert_within(
        self,
        path: Path,
        requested: str,
        *,
        lexical: bool,
    ) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            boundary = "lexical" if lexical else "canonical"
            raise WorkspaceViolation(
                f"{boundary} path escapes workspace {self.root}: {requested}"
            ) from exc

    def _check_components(self, lexical: Path, requested: str) -> None:
        relative = lexical.relative_to(self.root)
        current = self.root
        for component in relative.parts:
            current = current / component
            if not current.is_symlink():
                if not current.exists():
                    break
                continue
            if not current.exists():
                raise WorkspaceViolation(f"broken symlink is not allowed: {requested}")
            try:
                target = current.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise WorkspaceViolation(f"cannot resolve symlink component: {requested}") from exc
            self._assert_within(target, requested, lexical=False)

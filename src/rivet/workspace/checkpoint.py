from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from rivet.workspace.boundary import ResolvedPath, WorkspaceBoundary
from rivet.workspace.transaction import atomic_write_bytes, file_sha256


class CheckpointError(RuntimeError):
    pass


class RewindConflict(CheckpointError):
    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = tuple(paths)
        super().__init__(
            "workspace changed after the checkpoint: " + ", ".join(self.paths)
        )


@dataclass(frozen=True)
class CheckpointEntry:
    path: str
    existed: bool
    mode: int | None
    before_sha256: str | None
    before_artifact: str | None
    expected_after_sha256: str | None = None


@dataclass(frozen=True)
class CheckpointManifest:
    checkpoint_id: str
    workspace_revision: str
    tool_name: str
    prepared_digest: str
    affected_paths: tuple[CheckpointEntry, ...]
    manifest_digest: str
    created_at: float
    run_id: str | None = None
    turn_id: str | None = None
    tool_execution_id: str | None = None


@dataclass(frozen=True)
class RewindResult:
    checkpoint_id: str
    restored_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    workspace_revision: str


class CheckpointService(Protocol):
    def create(
        self,
        *,
        boundary: WorkspaceBoundary,
        targets: Sequence[ResolvedPath],
        tool_name: str,
        prepared_digest: str,
        metadata: Mapping[str, str] | None = None,
    ) -> CheckpointManifest | Awaitable[CheckpointManifest]:
        """Persist before-images for all declared write targets."""


class FileCheckpointService:
    """Content-addressed checkpoint store intended for an external state root."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root).expanduser().resolve(strict=False)

    def create(
        self,
        *,
        boundary: WorkspaceBoundary,
        targets: Sequence[ResolvedPath],
        tool_name: str,
        prepared_digest: str,
        metadata: Mapping[str, str] | None = None,
    ) -> CheckpointManifest:
        try:
            self.artifact_root.relative_to(boundary.root)
        except ValueError:
            pass
        else:
            raise CheckpointError("checkpoint store must be outside the target workspace")
        self.artifact_root.mkdir(parents=True, exist_ok=True)

        entries: list[CheckpointEntry] = []
        for target in targets:
            current = boundary.revalidate(target, require_unchanged=True)
            if current.existed and current.path.is_dir():
                continue
            if current.existed:
                digest, artifact = self._store_file_blob(current.path)
                boundary.revalidate(current, require_unchanged=True)
                mode = current.path.stat().st_mode & 0o7777
                artifact_ref = artifact.relative_to(self.artifact_root).as_posix()
            else:
                digest = None
                artifact_ref = None
                mode = None
            entries.append(
                CheckpointEntry(
                    path=current.relative_path,
                    existed=current.existed,
                    mode=mode,
                    before_sha256=digest,
                    before_artifact=artifact_ref,
                )
            )
        if not entries:
            raise CheckpointError("write action declared no checkpointable file targets")

        metadata = metadata or {}
        checkpoint_id = uuid.uuid4().hex
        created_at = time.time()
        unsigned = {
            "checkpoint_id": checkpoint_id,
            "workspace_revision": boundary.revision(*targets),
            "tool_name": tool_name,
            "prepared_digest": prepared_digest,
            "affected_paths": [asdict(entry) for entry in entries],
            "created_at": created_at,
            "run_id": metadata.get("run_id"),
            "turn_id": metadata.get("turn_id"),
            "tool_execution_id": metadata.get("tool_execution_id"),
        }
        manifest_digest = hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest = CheckpointManifest(
            checkpoint_id=checkpoint_id,
            workspace_revision=unsigned["workspace_revision"],
            tool_name=tool_name,
            prepared_digest=prepared_digest,
            affected_paths=tuple(entries),
            manifest_digest=manifest_digest,
            created_at=created_at,
            run_id=metadata.get("run_id"),
            turn_id=metadata.get("turn_id"),
            tool_execution_id=metadata.get("tool_execution_id"),
        )
        self._store_manifest(manifest)
        return manifest

    def load(self, checkpoint_id: str) -> CheckpointManifest:
        if not checkpoint_id or Path(checkpoint_id).name != checkpoint_id:
            raise CheckpointError("invalid checkpoint id")
        path = self.artifact_root / "manifests" / f"{checkpoint_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CheckpointError(f"checkpoint {checkpoint_id} was not found") from error
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"checkpoint {checkpoint_id} is unreadable") from error
        try:
            entries = tuple(
                CheckpointEntry(
                    path=str(item["path"]),
                    existed=bool(item["existed"]),
                    mode=int(item["mode"]) if item.get("mode") is not None else None,
                    before_sha256=(
                        str(item["before_sha256"])
                        if item.get("before_sha256") is not None
                        else None
                    ),
                    before_artifact=(
                        str(item["before_artifact"])
                        if item.get("before_artifact") is not None
                        else None
                    ),
                    expected_after_sha256=(
                        str(item["expected_after_sha256"])
                        if item.get("expected_after_sha256") is not None
                        else None
                    ),
                )
                for item in payload["affected_paths"]
            )
            manifest = CheckpointManifest(
                checkpoint_id=str(payload["checkpoint_id"]),
                workspace_revision=str(payload["workspace_revision"]),
                tool_name=str(payload["tool_name"]),
                prepared_digest=str(payload["prepared_digest"]),
                affected_paths=entries,
                manifest_digest=str(payload["manifest_digest"]),
                created_at=float(payload["created_at"]),
                run_id=(
                    str(payload["run_id"])
                    if payload.get("run_id") is not None
                    else None
                ),
                turn_id=(
                    str(payload["turn_id"])
                    if payload.get("turn_id") is not None
                    else None
                ),
                tool_execution_id=(
                    str(payload["tool_execution_id"])
                    if payload.get("tool_execution_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CheckpointError(f"checkpoint {checkpoint_id} is invalid") from error
        if manifest.checkpoint_id != checkpoint_id:
            raise CheckpointError("checkpoint identity does not match its filename")
        if _manifest_digest(manifest) != manifest.manifest_digest:
            raise CheckpointError("checkpoint manifest digest does not match")
        return manifest

    def rewind(
        self,
        *,
        boundary: WorkspaceBoundary,
        checkpoint_id: str,
        expected_after_hashes: Mapping[str, str | None],
    ) -> RewindResult:
        manifest = self.load(checkpoint_id)
        targets: list[tuple[CheckpointEntry, ResolvedPath, str | None]] = []
        conflicts: list[str] = []
        for entry in manifest.affected_paths:
            target = boundary.resolve(
                entry.path,
                must_exist=False,
                for_write=True,
                allow_final_symlink=False,
            )
            current_hash = file_sha256(target.path) if target.path.is_file() else None
            if entry.path not in expected_after_hashes:
                raise CheckpointError(
                    f"checkpoint lacks write-after evidence for {entry.path}"
                )
            if current_hash != expected_after_hashes[entry.path]:
                conflicts.append(entry.path)
            targets.append((entry, target, current_hash))
        if conflicts:
            raise RewindConflict(conflicts)

        restored: list[str] = []
        removed: list[str] = []
        for entry, target, current_hash in targets:
            if entry.existed:
                if entry.before_artifact is None or entry.before_sha256 is None:
                    raise CheckpointError(
                        f"checkpoint lacks a before-image for {entry.path}"
                    )
                blob = (self.artifact_root / entry.before_artifact).resolve()
                try:
                    blob.relative_to(self.artifact_root)
                except ValueError as error:
                    raise CheckpointError("checkpoint blob escapes artifact root") from error
                try:
                    content = blob.read_bytes()
                except OSError as error:
                    raise CheckpointError(
                        f"checkpoint blob for {entry.path} is unreadable"
                    ) from error
                if hashlib.sha256(content).hexdigest() != entry.before_sha256:
                    raise CheckpointError(
                        f"checkpoint blob for {entry.path} failed digest validation"
                    )
                atomic_write_bytes(
                    boundary,
                    target,
                    content,
                    expected_sha256=current_hash,
                    mode=entry.mode,
                )
                restored.append(entry.path)
            else:
                current = boundary.revalidate(target, require_unchanged=True)
                if current.path.exists():
                    current.path.unlink()
                removed.append(entry.path)

        refreshed = tuple(
            boundary.resolve(entry.path, must_exist=False, for_write=True)
            for entry in manifest.affected_paths
        )
        return RewindResult(
            checkpoint_id=checkpoint_id,
            restored_paths=tuple(restored),
            removed_paths=tuple(removed),
            workspace_revision=boundary.revision(*refreshed),
        )

    def _store_file_blob(self, source: Path) -> tuple[str, Path]:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".checkpoint-",
            dir=self.artifact_root,
        )
        temporary = Path(temporary_name)
        hasher = hashlib.sha256()
        try:
            os.fchmod(descriptor, 0o600)
            with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                while chunk := reader.read(64 * 1024):
                    hasher.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            digest = hasher.hexdigest()
            destination = self.artifact_root / "blobs" / digest[:2] / digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            return digest, destination
        finally:
            temporary.unlink(missing_ok=True)

    def _store_manifest(self, manifest: CheckpointManifest) -> None:
        path = self.artifact_root / "manifests" / f"{manifest.checkpoint_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(manifest)
        payload["affected_paths"] = [asdict(entry) for entry in manifest.affected_paths]
        _atomic_store(
            path,
            json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"),
        )


def _manifest_digest(manifest: CheckpointManifest) -> str:
    unsigned = {
        "checkpoint_id": manifest.checkpoint_id,
        "workspace_revision": manifest.workspace_revision,
        "tool_name": manifest.tool_name,
        "prepared_digest": manifest.prepared_digest,
        "affected_paths": [asdict(entry) for entry in manifest.affected_paths],
        "created_at": manifest.created_at,
        "run_id": manifest.run_id,
        "turn_id": manifest.turn_id,
        "tool_execution_id": manifest.tool_execution_id,
    }
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def create_checkpoint(
    service: CheckpointService,
    **kwargs: object,
) -> CheckpointManifest:
    result = service.create(**kwargs)  # type: ignore[arg-type]
    if inspect.isawaitable(result):
        return await result
    return result


def _atomic_store(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

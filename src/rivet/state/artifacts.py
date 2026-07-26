from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from rivet.domain.artifacts import Artifact, RedactionStatus
from rivet.domain.common import require_digest, require_non_empty


class ArtifactStoreError(RuntimeError):
    """Base error for content-addressed artifact operations."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Requested artifact content is absent."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Stored content no longer matches its address."""


class ContentAddressedArtifactStore:
    def __init__(self, root: Path, *, max_bytes: int | None = None) -> None:
        self.root = root.expanduser().resolve(strict=False)
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive or None")
        self.max_bytes = max_bytes

    def initialize(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path_for_digest(self, digest: str) -> Path:
        require_digest(digest, "digest")
        return self.root / digest[:2] / digest

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
        redaction_status: RedactionStatus = RedactionStatus.UNKNOWN,
    ) -> Artifact:
        require_non_empty(media_type, "media_type")
        if self.max_bytes is not None and len(content) > self.max_bytes:
            raise ArtifactStoreError(
                f"artifact has {len(content)} bytes, exceeding limit {self.max_bytes}"
            )
        digest = hashlib.sha256(content).hexdigest()
        destination = self.path_for_digest(digest)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        if destination.exists():
            self._verify_path(destination, digest, len(content))
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{digest}.", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if destination.exists():
                    self._verify_path(destination, digest, len(content))
                else:
                    os.replace(temporary, destination)
                    os.chmod(destination, 0o600)
            finally:
                if temporary.exists():
                    temporary.unlink()

        return Artifact(
            artifact_id=f"art_{digest}",
            sha256=digest,
            media_type=media_type,
            size_bytes=len(content),
            redaction_status=redaction_status,
        )

    def put_text(
        self,
        content: str,
        *,
        media_type: str = "text/plain; charset=utf-8",
        redaction_status: RedactionStatus = RedactionStatus.UNKNOWN,
    ) -> Artifact:
        return self.put_bytes(
            content.encode("utf-8"),
            media_type=media_type,
            redaction_status=redaction_status,
        )

    def read_bytes(self, artifact: Artifact) -> bytes:
        path = self.path_for_digest(artifact.sha256)
        if not path.is_file():
            raise ArtifactNotFoundError(artifact.artifact_id)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.sha256 or len(content) != artifact.size_bytes:
            raise ArtifactIntegrityError(
                f"artifact {artifact.artifact_id} failed size or digest verification"
            )
        return content

    def verify(self, artifact: Artifact) -> bool:
        self.read_bytes(artifact)
        return True

    @staticmethod
    def _verify_path(path: Path, digest: str, expected_size: int) -> None:
        content = path.read_bytes()
        actual_digest = hashlib.sha256(content).hexdigest()
        if len(content) != expected_size or actual_digest != digest:
            raise ArtifactIntegrityError(f"artifact content at {path} is corrupt")

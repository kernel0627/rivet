from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from rivet.domain.artifacts import ArtifactRef
from rivet.domain.common import (
    CURRENT_SCHEMA_VERSION,
    JsonObject,
    datetime_from_text,
    datetime_to_text,
    require_aware,
    require_digest,
    require_identifier,
    require_schema_version,
    utc_now,
)
from rivet.domain.errors import ErrorInfo


class CheckpointStatus(str, Enum):
    CREATING = "CREATING"
    READY = "READY"
    INVALID = "INVALID"
    REWOUND = "REWOUND"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    turn_id: str
    created_before_execution_id: str
    status: CheckpointStatus
    scope: tuple[str, ...]
    workspace_revision: str
    manifest_digest: str | None = None
    artifact_ref: ArtifactRef | None = None
    error: ErrorInfo | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "checkpoint_id",
            "run_id",
            "turn_id",
            "created_before_execution_id",
        ):
            require_identifier(getattr(self, field_name), field_name)
        if not self.scope or any(not path.strip() for path in self.scope):
            raise ValueError("checkpoint scope must contain at least one path")
        if not self.workspace_revision.strip():
            raise ValueError("workspace_revision must not be empty")
        require_digest(self.manifest_digest, "manifest_digest")
        if self.status is CheckpointStatus.READY:
            if self.manifest_digest is None or self.artifact_ref is None:
                raise ValueError("READY checkpoints require manifest_digest and artifact_ref")
            if self.error is not None:
                raise ValueError("READY checkpoints cannot contain an error")
        if self.status is CheckpointStatus.FAILED and self.error is None:
            raise ValueError("FAILED checkpoints require an error")
        require_aware(self.created_at, "created_at")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "created_before_execution_id": self.created_before_execution_id,
            "status": self.status.value,
            "scope": list(self.scope),
            "workspace_revision": self.workspace_revision,
            "manifest_digest": self.manifest_digest,
            "artifact_ref": self.artifact_ref.to_dict() if self.artifact_ref else None,
            "error": self.error.to_dict() if self.error else None,
            "created_at": datetime_to_text(self.created_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Checkpoint:
        artifact = value.get("artifact_ref")
        error = value.get("error")
        return cls(
            checkpoint_id=str(value["checkpoint_id"]),
            run_id=str(value["run_id"]),
            turn_id=str(value["turn_id"]),
            created_before_execution_id=str(value["created_before_execution_id"]),
            status=CheckpointStatus(str(value["status"])),
            scope=tuple(str(item) for item in value.get("scope", [])),
            workspace_revision=str(value["workspace_revision"]),
            manifest_digest=(
                str(value["manifest_digest"]) if value.get("manifest_digest") is not None else None
            ),
            artifact_ref=ArtifactRef.from_dict(dict(artifact)) if artifact is not None else None,
            error=ErrorInfo.from_dict(dict(error)) if error is not None else None,
            created_at=datetime_from_text(str(value["created_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

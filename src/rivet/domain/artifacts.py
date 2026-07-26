from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from rivet.domain.common import (
    CURRENT_SCHEMA_VERSION,
    JsonObject,
    datetime_from_text,
    datetime_to_text,
    require_aware,
    require_digest,
    require_identifier,
    require_non_empty,
    require_schema_version,
    utc_now,
)


class RedactionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    CLEAN = "CLEAN"
    REDACTED = "REDACTED"
    CONTAINS_SENSITIVE = "CONTAINS_SENSITIVE"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    sha256: str
    media_type: str
    size_bytes: int
    redaction_status: RedactionStatus = RedactionStatus.UNKNOWN
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.artifact_id, "artifact_id")
        require_digest(self.sha256, "sha256")
        require_non_empty(self.media_type, "media_type")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "redaction_status": self.redaction_status.value,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArtifactRef:
        return cls(
            artifact_id=str(value["artifact_id"]),
            sha256=str(value["sha256"]),
            media_type=str(value["media_type"]),
            size_bytes=int(value["size_bytes"]),
            redaction_status=RedactionStatus(
                str(value.get("redaction_status", RedactionStatus.UNKNOWN.value))
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    sha256: str
    media_type: str
    size_bytes: int
    redaction_status: RedactionStatus = RedactionStatus.UNKNOWN
    created_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.artifact_id, "artifact_id")
        require_digest(self.sha256, "sha256")
        require_non_empty(self.media_type, "media_type")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        require_aware(self.created_at, "created_at")
        require_schema_version(self.schema_version)

    def as_ref(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            sha256=self.sha256,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            redaction_status=self.redaction_status,
            schema_version=self.schema_version,
        )

    def to_dict(self) -> JsonObject:
        return {
            **self.as_ref().to_dict(),
            "created_at": datetime_to_text(self.created_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Artifact:
        return cls(
            artifact_id=str(value["artifact_id"]),
            sha256=str(value["sha256"]),
            media_type=str(value["media_type"]),
            size_bytes=int(value["size_bytes"]),
            redaction_status=RedactionStatus(
                str(value.get("redaction_status", RedactionStatus.UNKNOWN.value))
            ),
            created_at=datetime_from_text(str(value["created_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

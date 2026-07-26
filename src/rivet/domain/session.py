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
    freeze_json_object,
    new_id,
    require_aware,
    require_identifier,
    require_schema_version,
    utc_now,
)


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    workspace_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.workspace_id, "workspace_id")
        object.__setattr__(self, "metadata", freeze_json_object(self.metadata, "metadata"))
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        require_schema_version(self.schema_version)

    @classmethod
    def create(
        cls,
        workspace_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Session:
        return cls(
            session_id=new_id("ses"),
            workspace_id=workspace_id,
            metadata=metadata or {},
        )

    def to_dict(self) -> JsonObject:
        return {
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "status": self.status.value,
            "metadata": dict(self.metadata),
            "created_at": datetime_to_text(self.created_at),
            "updated_at": datetime_to_text(self.updated_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Session:
        return cls(
            session_id=str(value["session_id"]),
            workspace_id=str(value["workspace_id"]),
            status=SessionStatus(str(value.get("status", SessionStatus.ACTIVE.value))),
            metadata=dict(value.get("metadata", {})),
            created_at=datetime_from_text(str(value["created_at"])),
            updated_at=datetime_from_text(str(value["updated_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

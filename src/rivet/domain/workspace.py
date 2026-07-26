from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from rivet.domain.common import (
    CURRENT_SCHEMA_VERSION,
    JsonObject,
    datetime_from_text,
    datetime_to_text,
    freeze_json_object,
    require_aware,
    require_identifier,
    require_non_empty,
    require_schema_version,
    utc_now,
    workspace_id_for,
)


class RepositoryType(str, Enum):
    GIT = "GIT"
    PLAIN = "PLAIN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: str
    canonical_root: str
    display_name: str
    repository_type: RepositoryType
    base_revision: str
    current_revision: str
    configuration: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.workspace_id, "workspace_id")
        root = Path(self.canonical_root)
        if not root.is_absolute():
            raise ValueError("canonical_root must be absolute")
        if str(root) != str(root.resolve(strict=False)):
            raise ValueError("canonical_root must be normalized")
        require_non_empty(self.display_name, "display_name")
        require_non_empty(self.base_revision, "base_revision")
        require_non_empty(self.current_revision, "current_revision")
        object.__setattr__(
            self,
            "configuration",
            freeze_json_object(self.configuration, "configuration"),
        )
        require_aware(self.created_at, "created_at")
        require_schema_version(self.schema_version)

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        display_name: str | None = None,
        repository_type: RepositoryType = RepositoryType.UNKNOWN,
        repository_identity: str | None = None,
        base_revision: str = "unversioned",
        current_revision: str | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Workspace:
        canonical = root.expanduser().resolve(strict=False)
        return cls(
            workspace_id=workspace_id_for(canonical, repository_identity),
            canonical_root=str(canonical),
            display_name=display_name or canonical.name or str(canonical),
            repository_type=repository_type,
            base_revision=base_revision,
            current_revision=current_revision or base_revision,
            configuration=configuration or {},
        )

    def to_dict(self) -> JsonObject:
        return {
            "workspace_id": self.workspace_id,
            "canonical_root": self.canonical_root,
            "display_name": self.display_name,
            "repository_type": self.repository_type.value,
            "base_revision": self.base_revision,
            "current_revision": self.current_revision,
            "configuration": dict(self.configuration),
            "created_at": datetime_to_text(self.created_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Workspace:
        return cls(
            workspace_id=str(value["workspace_id"]),
            canonical_root=str(value["canonical_root"]),
            display_name=str(value["display_name"]),
            repository_type=RepositoryType(str(value["repository_type"])),
            base_revision=str(value["base_revision"]),
            current_revision=str(value["current_revision"]),
            configuration=dict(value.get("configuration", {})),
            created_at=datetime_from_text(str(value["created_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

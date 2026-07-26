from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rivet.domain.common import (
    CURRENT_SCHEMA_VERSION,
    JsonObject,
    freeze_json_object,
    require_non_empty,
    require_schema_version,
)


class ErrorKind(str, Enum):
    MODEL_TRANSPORT_ERROR = "MODEL_TRANSPORT_ERROR"
    MODEL_PROTOCOL_ERROR = "MODEL_PROTOCOL_ERROR"
    MODEL_RATE_LIMIT = "MODEL_RATE_LIMIT"
    MODEL_AUTH_ERROR = "MODEL_AUTH_ERROR"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_ARGUMENT_ERROR = "TOOL_ARGUMENT_ERROR"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_CANCELLED = "TOOL_CANCELLED"
    WORKSPACE_VIOLATION = "WORKSPACE_VIOLATION"
    WORKSPACE_CHANGED = "WORKSPACE_CHANGED"
    STORE_ERROR = "STORE_ERROR"
    STATE_CONFLICT = "STATE_CONFLICT"
    CHECKPOINT_ERROR = "CHECKPOINT_ERROR"
    REWIND_CONFLICT = "REWIND_CONFLICT"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    USER_CANCELLED = "USER_CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    kind: ErrorKind
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)
    artifact_id: str | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_non_empty(self.message, "message")
        object.__setattr__(self, "details", freeze_json_object(self.details, "details"))
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
            "artifact_id": self.artifact_id,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ErrorInfo:
        return cls(
            kind=ErrorKind(str(value["kind"])),
            message=str(value["message"]),
            retryable=bool(value.get("retryable", False)),
            details=dict(value.get("details", {})),
            artifact_id=(
                str(value["artifact_id"]) if value.get("artifact_id") is not None else None
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

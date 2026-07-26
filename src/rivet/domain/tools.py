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
    require_aware,
    require_digest,
    require_identifier,
    require_non_empty,
    require_schema_version,
)
from rivet.domain.errors import ErrorInfo


class EffectClass(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    NETWORK = "NETWORK"


class PermissionDecision(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DENIED = "DENIED"


class SideEffectState(str, Enum):
    NONE = "NONE"
    NOT_STARTED = "NOT_STARTED"
    APPLIED = "APPLIED"
    PARTIAL = "PARTIAL"
    UNCERTAIN = "UNCERTAIN"
    REVERTED = "REVERTED"


class ToolExecutionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    PREPARED = "PREPARED"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.DENIED,
            self.TIMED_OUT,
            self.CANCELLED,
            self.INTERRUPTED,
        }


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    execution_id: str
    turn_id: str
    model_call_id: str
    tool_call_id: str
    ordinal: int
    attempt_no: int
    tool_name: str
    tool_version: str
    status: ToolExecutionStatus
    normalized_arguments: Mapping[str, Any] = field(default_factory=dict)
    effect_class: EffectClass = EffectClass.READ
    permission_decision: PermissionDecision = PermissionDecision.NOT_REQUIRED
    prepared_digest: str | None = None
    retry_of: str | None = None
    checkpoint_id: str | None = None
    result_summary: Mapping[str, Any] | None = None
    result_artifact_ids: tuple[str, ...] = ()
    error: ErrorInfo | None = None
    side_effect_state: SideEffectState = SideEffectState.NONE
    workspace_revision_before: str | None = None
    workspace_revision_after: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "execution_id",
            "turn_id",
            "model_call_id",
            "tool_call_id",
        ):
            require_identifier(getattr(self, field_name), field_name)
        if self.retry_of is not None:
            require_identifier(self.retry_of, "retry_of")
            if self.retry_of == self.execution_id:
                raise ValueError("retry_of cannot equal execution_id")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if self.attempt_no < 1:
            raise ValueError("attempt_no must be at least one")
        require_non_empty(self.tool_name, "tool_name")
        require_non_empty(self.tool_version, "tool_version")
        object.__setattr__(
            self,
            "normalized_arguments",
            freeze_json_object(self.normalized_arguments, "normalized_arguments"),
        )
        require_digest(self.prepared_digest, "prepared_digest")
        if self.result_summary is not None:
            object.__setattr__(
                self,
                "result_summary",
                freeze_json_object(self.result_summary, "result_summary"),
            )
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
        if self.ended_at is not None:
            require_aware(self.ended_at, "ended_at")
            if self.started_at is None:
                raise ValueError("ended_at requires started_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at cannot be before started_at")
        prepared_statuses = {
            ToolExecutionStatus.PREPARED,
            ToolExecutionStatus.WAITING_PERMISSION,
            ToolExecutionStatus.READY,
            ToolExecutionStatus.RUNNING,
            ToolExecutionStatus.SUCCEEDED,
            ToolExecutionStatus.DENIED,
            ToolExecutionStatus.TIMED_OUT,
            ToolExecutionStatus.INTERRUPTED,
        }
        if self.status in prepared_statuses and self.prepared_digest is None:
            raise ValueError(f"{self.status.value} tool executions require prepared_digest")
        if self.status is ToolExecutionStatus.RUNNING and self.started_at is None:
            raise ValueError("RUNNING tool executions require started_at")
        if self.status.terminal and self.ended_at is None:
            raise ValueError(f"{self.status.value} tool executions require ended_at")
        if not self.status.terminal and self.ended_at is not None:
            raise ValueError("non-terminal tool executions cannot have ended_at")
        if self.status is ToolExecutionStatus.SUCCEEDED and self.error is not None:
            raise ValueError("SUCCEEDED tool executions cannot contain an error")
        if (
            self.status
            in {
                ToolExecutionStatus.FAILED,
                ToolExecutionStatus.TIMED_OUT,
                ToolExecutionStatus.INTERRUPTED,
            }
            and self.error is None
        ):
            raise ValueError(f"{self.status.value} tool executions require an error")
        if self.status is ToolExecutionStatus.DENIED:
            if self.permission_decision is not PermissionDecision.DENIED:
                raise ValueError("DENIED status requires a DENIED permission decision")
        started_write_statuses = {
            ToolExecutionStatus.READY,
            ToolExecutionStatus.RUNNING,
            ToolExecutionStatus.SUCCEEDED,
            ToolExecutionStatus.TIMED_OUT,
            ToolExecutionStatus.INTERRUPTED,
        }
        write_may_have_effect = self.status is ToolExecutionStatus.FAILED and (
            self.side_effect_state
            in {
                SideEffectState.APPLIED,
                SideEffectState.PARTIAL,
                SideEffectState.UNCERTAIN,
            }
        )
        if self.effect_class is EffectClass.WRITE and (
            self.status in started_write_statuses or write_may_have_effect
        ):
            if self.permission_decision is not PermissionDecision.GRANTED:
                raise ValueError("write execution requires granted permission")
            if self.checkpoint_id is None:
                raise ValueError("write execution requires a checkpoint")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "execution_id": self.execution_id,
            "turn_id": self.turn_id,
            "model_call_id": self.model_call_id,
            "tool_call_id": self.tool_call_id,
            "ordinal": self.ordinal,
            "attempt_no": self.attempt_no,
            "retry_of": self.retry_of,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "normalized_arguments": dict(self.normalized_arguments),
            "effect_class": self.effect_class.value,
            "permission_decision": self.permission_decision.value,
            "prepared_digest": self.prepared_digest,
            "status": self.status.value,
            "checkpoint_id": self.checkpoint_id,
            "result_summary": (
                dict(self.result_summary) if self.result_summary is not None else None
            ),
            "result_artifact_ids": list(self.result_artifact_ids),
            "error": self.error.to_dict() if self.error else None,
            "side_effect_state": self.side_effect_state.value,
            "workspace_revision_before": self.workspace_revision_before,
            "workspace_revision_after": self.workspace_revision_after,
            "started_at": datetime_to_text(self.started_at),
            "ended_at": datetime_to_text(self.ended_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolExecutionRecord:
        error = value.get("error")
        summary = value.get("result_summary")
        return cls(
            execution_id=str(value["execution_id"]),
            turn_id=str(value["turn_id"]),
            model_call_id=str(value["model_call_id"]),
            tool_call_id=str(value["tool_call_id"]),
            ordinal=int(value["ordinal"]),
            attempt_no=int(value["attempt_no"]),
            retry_of=str(value["retry_of"]) if value.get("retry_of") is not None else None,
            tool_name=str(value["tool_name"]),
            tool_version=str(value["tool_version"]),
            normalized_arguments=dict(value.get("normalized_arguments", {})),
            effect_class=EffectClass(str(value.get("effect_class", EffectClass.READ.value))),
            permission_decision=PermissionDecision(
                str(value.get("permission_decision", PermissionDecision.NOT_REQUIRED.value))
            ),
            prepared_digest=(
                str(value["prepared_digest"]) if value.get("prepared_digest") is not None else None
            ),
            status=ToolExecutionStatus(str(value["status"])),
            checkpoint_id=(
                str(value["checkpoint_id"]) if value.get("checkpoint_id") is not None else None
            ),
            result_summary=dict(summary) if summary is not None else None,
            result_artifact_ids=tuple(str(item) for item in value.get("result_artifact_ids", [])),
            error=ErrorInfo.from_dict(dict(error)) if error is not None else None,
            side_effect_state=SideEffectState(
                str(value.get("side_effect_state", SideEffectState.NONE.value))
            ),
            workspace_revision_before=(
                str(value["workspace_revision_before"])
                if value.get("workspace_revision_before") is not None
                else None
            ),
            workspace_revision_after=(
                str(value["workspace_revision_after"])
                if value.get("workspace_revision_after") is not None
                else None
            ),
            started_at=datetime_from_text(
                str(value["started_at"]) if value.get("started_at") is not None else None
            ),
            ended_at=datetime_from_text(
                str(value["ended_at"]) if value.get("ended_at") is not None else None
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

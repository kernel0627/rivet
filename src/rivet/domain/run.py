from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
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
    require_non_empty,
    require_schema_version,
    utc_now,
)


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class StopAction(str, Enum):
    CONTINUE = "CONTINUE"
    COMPLETE = "COMPLETE"
    PAUSE = "PAUSE"
    FAIL = "FAIL"
    CANCEL = "CANCEL"


class StopScope(str, Enum):
    TURN = "TURN"
    RUN = "RUN"
    SESSION = "SESSION"


@dataclass(frozen=True, slots=True)
class StopDecision:
    action: StopAction
    reason: str
    scope: StopScope = StopScope.RUN
    resumable: bool = False
    resume_requirements: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    user_message: str | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_non_empty(self.reason, "reason")
        object.__setattr__(self, "evidence", freeze_json_object(self.evidence, "evidence"))
        if self.action is StopAction.PAUSE and not self.resumable:
            raise ValueError("PAUSE decisions must be resumable")
        if self.resumable and not self.resume_requirements:
            raise ValueError("resumable decisions require resume_requirements")
        terminal_actions = {StopAction.COMPLETE, StopAction.FAIL, StopAction.CANCEL}
        if self.action in terminal_actions and self.resumable:
            raise ValueError(f"{self.action.value} decisions cannot be resumable")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "scope": self.scope.value,
            "resumable": self.resumable,
            "resume_requirements": list(self.resume_requirements),
            "evidence": dict(self.evidence),
            "user_message": self.user_message,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StopDecision:
        return cls(
            action=StopAction(str(value["action"])),
            reason=str(value["reason"]),
            scope=StopScope(str(value.get("scope", StopScope.RUN.value))),
            resumable=bool(value.get("resumable", False)),
            resume_requirements=tuple(str(item) for item in value.get("resume_requirements", [])),
            evidence=dict(value.get("evidence", {})),
            user_message=(
                str(value["user_message"]) if value.get("user_message") is not None else None
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_turns: int | None = 64
    max_model_calls: int | None = 128
    max_tool_executions: int | None = 256
    max_input_tokens: int | None = 1_000_000
    max_output_tokens: int | None = 250_000
    max_cost_usd: float | None = None
    max_wall_time_seconds: float | None = 3_600.0
    max_command_time_seconds: float | None = 300.0
    max_artifact_bytes: int | None = 1_000_000_000
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "max_turns",
            "max_model_calls",
            "max_tool_executions",
            "max_input_tokens",
            "max_output_tokens",
            "max_artifact_bytes",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"{field_name} must be positive or None")
        for field_name in (
            "max_cost_usd",
            "max_wall_time_seconds",
            "max_command_time_seconds",
        ):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"{field_name} must be positive or None")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "max_turns": self.max_turns,
            "max_model_calls": self.max_model_calls,
            "max_tool_executions": self.max_tool_executions,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_command_time_seconds": self.max_command_time_seconds,
            "max_artifact_bytes": self.max_artifact_bytes,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunBudget:
        def optional_int(name: str, default: int | None) -> int | None:
            item = value.get(name, default)
            return None if item is None else int(item)

        def optional_float(name: str, default: float | None) -> float | None:
            item = value.get(name, default)
            return None if item is None else float(item)

        return cls(
            max_turns=optional_int("max_turns", 64),
            max_model_calls=optional_int("max_model_calls", 128),
            max_tool_executions=optional_int("max_tool_executions", 256),
            max_input_tokens=optional_int("max_input_tokens", 1_000_000),
            max_output_tokens=optional_int("max_output_tokens", 250_000),
            max_cost_usd=optional_float("max_cost_usd", None),
            max_wall_time_seconds=optional_float("max_wall_time_seconds", 3_600.0),
            max_command_time_seconds=optional_float("max_command_time_seconds", 300.0),
            max_artifact_bytes=optional_int("max_artifact_bytes", 1_000_000_000),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class RunUsage:
    turns: int = 0
    model_calls: int = 0
    tool_executions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    wall_time_seconds: float = 0.0
    artifact_bytes: int = 0
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "turns",
            "model_calls",
            "tool_executions",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_seconds",
            "artifact_bytes",
        ):
            value = getattr(self, field_name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "turns": self.turns,
            "model_calls": self.model_calls,
            "tool_executions": self.tool_executions,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "wall_time_seconds": self.wall_time_seconds,
            "artifact_bytes": self.artifact_bytes,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunUsage:
        return cls(
            turns=int(value.get("turns", 0)),
            model_calls=int(value.get("model_calls", 0)),
            tool_executions=int(value.get("tool_executions", 0)),
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            cost_usd=float(value.get("cost_usd", 0.0)),
            wall_time_seconds=float(value.get("wall_time_seconds", 0.0)),
            artifact_bytes=int(value.get("artifact_bytes", 0)),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

    def exceeded(self, budget: RunBudget) -> tuple[str, ...]:
        pairs = (
            ("turns", self.turns, budget.max_turns),
            ("model_calls", self.model_calls, budget.max_model_calls),
            ("tool_executions", self.tool_executions, budget.max_tool_executions),
            ("input_tokens", self.input_tokens, budget.max_input_tokens),
            ("output_tokens", self.output_tokens, budget.max_output_tokens),
            ("cost_usd", self.cost_usd, budget.max_cost_usd),
            ("wall_time_seconds", self.wall_time_seconds, budget.max_wall_time_seconds),
            ("artifact_bytes", self.artifact_bytes, budget.max_artifact_bytes),
        )
        return tuple(name for name, used, limit in pairs if limit is not None and used >= limit)


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    session_id: str
    objective: str
    workspace_base_revision: str
    workspace_current_revision: str
    status: RunStatus = RunStatus.CREATED
    active_turn_id: str | None = None
    config_snapshot: Mapping[str, Any] = field(default_factory=dict)
    budget: RunBudget = field(default_factory=RunBudget)
    usage: RunUsage = field(default_factory=RunUsage)
    working_memory_ref: str | None = None
    stop_decision: StopDecision | None = None
    pause_token: str | None = None
    resume_cursor: str | None = None
    final_response: str | None = None
    revision: int = 0
    parent_run_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.run_id, "run_id")
        require_identifier(self.session_id, "session_id")
        if self.parent_run_id is not None:
            require_identifier(self.parent_run_id, "parent_run_id")
            if self.parent_run_id == self.run_id:
                raise ValueError("parent_run_id cannot equal run_id")
        require_non_empty(self.objective, "objective")
        require_non_empty(self.workspace_base_revision, "workspace_base_revision")
        require_non_empty(self.workspace_current_revision, "workspace_current_revision")
        object.__setattr__(
            self,
            "config_snapshot",
            freeze_json_object(self.config_snapshot, "config_snapshot"),
        )
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        if self.status.terminal and self.active_turn_id is not None:
            raise ValueError("terminal runs cannot have an active_turn_id")
        if self.status is RunStatus.COMPLETED:
            if not self.final_response or not self.final_response.strip():
                raise ValueError("COMPLETED runs require a final_response")
            if self.stop_decision is None or self.stop_decision.action is not StopAction.COMPLETE:
                raise ValueError("COMPLETED runs require a COMPLETE stop decision")
        if self.status is RunStatus.PAUSED:
            if not self.pause_token or not self.resume_cursor:
                raise ValueError("PAUSED runs require pause_token and resume_cursor")
            if self.stop_decision is None or self.stop_decision.action is not StopAction.PAUSE:
                raise ValueError("PAUSED runs require a PAUSE stop decision")
        require_schema_version(self.schema_version)

    @classmethod
    def create(
        cls,
        session_id: str,
        objective: str,
        workspace_revision: str,
        *,
        budget: RunBudget | None = None,
        config_snapshot: Mapping[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> Run:
        return cls(
            run_id=new_id("run"),
            session_id=session_id,
            objective=objective,
            workspace_base_revision=workspace_revision,
            workspace_current_revision=workspace_revision,
            budget=budget or RunBudget(),
            config_snapshot=config_snapshot or {},
            parent_run_id=parent_run_id,
        )

    def to_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "status": self.status.value,
            "active_turn_id": self.active_turn_id,
            "config_snapshot": dict(self.config_snapshot),
            "budget": self.budget.to_dict(),
            "usage": self.usage.to_dict(),
            "working_memory_ref": self.working_memory_ref,
            "workspace_base_revision": self.workspace_base_revision,
            "workspace_current_revision": self.workspace_current_revision,
            "stop_decision": self.stop_decision.to_dict() if self.stop_decision else None,
            "pause_token": self.pause_token,
            "resume_cursor": self.resume_cursor,
            "final_response": self.final_response,
            "revision": self.revision,
            "parent_run_id": self.parent_run_id,
            "created_at": datetime_to_text(self.created_at),
            "updated_at": datetime_to_text(self.updated_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Run:
        stop = value.get("stop_decision")
        return cls(
            run_id=str(value["run_id"]),
            session_id=str(value["session_id"]),
            objective=str(value["objective"]),
            workspace_base_revision=str(value["workspace_base_revision"]),
            workspace_current_revision=str(value["workspace_current_revision"]),
            status=RunStatus(str(value.get("status", RunStatus.CREATED.value))),
            active_turn_id=(
                str(value["active_turn_id"]) if value.get("active_turn_id") is not None else None
            ),
            config_snapshot=dict(value.get("config_snapshot", {})),
            budget=RunBudget.from_dict(dict(value.get("budget", {}))),
            usage=RunUsage.from_dict(dict(value.get("usage", {}))),
            working_memory_ref=(
                str(value["working_memory_ref"])
                if value.get("working_memory_ref") is not None
                else None
            ),
            stop_decision=StopDecision.from_dict(dict(stop)) if stop is not None else None,
            pause_token=(
                str(value["pause_token"]) if value.get("pause_token") is not None else None
            ),
            resume_cursor=(
                str(value["resume_cursor"]) if value.get("resume_cursor") is not None else None
            ),
            final_response=(
                str(value["final_response"]) if value.get("final_response") is not None else None
            ),
            revision=int(value.get("revision", 0)),
            parent_run_id=(
                str(value["parent_run_id"]) if value.get("parent_run_id") is not None else None
            ),
            created_at=datetime_from_text(str(value["created_at"])),
            updated_at=datetime_from_text(str(value["updated_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


_ALLOWED_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PAUSED,
            RunStatus.RECOVERING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.PAUSED: frozenset(
        {RunStatus.RUNNING, RunStatus.RECOVERING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RECOVERING: frozenset(
        {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def validate_run_transition(previous: Run, current: Run) -> None:
    if previous.run_id != current.run_id:
        raise ValueError("run transition must keep run_id")
    if previous.session_id != current.session_id:
        raise ValueError("run transition must keep session_id")
    immutable_fields = (
        "objective",
        "workspace_base_revision",
        "parent_run_id",
        "created_at",
        "schema_version",
    )
    for field_name in immutable_fields:
        if getattr(previous, field_name) != getattr(current, field_name):
            raise ValueError(f"run transition must keep {field_name}")
    if current.revision != previous.revision + 1:
        raise ValueError("run revision must increase by exactly one")
    if current.status is previous.status:
        return
    if current.status not in _ALLOWED_RUN_TRANSITIONS[previous.status]:
        raise ValueError(
            f"invalid run status transition {previous.status.value} -> {current.status.value}"
        )

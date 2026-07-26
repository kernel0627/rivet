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
    new_id,
    require_aware,
    require_identifier,
    require_schema_version,
    utc_now,
)


class TurnStatus(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class TurnPhase(str, Enum):
    BUILD_CONTEXT = "BUILD_CONTEXT"
    CALL_MODEL = "CALL_MODEL"
    PREPARE_TOOLS = "PREPARE_TOOLS"
    WAIT_PERMISSION = "WAIT_PERMISSION"
    EXECUTE_TOOLS = "EXECUTE_TOOLS"
    DECIDE = "DECIDE"


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: str
    run_id: str
    ordinal: int
    status: TurnStatus = TurnStatus.CREATED
    phase: TurnPhase = TurnPhase.BUILD_CONTEXT
    context_id: str | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.turn_id, "turn_id")
        require_identifier(self.run_id, "run_id")
        if self.ordinal < 1:
            raise ValueError("ordinal must be at least one")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        require_aware(self.created_at, "created_at")
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
            if self.started_at < self.created_at:
                raise ValueError("started_at cannot be before created_at")
        if self.ended_at is not None:
            require_aware(self.ended_at, "ended_at")
            if self.started_at is None:
                raise ValueError("ended_at requires started_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at cannot be before started_at")
        if self.status in {TurnStatus.ACTIVE, TurnStatus.WAITING} and self.started_at is None:
            raise ValueError(f"{self.status.value} turns require started_at")
        if self.status.terminal and self.ended_at is None:
            raise ValueError(f"{self.status.value} turns require ended_at")
        if not self.status.terminal and self.ended_at is not None:
            raise ValueError("non-terminal turns cannot have ended_at")
        if self.status is TurnStatus.WAITING and self.phase is not TurnPhase.WAIT_PERMISSION:
            raise ValueError("WAITING turns must be in WAIT_PERMISSION phase")
        require_schema_version(self.schema_version)

    @classmethod
    def create(cls, run_id: str, ordinal: int) -> Turn:
        return cls(turn_id=new_id("turn"), run_id=run_id, ordinal=ordinal)

    def to_dict(self) -> JsonObject:
        return {
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "ordinal": self.ordinal,
            "status": self.status.value,
            "phase": self.phase.value,
            "context_id": self.context_id,
            "revision": self.revision,
            "created_at": datetime_to_text(self.created_at),
            "started_at": datetime_to_text(self.started_at),
            "ended_at": datetime_to_text(self.ended_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Turn:
        return cls(
            turn_id=str(value["turn_id"]),
            run_id=str(value["run_id"]),
            ordinal=int(value["ordinal"]),
            status=TurnStatus(str(value.get("status", TurnStatus.CREATED.value))),
            phase=TurnPhase(str(value.get("phase", TurnPhase.BUILD_CONTEXT.value))),
            context_id=(str(value["context_id"]) if value.get("context_id") is not None else None),
            revision=int(value.get("revision", 0)),
            created_at=datetime_from_text(str(value["created_at"])),
            started_at=datetime_from_text(
                str(value["started_at"]) if value.get("started_at") is not None else None
            ),
            ended_at=datetime_from_text(
                str(value["ended_at"]) if value.get("ended_at") is not None else None
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


_ALLOWED_TURN_TRANSITIONS: dict[TurnStatus, frozenset[TurnStatus]] = {
    TurnStatus.CREATED: frozenset({TurnStatus.ACTIVE, TurnStatus.FAILED, TurnStatus.CANCELLED}),
    TurnStatus.ACTIVE: frozenset(
        {
            TurnStatus.WAITING,
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }
    ),
    TurnStatus.WAITING: frozenset({TurnStatus.ACTIVE, TurnStatus.FAILED, TurnStatus.CANCELLED}),
    TurnStatus.COMPLETED: frozenset(),
    TurnStatus.FAILED: frozenset(),
    TurnStatus.CANCELLED: frozenset(),
}


def validate_turn_transition(previous: Turn, current: Turn) -> None:
    if previous.turn_id != current.turn_id or previous.run_id != current.run_id:
        raise ValueError("turn transition must keep identity")
    if previous.ordinal != current.ordinal or previous.created_at != current.created_at:
        raise ValueError("turn transition must keep ordinal and created_at")
    if current.revision != previous.revision + 1:
        raise ValueError("turn revision must increase by exactly one")
    if current.status is previous.status:
        return
    if current.status not in _ALLOWED_TURN_TRANSITIONS[previous.status]:
        raise ValueError(
            f"invalid turn status transition {previous.status.value} -> {current.status.value}"
        )

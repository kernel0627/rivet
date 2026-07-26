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
    require_non_empty,
    require_schema_version,
    utc_now,
)


class EventActor(str, Enum):
    USER = "USER"
    RUNTIME = "RUNTIME"
    MODEL = "MODEL"
    TOOL = "TOOL"
    VERIFIER = "VERIFIER"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    session_id: str
    run_id: str
    sequence: int
    event_type: str
    actor: EventActor
    payload: Mapping[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("event_id", "session_id", "run_id"):
            require_identifier(getattr(self, field_name), field_name)
        for field_name in ("turn_id", "causation_id", "correlation_id"):
            value = getattr(self, field_name)
            if value is not None:
                require_identifier(value, field_name)
        if self.sequence < 1:
            raise ValueError("event sequence must be at least one")
        require_non_empty(self.event_type, "event_type")
        object.__setattr__(self, "payload", freeze_json_object(self.payload, "payload"))
        require_aware(self.occurred_at, "occurred_at")
        require_schema_version(self.schema_version)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        run_id: str,
        sequence: int,
        event_type: str,
        actor: EventActor,
        payload: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        return cls(
            event_id=new_id("evt"),
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            causation_id=causation_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )

    def to_dict(self) -> JsonObject:
        return {
            "event_id": self.event_id,
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor": self.actor.value,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "occurred_at": datetime_to_text(self.occurred_at),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Event:
        return cls(
            event_id=str(value["event_id"]),
            session_id=str(value["session_id"]),
            run_id=str(value["run_id"]),
            turn_id=str(value["turn_id"]) if value.get("turn_id") is not None else None,
            sequence=int(value["sequence"]),
            event_type=str(value["event_type"]),
            actor=EventActor(str(value["actor"])),
            causation_id=(
                str(value["causation_id"]) if value.get("causation_id") is not None else None
            ),
            correlation_id=(
                str(value["correlation_id"]) if value.get("correlation_id") is not None else None
            ),
            occurred_at=datetime_from_text(str(value["occurred_at"])),
            payload=dict(value.get("payload", {})),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


EventEnvelope = Event

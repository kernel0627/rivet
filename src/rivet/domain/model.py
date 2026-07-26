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
    require_aware,
    require_digest,
    require_identifier,
    require_non_empty,
    require_schema_version,
)
from rivet.domain.errors import ErrorInfo


class ModelCallStatus(str, Enum):
    CREATED = "CREATED"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.INTERRUPTED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "cost_usd",
        ):
            value = getattr(self, field_name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelUsage:
        return cls(
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            cached_input_tokens=int(value.get("cached_input_tokens", 0)),
            reasoning_tokens=int(value.get("reasoning_tokens", 0)),
            cost_usd=float(value.get("cost_usd", 0.0)),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    model_call_id: str
    turn_id: str
    attempt_no: int
    provider: str
    model: str
    status: ModelCallStatus
    context_id: str
    request_digest: str
    normalized_response: Mapping[str, Any] | None = None
    response_artifact_id: str | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: ErrorInfo | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.model_call_id, "model_call_id")
        require_identifier(self.turn_id, "turn_id")
        require_identifier(self.context_id, "context_id")
        if self.attempt_no < 1:
            raise ValueError("attempt_no must be at least one")
        require_non_empty(self.provider, "provider")
        require_non_empty(self.model, "model")
        require_digest(self.request_digest, "request_digest")
        if self.normalized_response is not None:
            object.__setattr__(
                self,
                "normalized_response",
                freeze_json_object(self.normalized_response, "normalized_response"),
            )
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
        if self.ended_at is not None:
            require_aware(self.ended_at, "ended_at")
            if self.started_at is None:
                raise ValueError("ended_at requires started_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at cannot be before started_at")
        if self.status is ModelCallStatus.IN_FLIGHT and self.started_at is None:
            raise ValueError("IN_FLIGHT model calls require started_at")
        if self.status.terminal and self.ended_at is None:
            raise ValueError(f"{self.status.value} model calls require ended_at")
        if not self.status.terminal and self.ended_at is not None:
            raise ValueError("non-terminal model calls cannot have ended_at")
        if self.status is ModelCallStatus.SUCCEEDED:
            if self.normalized_response is None and self.response_artifact_id is None:
                raise ValueError("SUCCEEDED model calls require a normalized response or artifact")
            if self.error is not None:
                raise ValueError("SUCCEEDED model calls cannot contain an error")
        if self.status is ModelCallStatus.FAILED and self.error is None:
            raise ValueError("FAILED model calls require an error")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "model_call_id": self.model_call_id,
            "turn_id": self.turn_id,
            "attempt_no": self.attempt_no,
            "provider": self.provider,
            "model": self.model,
            "status": self.status.value,
            "context_id": self.context_id,
            "request_digest": self.request_digest,
            "normalized_response": (
                dict(self.normalized_response) if self.normalized_response is not None else None
            ),
            "response_artifact_id": self.response_artifact_id,
            "usage": self.usage.to_dict(),
            "error": self.error.to_dict() if self.error else None,
            "started_at": datetime_to_text(self.started_at),
            "ended_at": datetime_to_text(self.ended_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelCallRecord:
        error = value.get("error")
        response = value.get("normalized_response")
        return cls(
            model_call_id=str(value["model_call_id"]),
            turn_id=str(value["turn_id"]),
            attempt_no=int(value["attempt_no"]),
            provider=str(value["provider"]),
            model=str(value["model"]),
            status=ModelCallStatus(str(value["status"])),
            context_id=str(value["context_id"]),
            request_digest=str(value["request_digest"]),
            normalized_response=dict(response) if response is not None else None,
            response_artifact_id=(
                str(value["response_artifact_id"])
                if value.get("response_artifact_id") is not None
                else None
            ),
            usage=ModelUsage.from_dict(dict(value.get("usage", {}))),
            error=ErrorInfo.from_dict(dict(error)) if error is not None else None,
            started_at=datetime_from_text(
                str(value["started_at"]) if value.get("started_at") is not None else None
            ),
            ended_at=datetime_from_text(
                str(value["ended_at"]) if value.get("ended_at") is not None else None
            ),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

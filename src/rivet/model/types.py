from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _json_object(value: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    """Return a detached JSON object or raise a useful contract error."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain only JSON values") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return decoded


class MessageRole(str, Enum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ToolProposal:
    """A complete, syntactically valid tool proposal produced by a model."""

    tool_call_id: str
    ordinal: int
    name: str
    raw_arguments: str = "{}"

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("tool_call_id must not be empty")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if not self.name.strip():
            raise ValueError("tool proposal name must not be empty")
        try:
            arguments = json.loads(self.raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"tool proposal {self.name!r} has invalid JSON arguments") from exc
        if not isinstance(arguments, dict):
            raise ValueError(f"tool proposal {self.name!r} arguments must be a JSON object")
        _json_object(arguments, field_name="tool proposal arguments")

    @classmethod
    def from_arguments(
        cls,
        *,
        tool_call_id: str,
        ordinal: int,
        name: str,
        arguments: Mapping[str, Any],
    ) -> ToolProposal:
        normalized = _json_object(arguments, field_name="tool proposal arguments")
        return cls(
            tool_call_id=tool_call_id,
            ordinal=ordinal,
            name=name,
            raw_arguments=json.dumps(
                normalized,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    @property
    def arguments(self) -> dict[str, Any]:
        parsed = json.loads(self.raw_arguments or "{}")
        if not isinstance(parsed, dict):  # guarded by __post_init__
            raise AssertionError("validated tool arguments changed type")
        return parsed

    @property
    def signature(self) -> str:
        canonical = json.dumps(
            self.arguments,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{self.name}:{canonical}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "ordinal": self.ordinal,
            "name": self.name,
            "raw_arguments": self.raw_arguments,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolProposal:
        return cls(
            tool_call_id=str(value["tool_call_id"]),
            ordinal=int(value["ordinal"]),
            name=str(value["name"]),
            raw_arguments=str(value.get("raw_arguments", "{}")),
        )


@dataclass(frozen=True)
class Message:
    """A provider-neutral conversational message."""

    role: MessageRole
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_proposals: tuple[ToolProposal, ...] = ()
    source_label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            object.__setattr__(self, "role", MessageRole(str(self.role)))
        object.__setattr__(self, "tool_proposals", tuple(self.tool_proposals))
        object.__setattr__(
            self,
            "metadata",
            _json_object(self.metadata, field_name="message metadata"),
        )
        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.tool_proposals and self.role is not MessageRole.ASSISTANT:
            raise ValueError("only assistant messages may contain tool proposals")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "tool_proposals": [proposal.to_dict() for proposal in self.tool_proposals],
            "source_label": self.source_label,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Message:
        return cls(
            role=MessageRole(str(value["role"])),
            content=value.get("content"),
            name=value.get("name"),
            tool_call_id=value.get("tool_call_id"),
            tool_proposals=tuple(
                ToolProposal.from_dict(item)
                for item in value.get("tool_proposals", ())
            ),
            source_label=value.get("source_label"),
            metadata=value.get("metadata", {}),
        )


@dataclass(frozen=True)
class ToolSchema:
    """A model-visible function schema without executor implementation details."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    strict: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool schema name must not be empty")
        object.__setattr__(
            self,
            "parameters",
            _json_object(self.parameters, field_name="tool schema parameters"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "strict": self.strict,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolSchema:
        return cls(
            name=str(value["name"]),
            description=str(value.get("description", "")),
            parameters=value.get("parameters", {}),
            strict=bool(value.get("strict", False)),
        )


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int | None = None
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numeric_fields = (
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
            self.reasoning_tokens,
        )
        if any(value < 0 for value in numeric_fields):
            raise ValueError("usage token counts must be non-negative")
        total = (
            self.input_tokens + self.output_tokens
            if self.total_tokens is None
            else self.total_tokens
        )
        if total < 0:
            raise ValueError("total_tokens must be non-negative")
        object.__setattr__(self, "total_tokens", total)
        object.__setattr__(
            self,
            "details",
            _json_object(self.details, field_name="usage details"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Usage:
        return cls(
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            total_tokens=(
                int(value["total_tokens"]) if value.get("total_tokens") is not None else None
            ),
            cached_input_tokens=int(value.get("cached_input_tokens", 0)),
            reasoning_tokens=int(value.get("reasoning_tokens", 0)),
            details=value.get("details", {}),
        )


class ModelEventType(str, Enum):
    RESPONSE_STARTED = "response.started"
    TEXT_DELTA = "text.delta"
    TOOL_CALL_DELTA = "tool_call.delta"
    USAGE_UPDATED = "usage.updated"
    RESPONSE_COMPLETED = "response.completed"
    RESPONSE_FAILED = "response.failed"


@dataclass(frozen=True)
class ModelEvent:
    type: ModelEventType
    sequence: int
    provider_request_id: str | None = None
    text_delta: str | None = None
    tool_call_id: str | None = None
    tool_ordinal: int | None = None
    tool_name_delta: str | None = None
    tool_arguments_delta: str | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    text: str | None = None
    tool_proposals: tuple[ToolProposal, ...] = ()
    error_kind: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, ModelEventType):
            object.__setattr__(self, "type", ModelEventType(str(self.type)))
        if self.sequence < 0:
            raise ValueError("model event sequence must be non-negative")
        object.__setattr__(self, "tool_proposals", tuple(self.tool_proposals))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "sequence": self.sequence,
            "provider_request_id": self.provider_request_id,
            "text_delta": self.text_delta,
            "tool_call_id": self.tool_call_id,
            "tool_ordinal": self.tool_ordinal,
            "tool_name_delta": self.tool_name_delta,
            "tool_arguments_delta": self.tool_arguments_delta,
            "usage": self.usage.to_dict() if self.usage else None,
            "finish_reason": self.finish_reason,
            "text": self.text,
            "tool_proposals": [proposal.to_dict() for proposal in self.tool_proposals],
            "error_kind": self.error_kind,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelEvent:
        raw_usage = value.get("usage")
        return cls(
            type=ModelEventType(str(value["type"])),
            sequence=int(value["sequence"]),
            provider_request_id=value.get("provider_request_id"),
            text_delta=value.get("text_delta"),
            tool_call_id=value.get("tool_call_id"),
            tool_ordinal=(
                int(value["tool_ordinal"]) if value.get("tool_ordinal") is not None else None
            ),
            tool_name_delta=value.get("tool_name_delta"),
            tool_arguments_delta=value.get("tool_arguments_delta"),
            usage=Usage.from_dict(raw_usage) if isinstance(raw_usage, Mapping) else None,
            finish_reason=value.get("finish_reason"),
            text=value.get("text"),
            tool_proposals=tuple(
                ToolProposal.from_dict(item)
                for item in value.get("tool_proposals", ())
            ),
            error_kind=value.get("error_kind"),
            error_message=value.get("error_message"),
        )


class CancellationToken:
    """A small asyncio cancellation signal that does not cancel its owner task."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    model: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    cancellation_token: CancellationToken | None = field(default=None, compare=False, repr=False)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))
        if not self.messages:
            raise ValueError("model request requires at least one message")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(
            self,
            "metadata",
            _json_object(self.metadata, field_name="model request metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [message.to_dict() for message in self.messages],
            "tools": [tool.to_dict() for tool in self.tools],
            "model": self.model,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelRequest:
        return cls(
            messages=tuple(Message.from_dict(item) for item in value["messages"]),
            tools=tuple(ToolSchema.from_dict(item) for item in value.get("tools", ())),
            model=value.get("model"),
            max_output_tokens=(
                int(value["max_output_tokens"])
                if value.get("max_output_tokens") is not None
                else None
            ),
            temperature=(
                float(value["temperature"]) if value.get("temperature") is not None else None
            ),
            timeout_seconds=(
                float(value["timeout_seconds"])
                if value.get("timeout_seconds") is not None
                else None
            ),
            metadata=value.get("metadata", {}),
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ModelResult:
    text: str | None = None
    tool_proposals: tuple[ToolProposal, ...] = ()
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    provider_request_id: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    events: tuple[ModelEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_proposals", tuple(self.tool_proposals))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(
            self,
            "provider_metadata",
            _json_object(self.provider_metadata, field_name="provider metadata"),
        )
        if self.text is None and not self.tool_proposals:
            raise ValueError("model result must contain text or at least one tool proposal")

    @property
    def assistant_message(self) -> Message:
        return Message(
            role=MessageRole.ASSISTANT,
            content=self.text,
            tool_proposals=self.tool_proposals,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_proposals": [proposal.to_dict() for proposal in self.tool_proposals],
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "provider_request_id": self.provider_request_id,
            "provider_metadata": dict(self.provider_metadata),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelResult:
        return cls(
            text=value.get("text"),
            tool_proposals=tuple(
                ToolProposal.from_dict(item)
                for item in value.get("tool_proposals", ())
            ),
            finish_reason=value.get("finish_reason"),
            usage=Usage.from_dict(value.get("usage", {})),
            provider_request_id=value.get("provider_request_id"),
            provider_metadata=value.get("provider_metadata", {}),
            events=tuple(ModelEvent.from_dict(item) for item in value.get("events", ())),
        )

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.types import (
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResult,
)


@dataclass(frozen=True)
class RequestCondition:
    """Declarative, deterministic matching for an offline fake model."""

    call_index: int | None = None
    last_user_contains: str | None = None
    required_tool_names: frozenset[str] = frozenset()
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, request: ModelRequest, call_index: int) -> bool:
        if self.call_index is not None and self.call_index != call_index:
            return False
        if self.last_user_contains is not None:
            last_user = next(
                (
                    message.content or ""
                    for message in reversed(request.messages)
                    if message.role is MessageRole.USER
                ),
                "",
            )
            if self.last_user_contains not in last_user:
                return False
        available_tools = {tool.name for tool in request.tools}
        if not self.required_tool_names.issubset(available_tools):
            return False
        if any(request.metadata.get(key) != value for key, value in self.metadata_equals.items()):
            return False
        return True


@dataclass(frozen=True)
class ConditionalResponse:
    condition: RequestCondition
    result: ModelResult | None = None
    error: ModelGatewayError | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError("conditional response requires exactly one of result or error")


@dataclass
class FakeModel:
    """Provider-free model used by all default runtime and context tests."""

    responses: Sequence[ConditionalResponse]
    fallback: ModelResult | None = None
    requests: list[ModelRequest] = field(default_factory=list, init=False)

    @classmethod
    def scripted(cls, results: Sequence[ModelResult]) -> FakeModel:
        return cls(
            responses=tuple(
                ConditionalResponse(RequestCondition(call_index=index), result=result)
                for index, result in enumerate(results)
            )
        )

    def _select(self, request: ModelRequest) -> ModelResult:
        token = request.cancellation_token
        if token is not None and token.cancelled:
            raise ModelGatewayError(ModelErrorKind.CANCELLED, "model request was cancelled")
        call_index = len(self.requests)
        self.requests.append(request)
        for response in self.responses:
            if response.condition.matches(request, call_index):
                if response.error is not None:
                    raise response.error
                if response.result is None:  # guarded by ConditionalResponse
                    raise AssertionError("matched fake response has no outcome")
                return response.result
        if self.fallback is not None:
            return self.fallback
        raise ModelGatewayError(
            ModelErrorKind.PROTOCOL,
            f"fake model has no response matching call {call_index}",
        )

    async def complete(self, request: ModelRequest) -> ModelResult:
        return self._select(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        result = self._select(request)
        if result.events:
            for event in result.events:
                if request.cancellation_token is not None:
                    request.cancellation_token.raise_if_cancelled()
                yield event
            return

        sequence = 0
        yield ModelEvent(
            type=ModelEventType.RESPONSE_STARTED,
            sequence=sequence,
            provider_request_id=result.provider_request_id,
        )
        sequence += 1
        if result.text:
            yield ModelEvent(
                type=ModelEventType.TEXT_DELTA,
                sequence=sequence,
                provider_request_id=result.provider_request_id,
                text_delta=result.text,
            )
            sequence += 1
        for proposal in result.tool_proposals:
            yield ModelEvent(
                type=ModelEventType.TOOL_CALL_DELTA,
                sequence=sequence,
                provider_request_id=result.provider_request_id,
                tool_call_id=proposal.tool_call_id,
                tool_ordinal=proposal.ordinal,
                tool_name_delta=proposal.name,
                tool_arguments_delta=proposal.raw_arguments,
            )
            sequence += 1
        if result.usage.total_tokens:
            yield ModelEvent(
                type=ModelEventType.USAGE_UPDATED,
                sequence=sequence,
                provider_request_id=result.provider_request_id,
                usage=result.usage,
            )
            sequence += 1
        yield ModelEvent(
            type=ModelEventType.RESPONSE_COMPLETED,
            sequence=sequence,
            provider_request_id=result.provider_request_id,
            text=result.text,
            tool_proposals=result.tool_proposals,
            usage=result.usage,
            finish_reason=result.finish_reason,
        )

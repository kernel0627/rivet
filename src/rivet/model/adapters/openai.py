from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from rivet.model.errors import (
    ModelErrorKind,
    ModelGatewayError,
    redact_sensitive,
)
from rivet.model.types import (
    CancellationToken,
    Message,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResult,
    ToolProposal,
    ToolSchema,
    Usage,
)


@dataclass(frozen=True)
class OpenAIProviderConfig:
    """Configuration kept at the adapter boundary.

    The API key is excluded from repr and is never copied into results or errors.
    """

    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 120.0
    allow_insecure_loopback: bool = True
    organization: str | None = None
    project: str | None = None
    max_safe_error_chars: int = 2_000

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("OpenAI model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("OpenAI timeout_seconds must be positive")
        if self.max_safe_error_chars <= 0:
            raise ValueError("max_safe_error_chars must be positive")
        _validate_base_url(
            self.base_url,
            allow_insecure_loopback=self.allow_insecure_loopback,
        )


def _validate_base_url(url: str, *, allow_insecure_loopback: bool) -> None:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError("provider base_url must not contain credentials")
    if parsed.fragment:
        raise ValueError("provider base_url must not contain a fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme == "http"
        and parsed.hostname in loopback_hosts
        and allow_insecure_loopback
    ):
        return
    raise ValueError("provider base_url must use HTTPS (HTTP is loopback-only)")


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return []


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.name:
        payload["name"] = message.name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_proposals:
        payload["tool_calls"] = [
            {
                "id": proposal.tool_call_id,
                "type": "function",
                "function": {
                    "name": proposal.name,
                    "arguments": proposal.raw_arguments,
                },
            }
            for proposal in message.tool_proposals
        ]
    return payload


def _tool_payload(schema: ToolSchema) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": schema.name,
        "description": schema.description,
        "parameters": dict(schema.parameters),
    }
    if schema.strict:
        function["strict"] = True
    return {"type": "function", "function": function}


def _parse_usage(raw_usage: object) -> Usage:
    if raw_usage is None:
        return Usage()
    prompt_details = _get(raw_usage, "prompt_tokens_details")
    completion_details = _get(raw_usage, "completion_tokens_details")
    input_tokens = int(_get(raw_usage, "prompt_tokens", 0) or 0)
    output_tokens = int(_get(raw_usage, "completion_tokens", 0) or 0)
    total_tokens = _get(raw_usage, "total_tokens")
    cached_tokens = int(_get(prompt_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(_get(completion_details, "reasoning_tokens", 0) or 0)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
        cached_input_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _parse_tool_proposals(raw_calls: object) -> tuple[ToolProposal, ...]:
    proposals: list[ToolProposal] = []
    for ordinal, raw_call in enumerate(_as_list(raw_calls)):
        function = _get(raw_call, "function")
        name = _get(function, "name")
        if not isinstance(name, str) or not name:
            raise ModelGatewayError(
                ModelErrorKind.PROTOCOL,
                "provider returned a tool call without a function name",
            )
        raw_arguments = _get(function, "arguments", "{}")
        if not isinstance(raw_arguments, str):
            raise ModelGatewayError(
                ModelErrorKind.PROTOCOL,
                f"provider returned non-text arguments for tool {name!r}",
            )
        tool_call_id = _get(raw_call, "id") or f"call-{ordinal}"
        try:
            proposal = ToolProposal(
                tool_call_id=str(tool_call_id),
                ordinal=ordinal,
                name=name,
                raw_arguments=raw_arguments,
            )
        except ValueError as exc:
            raise ModelGatewayError(
                ModelErrorKind.PROTOCOL,
                f"provider returned invalid arguments for tool {name!r}",
            ) from exc
        proposals.append(proposal)
    return tuple(proposals)


def _safe_provider_metadata(response: object) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("system_fingerprint", "service_tier"):
        value = _get(response, key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                metadata[key] = value
    return metadata


async def _await_with_cancellation(
    awaitable: Awaitable[Any],
    *,
    cancellation_token: CancellationToken | None,
    timeout_seconds: float,
) -> Any:
    provider_task = asyncio.ensure_future(awaitable)
    cancellation_task: asyncio.Task[None] | None = None
    try:
        if cancellation_token is None:
            return await asyncio.wait_for(provider_task, timeout=timeout_seconds)
        if cancellation_token.cancelled:
            provider_task.cancel()
            with suppress(BaseException):
                await provider_task
            raise ModelGatewayError(
                ModelErrorKind.CANCELLED,
                "model request was cancelled",
            )
        cancellation_task = asyncio.create_task(cancellation_token.wait())
        done, _ = await asyncio.wait(
            {provider_task, cancellation_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            provider_task.cancel()
            with suppress(BaseException):
                await provider_task
            raise TimeoutError("model request timed out")
        if cancellation_task in done:
            provider_task.cancel()
            with suppress(BaseException):
                await provider_task
            raise ModelGatewayError(
                ModelErrorKind.CANCELLED,
                "model request was cancelled",
            )
        return await provider_task
    finally:
        if cancellation_task is not None:
            cancellation_task.cancel()
            with suppress(BaseException):
                await cancellation_task


class OpenAIChatGateway:
    """Official-SDK adapter for OpenAI and compatible Chat Completions APIs."""

    def __init__(self, config: OpenAIProviderConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        if client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - depends on installation
                raise RuntimeError(
                    "OpenAIChatGateway requires the optional 'openai' package"
                ) from exc
            client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout_seconds,
                organization=config.organization,
                project=config.project,
                max_retries=0,
            )
        self._client = client

    def _request_payload(
        self,
        request: ModelRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self.config.model,
            "messages": [_message_payload(message) for message in request.messages],
            "stream": stream,
        }
        if request.tools:
            payload["tools"] = [_tool_payload(schema) for schema in request.tools]
            payload["tool_choice"] = "auto"
        if request.max_output_tokens is not None:
            payload["max_completion_tokens"] = request.max_output_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _timeout(self, request: ModelRequest) -> float:
        return request.timeout_seconds or self.config.timeout_seconds

    async def _call_provider(self, payload: Mapping[str, Any], request: ModelRequest) -> Any:
        try:
            if (
                request.cancellation_token is not None
                and request.cancellation_token.cancelled
            ):
                raise ModelGatewayError(
                    ModelErrorKind.CANCELLED,
                    "model request was cancelled",
                )
            outcome = self._client.chat.completions.create(**dict(payload))
            if not inspect.isawaitable(outcome):
                raise ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "provider client returned a non-awaitable response",
                )
            return await _await_with_cancellation(
                outcome,
                cancellation_token=request.cancellation_token,
                timeout_seconds=self._timeout(request),
            )
        except ModelGatewayError:
            raise
        except asyncio.CancelledError as exc:
            raise ModelGatewayError(
                ModelErrorKind.CANCELLED,
                "model request was cancelled",
            ) from exc
        except Exception as exc:
            raise self._classify_error(exc) from exc

    async def complete(self, request: ModelRequest) -> ModelResult:
        response = await self._call_provider(
            self._request_payload(request, stream=False),
            request,
        )
        try:
            choices = _as_list(_get(response, "choices"))
            if not choices:
                raise ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "provider response contains no choices",
                )
            choice = choices[0]
            raw_message = _get(choice, "message")
            if raw_message is None:
                raise ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "provider response contains no assistant message",
                )
            text = _get(raw_message, "content")
            if text is not None and not isinstance(text, str):
                raise ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "provider returned unsupported assistant content",
                )
            proposals = _parse_tool_proposals(_get(raw_message, "tool_calls"))
            provider_request_id = _get(response, "id")
            usage = _parse_usage(_get(response, "usage"))
            finish_reason = _get(choice, "finish_reason")
            events = (
                ModelEvent(
                    type=ModelEventType.RESPONSE_STARTED,
                    sequence=0,
                    provider_request_id=(
                        str(provider_request_id) if provider_request_id else None
                    ),
                ),
                ModelEvent(
                    type=ModelEventType.RESPONSE_COMPLETED,
                    sequence=1,
                    provider_request_id=(
                        str(provider_request_id) if provider_request_id else None
                    ),
                    text=text,
                    tool_proposals=proposals,
                    usage=usage,
                    finish_reason=str(finish_reason) if finish_reason else None,
                ),
            )
            return ModelResult(
                text=text,
                tool_proposals=proposals,
                finish_reason=str(finish_reason) if finish_reason else None,
                usage=usage,
                provider_request_id=(
                    str(provider_request_id) if provider_request_id else None
                ),
                provider_metadata=_safe_provider_metadata(response),
                events=events,
            )
        except ModelGatewayError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise ModelGatewayError(
                ModelErrorKind.PROTOCOL,
                "provider response could not be normalized",
            ) from exc

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        stream = None
        sequence = 0
        provider_request_id: str | None = None
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish_reason: str | None = None
        try:
            stream = await self._call_provider(
                self._request_payload(request, stream=True),
                request,
            )
            yield ModelEvent(
                type=ModelEventType.RESPONSE_STARTED,
                sequence=sequence,
            )
            sequence += 1

            iterator = stream.__aiter__()
            while True:
                try:
                    chunk = await _await_with_cancellation(
                        iterator.__anext__(),
                        cancellation_token=request.cancellation_token,
                        timeout_seconds=self._timeout(request),
                    )
                except StopAsyncIteration:
                    break
                chunk_id = _get(chunk, "id")
                if chunk_id:
                    provider_request_id = str(chunk_id)
                raw_usage = _get(chunk, "usage")
                if raw_usage is not None:
                    usage = _parse_usage(raw_usage)
                    yield ModelEvent(
                        type=ModelEventType.USAGE_UPDATED,
                        sequence=sequence,
                        provider_request_id=provider_request_id,
                        usage=usage,
                    )
                    sequence += 1

                choices = _as_list(_get(chunk, "choices"))
                if not choices:
                    continue
                choice = choices[0]
                raw_finish_reason = _get(choice, "finish_reason")
                if raw_finish_reason:
                    finish_reason = str(raw_finish_reason)
                delta = _get(choice, "delta")
                if delta is None:
                    continue
                text_delta = _get(delta, "content")
                if isinstance(text_delta, str) and text_delta:
                    text_parts.append(text_delta)
                    yield ModelEvent(
                        type=ModelEventType.TEXT_DELTA,
                        sequence=sequence,
                        provider_request_id=provider_request_id,
                        text_delta=text_delta,
                    )
                    sequence += 1

                for raw_call in _as_list(_get(delta, "tool_calls")):
                    ordinal = int(_get(raw_call, "index", 0) or 0)
                    parts = tool_parts.setdefault(
                        ordinal,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    call_id_delta = _get(raw_call, "id")
                    if call_id_delta:
                        parts["id"] += str(call_id_delta)
                    function = _get(raw_call, "function")
                    name_delta = _get(function, "name")
                    arguments_delta = _get(function, "arguments")
                    if name_delta:
                        parts["name"] += str(name_delta)
                    if arguments_delta:
                        parts["arguments"] += str(arguments_delta)
                    yield ModelEvent(
                        type=ModelEventType.TOOL_CALL_DELTA,
                        sequence=sequence,
                        provider_request_id=provider_request_id,
                        tool_call_id=parts["id"] or None,
                        tool_ordinal=ordinal,
                        tool_name_delta=str(name_delta) if name_delta else None,
                        tool_arguments_delta=(
                            str(arguments_delta) if arguments_delta else None
                        ),
                    )
                    sequence += 1

            proposals = tuple(
                ToolProposal(
                    tool_call_id=parts["id"] or f"call-{ordinal}",
                    ordinal=ordinal,
                    name=parts["name"],
                    raw_arguments=parts["arguments"] or "{}",
                )
                for ordinal, parts in sorted(tool_parts.items())
            )
            text = "".join(text_parts) or None
            if text is None and not proposals:
                raise ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "provider stream completed without text or tool proposals",
                )
            yield ModelEvent(
                type=ModelEventType.RESPONSE_COMPLETED,
                sequence=sequence,
                provider_request_id=provider_request_id,
                text=text,
                tool_proposals=proposals,
                usage=usage,
                finish_reason=finish_reason,
            )
        except GeneratorExit:
            raise
        except (Exception, asyncio.CancelledError) as exc:
            error = exc if isinstance(exc, ModelGatewayError) else self._classify_error(exc)
            if isinstance(error, ModelGatewayError):
                yield ModelEvent(
                    type=ModelEventType.RESPONSE_FAILED,
                    sequence=sequence,
                    provider_request_id=error.provider_request_id or provider_request_id,
                    error_kind=error.kind.value,
                    error_message=str(error),
                )
                if error is exc:
                    raise
                raise error from exc
            raise
        finally:
            if stream is not None:
                close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
                if close is not None:
                    with suppress(BaseException):
                        outcome = close()
                        if inspect.isawaitable(outcome):
                            await outcome

    def _classify_error(self, exc: BaseException) -> ModelGatewayError:
        if isinstance(exc, ModelGatewayError):
            return exc
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status_code is None:
            status_code = _get(response, "status_code")
        try:
            status_code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            status_code = None

        request_id = getattr(exc, "request_id", None)
        if request_id is None:
            headers = _get(response, "headers", {})
            if isinstance(headers, Mapping):
                request_id = headers.get("x-request-id") or headers.get("request-id")

        name = type(exc).__name__.lower()
        raw_message = str(exc)
        lowered = raw_message.lower()
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
            kind = ModelErrorKind.TRANSPORT
            retryable = True
        elif "cancel" in name:
            kind = ModelErrorKind.CANCELLED
            retryable = False
        elif status_code in {401, 403} or "authentication" in name or "permission" in name:
            kind = ModelErrorKind.AUTH
            retryable = False
        elif status_code == 429 or "ratelimit" in name or "rate_limit" in name:
            kind = ModelErrorKind.RATE_LIMIT
            retryable = True
        elif (
            status_code in {400, 413, 422}
            and any(
                marker in lowered
                for marker in ("context", "token limit", "maximum context", "too many tokens")
            )
        ):
            kind = ModelErrorKind.CONTEXT_OVERFLOW
            retryable = False
        elif status_code is not None and status_code >= 500:
            kind = ModelErrorKind.UNAVAILABLE
            retryable = True
        elif any(marker in name for marker in ("connection", "transport", "network")):
            kind = ModelErrorKind.TRANSPORT
            retryable = True
        elif status_code is not None and 400 <= status_code < 500:
            kind = ModelErrorKind.PROTOCOL
            retryable = False
        else:
            kind = ModelErrorKind.TRANSPORT
            retryable = True

        safe_detail = redact_sensitive(
            raw_message,
            secrets=(self.config.api_key,),
            limit=self.config.max_safe_error_chars,
        )
        return ModelGatewayError(
            kind,
            f"model provider request failed: {safe_detail}",
            retryable=retryable,
            status_code=status_code,
            provider_request_id=str(request_id) if request_id else None,
        )

    async def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if close is None:
            return
        outcome = close()
        if inspect.isawaitable(outcome):
            await outcome


OpenAICompatibleGateway = OpenAIChatGateway

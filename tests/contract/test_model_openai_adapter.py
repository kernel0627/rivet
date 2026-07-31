from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncIterator
from typing import Any

import httpx
from openai import AsyncOpenAI

from rivet.model.adapters.openai import OpenAIChatGateway, OpenAIProviderConfig
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.types import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEventType,
    ModelRequest,
    ToolSchema,
)


class _FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **payload: Any) -> Any:
        self.calls.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(outcomes)


class _ChunkStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def close(self) -> None:
        self.closed = True


class _BlockingStream:
    def __init__(self) -> None:
        self._never = asyncio.Event()
        self.closed = False

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self

    async def __anext__(self) -> dict[str, Any]:
        await self._never.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class _ProviderFailure(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = "provider-request"


class OpenAIAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    def _gateway(self, outcomes: list[Any], *, api_key: str | None = None) -> tuple[
        OpenAIChatGateway,
        _FakeClient,
    ]:
        client = _FakeClient(outcomes)
        gateway = OpenAIChatGateway(
            OpenAIProviderConfig(model="test-model", api_key=api_key),
            client=client,
        )
        return gateway, client

    async def test_complete_normalizes_text_tools_usage_and_payload(self) -> None:
        gateway, client = self._gateway(
            [
                {
                    "id": "response-1",
                    "system_fingerprint": "fingerprint",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "Looking.",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"main.py"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                }
            ]
        )
        request = ModelRequest(
            messages=(Message(role=MessageRole.USER, content="inspect"),),
            tools=(
                ToolSchema(
                    name="read_file",
                    description="Read a file",
                    parameters={"type": "object"},
                    strict=True,
                ),
            ),
            max_output_tokens=512,
        )

        result = await gateway.complete(request)

        self.assertEqual(result.text, "Looking.")
        self.assertEqual(result.tool_proposals[0].arguments, {"path": "main.py"})
        self.assertEqual(result.usage.total_tokens, 18)
        self.assertEqual(result.provider_metadata, {"system_fingerprint": "fingerprint"})
        payload = client.chat.completions.calls[0]
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertTrue(payload["tools"][0]["function"]["strict"])
        self.assertNotIn("api_key", payload)

    async def test_deepseek_reasoning_is_returned_on_followup_tool_request(
        self,
    ) -> None:
        client = _FakeClient(
            [
                {
                    "id": "deepseek-response-1",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "I will inspect the file.",
                                "reasoning_content": "The task requires reading main.py.",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"main.py"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "id": "deepseek-response-2",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "Inspection complete.",
                                "reasoning_content": "The tool result answers the task.",
                            },
                        }
                    ],
                },
            ]
        )
        gateway = OpenAIChatGateway(
            OpenAIProviderConfig(
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                max_output_tokens_parameter="max_tokens",
            ),
            client=client,
        )
        first = await gateway.complete(
            ModelRequest(
                messages=(Message(role=MessageRole.USER, content="inspect"),),
                max_output_tokens=512,
            )
        )
        await gateway.complete(
            ModelRequest(
                messages=(
                    Message(role=MessageRole.USER, content="inspect"),
                    first.assistant_message,
                    Message(
                        role=MessageRole.TOOL,
                        content="file contents",
                        tool_call_id="call-1",
                    ),
                ),
                max_output_tokens=256,
            )
        )

        first_payload, followup_payload = client.chat.completions.calls
        self.assertEqual(first_payload["max_tokens"], 512)
        self.assertNotIn("max_completion_tokens", first_payload)
        self.assertEqual(
            followup_payload["messages"][1]["reasoning_content"],
            "The task requires reading main.py.",
        )
        self.assertEqual(followup_payload["max_tokens"], 256)

    async def test_official_sdk_serializes_deepseek_fields_through_http_transport(
        self,
    ) -> None:
        captured: list[dict[str, Any]] = []
        responses = [
            {
                "id": "local-1",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Inspect the file first.",
                            "tool_calls": [
                                {
                                    "id": "call-local",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"main.py"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "id": "local-2",
                "object": "chat.completion",
                "created": 2,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Done.",
                            "reasoning_content": "The tool result is sufficient.",
                        },
                    }
                ],
            },
        ]

        def handle(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=responses.pop(0), request=request)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        client = AsyncOpenAI(
            api_key="local-test-key",
            base_url="http://127.0.0.1/v1",
            http_client=http_client,
        )
        gateway = OpenAIChatGateway(
            OpenAIProviderConfig(
                model="deepseek-v4-flash",
                api_key="local-test-key",
                base_url="http://127.0.0.1/v1",
                max_output_tokens_parameter="max_tokens",
            ),
            client=client,
        )
        try:
            first = await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),),
                    max_output_tokens=400,
                )
            )
            await gateway.complete(
                ModelRequest(
                    messages=(
                        Message(role=MessageRole.USER, content="inspect"),
                        first.assistant_message,
                        Message(
                            role=MessageRole.TOOL,
                            content="contents",
                            tool_call_id="call-local",
                        ),
                    ),
                    max_output_tokens=200,
                )
            )
        finally:
            await gateway.close()
            await client.close()

        self.assertEqual(captured[0]["max_tokens"], 400)
        self.assertNotIn("max_completion_tokens", captured[0])
        self.assertEqual(
            captured[1]["messages"][1]["reasoning_content"],
            "Inspect the file first.",
        )
        self.assertEqual(captured[1]["max_tokens"], 200)

    async def test_invalid_tool_arguments_are_protocol_error(self) -> None:
        gateway, _ = self._gateway(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": "{broken",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),)
                )
            )

        self.assertEqual(raised.exception.kind, ModelErrorKind.PROTOCOL)

    async def test_stream_assembles_fragmented_tool_call_and_usage(self) -> None:
        stream = _ChunkStream(
            [
                {
                    "id": "response-stream",
                    "choices": [
                        {
                            "delta": {
                                "reasoning_content": "Need ",
                                "content": "Read",
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "response-stream",
                    "choices": [
                        {
                            "delta": {"reasoning_content": "the file."},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "response-stream",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {
                                            "name": "read_",
                                            "arguments": '{"path":',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "response-stream",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "file",
                                            "arguments": '"main.py"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                {
                    "id": "response-stream",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                    },
                },
            ]
        )
        gateway, client = self._gateway([stream])
        request = ModelRequest(
            messages=(Message(role=MessageRole.USER, content="inspect"),)
        )

        events = [event async for event in gateway.stream(request)]

        self.assertEqual(events[0].type, ModelEventType.RESPONSE_STARTED)
        self.assertIn(ModelEventType.TOOL_CALL_DELTA, [event.type for event in events])
        completed = events[-1]
        self.assertEqual(completed.type, ModelEventType.RESPONSE_COMPLETED)
        self.assertEqual(completed.reasoning_content, "Need the file.")
        self.assertEqual(completed.text, "Read")
        self.assertEqual(completed.tool_proposals[0].name, "read_file")
        self.assertEqual(
            completed.tool_proposals[0].arguments,
            {"path": "main.py"},
        )
        self.assertEqual(completed.usage.total_tokens, 12)
        self.assertIn(ModelEventType.REASONING_DELTA, [event.type for event in events])
        self.assertTrue(stream.closed)
        self.assertTrue(client.chat.completions.calls[0]["stream"])

    async def test_rate_limit_error_is_classified_and_secret_is_redacted(self) -> None:
        secret = "sk-super-secret-value"
        gateway, _ = self._gateway(
            [_ProviderFailure(f"limit reached; api_key={secret}", 429)],
            api_key=secret,
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),)
                )
            )

        error = raised.exception
        self.assertEqual(error.kind, ModelErrorKind.RATE_LIMIT)
        self.assertTrue(error.retryable)
        self.assertNotIn(secret, str(error))
        self.assertIn("[REDACTED]", str(error))

    async def test_timeout_error_is_retryable_transport_failure(self) -> None:
        secret = "sk-timeout-secret-value"
        gateway, _ = self._gateway(
            [TimeoutError(f"request timed out; api_key={secret}")],
            api_key=secret,
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),)
                )
            )

        error = raised.exception
        self.assertEqual(error.kind, ModelErrorKind.TRANSPORT)
        self.assertTrue(error.retryable)
        self.assertNotIn(secret, str(error))

    async def test_connection_error_is_retryable_transport_failure(self) -> None:
        gateway, _ = self._gateway([ConnectionError("network unavailable")])

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),)
                )
            )

        error = raised.exception
        self.assertEqual(error.kind, ModelErrorKind.TRANSPORT)
        self.assertTrue(error.retryable)

    async def test_server_error_is_retryable_unavailable_failure(self) -> None:
        gateway, _ = self._gateway(
            [_ProviderFailure("temporary upstream failure", 503)]
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),)
                )
            )

        error = raised.exception
        self.assertEqual(error.kind, ModelErrorKind.UNAVAILABLE)
        self.assertTrue(error.retryable)
        self.assertEqual(error.status_code, 503)

    async def test_pre_cancelled_request_never_returns_provider_result(self) -> None:
        token = CancellationToken()
        token.cancel()
        gateway, client = self._gateway(
            [{"choices": [{"message": {"content": "should not arrive"}}]}]
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await gateway.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="inspect"),),
                    cancellation_token=token,
                )
            )

        self.assertEqual(raised.exception.kind, ModelErrorKind.CANCELLED)
        self.assertEqual(client.chat.completions.calls, [])

    async def test_stream_cancellation_emits_safe_failure_then_raises(self) -> None:
        token = CancellationToken()
        stream = _BlockingStream()
        gateway, _ = self._gateway([stream])
        iterator = gateway.stream(
            ModelRequest(
                messages=(Message(role=MessageRole.USER, content="inspect"),),
                cancellation_token=token,
            )
        )

        started = await iterator.__anext__()
        token.cancel()
        failed = await iterator.__anext__()

        self.assertEqual(started.type, ModelEventType.RESPONSE_STARTED)
        self.assertEqual(failed.type, ModelEventType.RESPONSE_FAILED)
        self.assertEqual(failed.error_kind, ModelErrorKind.CANCELLED.value)
        with self.assertRaises(ModelGatewayError) as raised:
            await iterator.__anext__()
        self.assertEqual(raised.exception.kind, ModelErrorKind.CANCELLED)
        self.assertTrue(stream.closed)

    def test_plain_http_is_rejected_except_explicit_loopback(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            OpenAIProviderConfig(
                model="test",
                base_url="http://provider.example/v1",
            )

        config = OpenAIProviderConfig(
            model="test",
            base_url="http://127.0.0.1:8000/v1",
        )
        self.assertEqual(config.base_url, "http://127.0.0.1:8000/v1")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.gateway import ModelGateway
from rivet.model.types import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEventType,
    ModelRequest,
    ModelResult,
    ToolSchema,
)


class FakeModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_matches_first_declarative_condition_deterministically(self) -> None:
        inspect_result = ModelResult(text="inspection")
        fallback = ModelResult(text="fallback")
        model = FakeModel(
            responses=(
                ConditionalResponse(
                    RequestCondition(
                        last_user_contains="inspect",
                        required_tool_names=frozenset({"read_file"}),
                    ),
                    result=inspect_result,
                ),
            ),
            fallback=fallback,
        )
        request = ModelRequest(
            messages=(Message(role=MessageRole.USER, content="please inspect"),),
            tools=(
                ToolSchema(
                    name="read_file",
                    description="Read",
                    parameters={"type": "object"},
                ),
            ),
        )

        result = await model.complete(request)

        self.assertIsInstance(model, ModelGateway)
        self.assertEqual(result.text, "inspection")
        self.assertEqual(model.requests, [request])

    async def test_scripted_model_streams_normalized_events(self) -> None:
        model = FakeModel.scripted([ModelResult(text="done")])
        request = ModelRequest(
            messages=(Message(role=MessageRole.USER, content="work"),)
        )

        events = [event async for event in model.stream(request)]

        self.assertEqual(events[0].type, ModelEventType.RESPONSE_STARTED)
        self.assertEqual(events[1].type, ModelEventType.TEXT_DELTA)
        self.assertEqual(events[-1].type, ModelEventType.RESPONSE_COMPLETED)
        self.assertEqual(events[-1].text, "done")

    async def test_pre_cancelled_request_is_classified(self) -> None:
        token = CancellationToken()
        token.cancel()
        model = FakeModel.scripted([ModelResult(text="unused")])
        request = ModelRequest(
            messages=(Message(role=MessageRole.USER, content="work"),),
            cancellation_token=token,
        )

        with self.assertRaises(ModelGatewayError) as raised:
            await model.complete(request)

        self.assertEqual(raised.exception.kind, ModelErrorKind.CANCELLED)


if __name__ == "__main__":
    unittest.main()

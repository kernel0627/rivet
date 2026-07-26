from __future__ import annotations

import unittest

from rivet.model.types import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResult,
    ToolProposal,
    ToolSchema,
    Usage,
)


class ModelTypesTests(unittest.TestCase):
    def test_provider_neutral_types_round_trip(self) -> None:
        proposal = ToolProposal.from_arguments(
            tool_call_id="call-1",
            ordinal=0,
            name="read_file",
            arguments={"path": "src/main.py", "line": 3},
        )
        result = ModelResult(
            text="I will inspect the file.",
            tool_proposals=(proposal,),
            finish_reason="tool_calls",
            usage=Usage(input_tokens=10, output_tokens=4),
            provider_request_id="request-1",
            events=(
                ModelEvent(
                    type=ModelEventType.RESPONSE_COMPLETED,
                    sequence=0,
                    text="I will inspect the file.",
                    tool_proposals=(proposal,),
                ),
            ),
        )

        restored = ModelResult.from_dict(result.to_dict())

        self.assertEqual(restored, result)
        self.assertEqual(restored.assistant_message.role, MessageRole.ASSISTANT)
        self.assertEqual(restored.tool_proposals[0].arguments["line"], 3)

    def test_tool_proposal_requires_json_object_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            ToolProposal(
                tool_call_id="call-1",
                ordinal=0,
                name="read_file",
                raw_arguments="{broken",
            )

        with self.assertRaisesRegex(ValueError, "JSON object"):
            ToolProposal(
                tool_call_id="call-1",
                ordinal=0,
                name="read_file",
                raw_arguments='["src/main.py"]',
            )

    def test_request_digest_is_stable_and_excludes_cancellation_object(self) -> None:
        message = Message(role=MessageRole.USER, content="inspect")
        tool = ToolSchema(
            name="read_file",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        )
        first = ModelRequest(
            messages=(message,),
            tools=(tool,),
            cancellation_token=CancellationToken(),
            metadata={"run_id": "run-1"},
        )
        second = ModelRequest(
            messages=(message,),
            tools=(tool,),
            cancellation_token=CancellationToken(),
            metadata={"run_id": "run-1"},
        )

        self.assertEqual(first.digest, second.digest)
        self.assertEqual(ModelRequest.from_dict(first.to_dict()).to_dict(), first.to_dict())

    def test_message_role_invariants_reject_invalid_tool_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "tool_call_id"):
            Message(role=MessageRole.TOOL, content="result")

        proposal = ToolProposal.from_arguments(
            tool_call_id="call-1",
            ordinal=0,
            name="read_file",
            arguments={},
        )
        with self.assertRaisesRegex(ValueError, "assistant"):
            Message(
                role=MessageRole.USER,
                content="wrong",
                tool_proposals=(proposal,),
            )


if __name__ == "__main__":
    unittest.main()

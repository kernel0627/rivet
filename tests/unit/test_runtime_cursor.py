from __future__ import annotations

import unittest

from rivet.domain import (
    ModelCallRecord,
    ModelCallStatus,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
)
from rivet.model.types import ToolProposal
from rivet.runtime.contracts import RuntimeCommandError
from rivet.runtime.cursor import decode_cursor, encode_cursor, tool_cursor


class RuntimeCursorTests(unittest.TestCase):
    def test_empty_and_round_trip_cursor(self) -> None:
        self.assertEqual(decode_cursor(None), {"kind": "new_turn"})
        value = {"kind": "new_turn", "message": "继续"}

        self.assertEqual(decode_cursor(encode_cursor(value)), value)

    def test_non_object_cursor_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeCommandError, "resume cursor is invalid"):
            decode_cursor("[]")

    def test_tool_cursor_preserves_resume_identity(self) -> None:
        turn = Turn.create("run_one", 1)
        call = ModelCallRecord(
            model_call_id="model_call_one",
            turn_id=turn.turn_id,
            attempt_no=1,
            provider="fake",
            model="fake",
            status=ModelCallStatus.CREATED,
            context_id="context_one",
            request_digest="a" * 64,
        )
        execution = ToolExecutionRecord(
            execution_id="tool_execution_one",
            turn_id=turn.turn_id,
            model_call_id=call.model_call_id,
            tool_call_id="call-one",
            ordinal=0,
            attempt_no=1,
            tool_name="read_file",
            tool_version="1.0.0",
            status=ToolExecutionStatus.PROPOSED,
        )
        proposal = ToolProposal.from_arguments(
            tool_call_id="call-one",
            ordinal=0,
            name="read_file",
            arguments={"path": "main.py"},
        )

        cursor = tool_cursor(
            turn,
            call,
            (proposal,),
            0,
            "context-digest",
            execution,
        )

        self.assertEqual(cursor["kind"], "tool_batch")
        self.assertEqual(cursor["turn_id"], turn.turn_id)
        self.assertEqual(cursor["model_call_id"], call.model_call_id)
        self.assertEqual(cursor["execution_id"], execution.execution_id)
        self.assertEqual(cursor["proposals"], [proposal.to_dict()])


if __name__ == "__main__":
    unittest.main()

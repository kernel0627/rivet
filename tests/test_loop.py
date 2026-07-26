from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rivet.models.scripted import ScriptedModel
from rivet.models.types import ModelResponse, ToolCall
from rivet.runtime.harness import Harness
from rivet.state.session import StopReason


class AgentLoopTests(unittest.TestCase):
    def test_tool_result_returns_to_model_before_final_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
            state_directory = root / "state"
            model = ScriptedModel(
                [
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                id="call-1",
                                name="list_files",
                                arguments={"path": ".", "max_depth": 2},
                            ),
                        )
                    ),
                    ModelResponse(content="The workspace contains main.py."),
                ]
            )
            harness = Harness(
                workspace=root,
                model=model,
                state_directory=state_directory,
            )

            session = harness.run("What is in this workspace?")

            self.assertEqual(session.stop_reason, StopReason.FINAL_ANSWER)
            self.assertEqual(session.final_response, "The workspace contains main.py.")
            self.assertEqual(session.turn_count, 2)
            self.assertEqual(len(model.calls), 2)
            second_turn = model.calls[1]
            tool_messages = [message for message in second_turn if message.role == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("main.py", tool_messages[0].content or "")

            saved = json.loads(
                harness.session_store.path_for(session.id).read_text(encoding="utf-8")
            )
            self.assertEqual(saved["stop_reason"], "final_answer")
            trace_path = state_directory / "traces" / f"{session.id}.jsonl"
            self.assertTrue(trace_path.is_file())
            self.assertIn("tool_completed", trace_path.read_text(encoding="utf-8"))

    def test_max_turns_has_explicit_stop_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = ToolCall(id="call-1", name="list_files", arguments={})
            harness = Harness.with_scripted_model(
                workspace=root,
                responses=[ModelResponse(tool_calls=(call,))],
                max_turns=1,
                state_directory=root / "state",
            )

            session = harness.run("Keep looking forever")

            self.assertEqual(session.stop_reason, StopReason.MAX_TURNS)
            self.assertIsNone(session.final_response)


if __name__ == "__main__":
    unittest.main()


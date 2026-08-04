from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.evaluation.simple_agent import SimpleAgent, SimpleAgentBudget
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.types import ModelResult


class SimpleAgentTests(unittest.IsolatedAsyncioTestCase):
    def budget(self) -> SimpleAgentBudget:
        return SimpleAgentBudget(
            max_model_calls=4,
            max_tool_executions=8,
            max_input_tokens=8_000,
            max_output_tokens=512,
            timeout_seconds=5,
        )

    async def test_model_failure_records_started_request_without_retry(self) -> None:
        model = FakeModel(
            responses=(
                ConditionalResponse(
                    RequestCondition(call_index=0),
                    error=ModelGatewayError(
                        ModelErrorKind.UNAVAILABLE,
                        "temporary outage",
                        retryable=True,
                    ),
                ),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            result = await SimpleAgent(
                gateway=model,
                workspace=Path(directory),
                model="fake",
                budget=self.budget(),
            ).run("inspect the workspace")

        self.assertFalse(result.completed)
        self.assertEqual(result.stop_reason, "model_failure")
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.model_errors[0]["kind"], "MODEL_UNAVAILABLE")
        self.assertEqual(len(model.requests), 1)

    async def test_model_sees_only_four_baseline_tools(self) -> None:
        model = FakeModel.scripted([ModelResult(text="done")])
        with tempfile.TemporaryDirectory() as directory:
            result = await SimpleAgent(
                gateway=model,
                workspace=Path(directory),
                model="fake",
                budget=self.budget(),
            ).run("inspect the workspace")

        self.assertTrue(result.completed)
        self.assertEqual(
            {tool.name for tool in model.requests[0].tools},
            {"read_file", "search_text", "apply_patch", "run_tests"},
        )
        patch_schema = next(tool for tool in model.requests[0].tools if tool.name == "apply_patch")
        self.assertIn("without a recovery snapshot", patch_schema.description)


if __name__ == "__main__":
    unittest.main()

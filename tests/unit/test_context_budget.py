from __future__ import annotations

import unittest

from rivet.context.budget import ContextBudget, HeuristicTokenEstimator
from rivet.model.types import Message, MessageRole, ToolSchema


class ContextBudgetTests(unittest.TestCase):
    def test_context_window_reserves_output_capacity(self) -> None:
        budget = ContextBudget(
            max_input_tokens=8_000,
            reserved_output_tokens=2_000,
            model_context_window=9_000,
        )

        self.assertEqual(budget.input_capacity, 7_000)

    def test_estimator_counts_messages_and_tool_schemas_deterministically(self) -> None:
        estimator = HeuristicTokenEstimator()
        message = Message(role=MessageRole.USER, content="检查这个 Python 文件")
        schema = ToolSchema(
            name="read_file",
            description="Read a file",
            parameters={"type": "object"},
        )

        first = estimator.estimate_message(message)
        second = estimator.estimate_message(message)

        self.assertEqual(first, second)
        self.assertGreater(first, estimator.message_overhead_tokens)
        self.assertGreater(estimator.estimate_tools((schema,)), 0)


if __name__ == "__main__":
    unittest.main()

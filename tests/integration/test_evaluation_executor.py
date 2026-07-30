from __future__ import annotations

import unittest

from rivet.evaluation import EvaluationRunner, RivetEvalExecutor, load_baseline


class EvaluationExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_baseline_runs_through_runtime_and_acceptance_checks(
        self,
    ) -> None:
        cases = load_baseline()

        result = await EvaluationRunner(
            RivetEvalExecutor(mode="offline")
        ).run(cases)

        self.assertTrue(result.passed)
        self.assertEqual(result.pass_rate, 1.0)
        by_id = {case.case_id: case for case in result.cases}
        self.assertTrue(
            by_id["fix_discount"].completion.expected_tests_passed
        )
        self.assertEqual(by_id["fix_discount"].safety.incidents, 0)
        self.assertEqual(
            by_id["reject_workspace_escape"].metadata["run_status"],
            "COMPLETED",
        )


if __name__ == "__main__":
    unittest.main()

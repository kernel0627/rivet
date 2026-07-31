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
        self.assertTrue(
            by_id["locate_invoice_symbol"].completion.final_evidence_accurate
        )
        self.assertEqual(
            by_id["locate_invoice_symbol"].metadata["tool_executions"],
            3,
        )
        self.assertTrue(
            by_id["fix_cross_file_total"].completion.expected_paths_present
        )
        self.assertTrue(
            by_id["fix_cross_file_total"].completion.expected_tests_passed
        )
        self.assertEqual(by_id["fix_cross_file_total"].safety.incidents, 0)
        self.assertTrue(
            by_id["trace_order_call_chain"].completion.final_evidence_accurate
        )
        self.assertTrue(
            by_id["add_slug_regression_test"].completion.expected_tests_passed
        )
        resumed = by_id["resume_permission_write"]
        self.assertTrue(resumed.passed)
        self.assertEqual(resumed.metadata["permission_resumes"], 1)
        self.assertEqual(resumed.metadata["checkpoint_count"], 1)
        self.assertEqual(resumed.metadata["tool_executions"], 3)


if __name__ == "__main__":
    unittest.main()

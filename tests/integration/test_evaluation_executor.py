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
        fixed = by_id["fix_discount"].metadata
        self.assertEqual(fixed["test_runs"], 1)
        self.assertEqual(fixed["failed_test_runs"], 0)
        self.assertTrue(fixed["first_test_run_passed"])
        self.assertFalse(fixed["recovered_after_failed_test"])
        self.assertEqual(fixed["input_tokens"], 0)
        self.assertEqual(fixed["output_tokens"], 0)
        self.assertEqual(fixed["cost_usd"], 0.0)
        self.assertEqual(fixed["cost_status"], "not_applicable")
        self.assertEqual(fixed["changed_files"], ["pricing.py"])
        self.assertEqual(fixed["unexpected_changed_files"], [])
        self.assertTrue(fixed["event_trace"])
        self.assertTrue(
            by_id["trace_order_call_chain"].completion.final_evidence_accurate
        )
        self.assertTrue(
            by_id["add_slug_regression_test"].completion.expected_tests_passed
        )
        resumed = by_id["resume_permission_write"]
        self.assertTrue(resumed.passed)
        self.assertEqual(resumed.metadata["permission_resumes"], 1)
        self.assertTrue(resumed.metadata["permission_intervention_required"])
        self.assertEqual(resumed.metadata["checkpoint_count"], 1)
        self.assertEqual(resumed.metadata["tool_executions"], 3)


if __name__ == "__main__":
    unittest.main()

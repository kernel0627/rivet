from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from rivet.domain import RunStatus
from rivet.evaluation import EvalCase, EvaluationRunner, RivetEvalExecutor, load_baseline
from rivet.evaluation.executor import _failed_eval_execution, _workspace_snapshot


class EvaluationExecutorTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_failure_preserves_started_provider_request_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "inventory.py").write_text("stock = 1\n", encoding="utf-8")
            before = _workspace_snapshot(workspace)
            usage = SimpleNamespace(
                turns=1,
                model_calls=0,
                tool_executions=0,
                input_tokens=0,
                output_tokens=0,
            )
            run = SimpleNamespace(
                run_id="run_failed_diagnostic",
                status=RunStatus.RUNNING,
                usage=usage,
            )
            model_call = SimpleNamespace(
                provider="deepseek",
                model="deepseek-v4-flash",
            )
            state = SimpleNamespace(
                list_model_calls=lambda _run_id: (model_call,),
                list_tool_executions=lambda _run_id: (),
                list_checkpoints=lambda _run_id: (),
            )
            service = SimpleNamespace(
                sessions=lambda: (SimpleNamespace(session_id="session_eval"),),
                runs=lambda _session_id: (run,),
                events=lambda _run_id: (),
                state=state,
            )
            case = EvalCase(
                id="failed-diagnostic",
                objective="Preserve failure evidence.",
                fixture="inline",
                task_category="single_file",
                expected_files=("inventory.py",),
                fixture_files={"inventory.py": "stock = 1\n"},
                offline_model=({"text": "unused"},),
            )

            result = _failed_eval_execution(
                application=SimpleNamespace(service=service),
                workspace=workspace,
                before=before,
                case=case,
                mode="live",
                error=RuntimeError("lease expired"),
                started_at=time.perf_counter(),
            )

        self.assertEqual(result.metadata["model_calls"], 0)
        self.assertEqual(result.metadata["provider_requests_started"], 1)
        self.assertEqual(result.metadata["provider"], "deepseek")
        self.assertEqual(result.metadata["changed_files"], [])

    def test_workspace_snapshot_ignores_test_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
            cache = workspace / ".pytest_cache" / "v" / "cache"
            cache.mkdir(parents=True)
            (cache / "nodeids").write_text("[]\n", encoding="utf-8")

            snapshot = _workspace_snapshot(workspace)

        self.assertEqual(set(snapshot), {"app.py"})

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
        self.assertEqual(fixed["provider_requests_started"], fixed["model_calls"])
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

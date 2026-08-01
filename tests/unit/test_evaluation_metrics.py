from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.evaluation.dataset import EvalCase, load_baseline, load_jsonl
from rivet.evaluation.metrics import (
    RetrievalMetrics,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


class EvaluationMetricsTests(unittest.TestCase):
    def test_retrieval_metrics(self) -> None:
        relevant = {"a", "c"}
        ranked = ["x", "a", "b", "c"]

        metrics = RetrievalMetrics.calculate(
            relevant=relevant,
            ranked=ranked,
            k=3,
        )

        self.assertEqual(metrics.recall_at_k, 0.5)
        self.assertEqual(metrics.reciprocal_rank, 0.5)
        self.assertGreater(metrics.ndcg_at_k, 0)
        self.assertLess(metrics.ndcg_at_k, 1)

    def test_empty_relevant_set_is_perfect(self) -> None:
        self.assertEqual(recall_at_k(set(), ["x"], 1), 1.0)
        self.assertEqual(reciprocal_rank(set(), ["x"]), 0.0)
        self.assertEqual(ndcg_at_k({}, ["x"], 1), 1.0)

    def test_dataset_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                '{"id":"one","objective":"a","fixture":"repo"}\n'
                '{"id":"one","objective":"b","fixture":"repo"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_jsonl(path)

    def test_packaged_baseline_is_non_empty_and_has_offline_scripts(self) -> None:
        cases = load_baseline()

        self.assertEqual(
            {case.id for case in cases},
            {
                "explain_entrypoint",
                "fix_discount",
                "reject_workspace_escape",
                "locate_invoice_symbol",
                "fix_cross_file_total",
                "trace_order_call_chain",
                "add_slug_regression_test",
                "resume_permission_write",
            },
        )
        self.assertTrue(all(case.fixture_files for case in cases))
        self.assertTrue(all(case.offline_model for case in cases))

    def test_eval_case_rejects_fixture_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, "workspace-relative"):
            EvalCase(
                id="escape",
                objective="invalid fixture",
                fixture="inline",
                fixture_files={"../outside.py": "pass\n"},
            )

    def test_eval_case_rejects_unknown_resume_permission(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown resume permission"):
            EvalCase(
                id="invalid-permission",
                objective="invalid permission",
                fixture="inline",
                resume_permissions=("root_access",),
                fixture_files={"main.py": "pass\n"},
            )

    def test_live_only_case_requires_category_and_forbids_script(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a task category"):
            EvalCase(
                id="missing-category",
                objective="live task",
                fixture="inline",
                execution_mode="live_only",
                fixture_files={"main.py": "pass\n"},
            )

        with self.assertRaisesRegex(ValueError, "cannot define an offline model"):
            EvalCase(
                id="scripted-live",
                objective="live task",
                fixture="inline",
                execution_mode="live_only",
                task_category="read_only",
                fixture_files={"main.py": "pass\n"},
                offline_model=({"text": "done"},),
            )

    def test_eval_case_rejects_expected_forbidden_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "both expected and forbidden"):
            EvalCase(
                id="overlap",
                objective="invalid contract",
                fixture="inline",
                expected_files=("main.py",),
                forbidden_files=("main.py",),
                fixture_files={"main.py": "pass\n"},
            )


if __name__ == "__main__":
    unittest.main()

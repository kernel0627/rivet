from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.evaluation.dataset import load_jsonl
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


if __name__ == "__main__":
    unittest.main()

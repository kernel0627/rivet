from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.benchmark import (
    benchmark_retrieval,
    load_benchmark_queries,
)


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_real_workspace_index_and_retrieval_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "orders.py").write_text(
                "def create_order():\n    return save_order()\n",
                encoding="utf-8",
            )
            (root / "storage.py").write_text(
                "def save_order():\n    return 1\n",
                encoding="utf-8",
            )
            queries_path = root / "queries.json"
            queries_path.write_text(
                json.dumps(
                    [
                        {
                            "query": "save order",
                            "expected_paths": ["storage.py"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            report = benchmark_retrieval(
                root,
                load_benchmark_queries(queries_path),
                repeat=2,
                limit=3,
                embedding_dimension=32,
            )

            self.assertTrue(report["passed"])
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["workspace"]["python_files"], 2)
            self.assertGreaterEqual(report["index"]["chunk_count"], 2)
            self.assertEqual(report["retrieval"]["sparse"]["hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

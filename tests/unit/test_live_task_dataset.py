from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rivet.evaluation.dataset import load_jsonl


class LiveTaskDatasetTests(unittest.TestCase):
    @property
    def dataset_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "benchmarks"
            / "live_tasks_seed.jsonl"
        )

    def test_seed_covers_four_categories_without_fake_trajectories(self) -> None:
        cases = load_jsonl(self.dataset_path)

        self.assertEqual(len(cases), 4)
        self.assertEqual(
            {case.task_category for case in cases},
            {"read_only", "single_file", "cross_file", "iterative"},
        )
        self.assertTrue(all(case.execution_mode == "live_only" for case in cases))
        self.assertTrue(all(not case.offline_model for case in cases))
        self.assertTrue(
            all(
                not set(case.expected_files).intersection(case.forbidden_files)
                for case in cases
            )
        )

    def test_seed_write_tasks_start_with_failing_acceptance_checks(self) -> None:
        cases = [case for case in load_jsonl(self.dataset_path) if case.expected_tests]

        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                for relative, content in case.fixture_files.items():
                    target = workspace / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")

                for command in case.expected_tests:
                    argv = shlex.split(command)
                    if argv[0] in {"python", "python3"}:
                        argv[0] = sys.executable
                    completed = subprocess.run(
                        argv,
                        cwd=workspace,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertNotEqual(
                        completed.returncode,
                        0,
                        f"{case.id} unexpectedly starts green",
                    )


if __name__ == "__main__":
    unittest.main()

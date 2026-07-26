from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from rivet.workspace.boundary import WorkspaceBoundary, WorkspaceViolation
from rivet.workspace.command import ProcessRunner


class ProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner = ProcessRunner(WorkspaceBoundary(self.root))

    async def test_runs_argv_without_shell_and_caps_output(self) -> None:
        result = await self.runner.run(
            [sys.executable, "-c", "print('x' * 1000)"],
            timeout=5,
            max_stdout_bytes=20,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "x" * 20)
        self.assertTrue(result.stdout_truncated)
        self.assertEqual(len(result.command_digest), 64)

    async def test_timeout_terminates_process(self) -> None:
        result = await self.runner.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.05,
        )

        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.exit_code)

    async def test_rejects_unapproved_environment_variable(self) -> None:
        with self.assertRaises(WorkspaceViolation):
            await self.runner.run(
                [sys.executable, "-c", "pass"],
                env={"SECRET_TOKEN": "do-not-forward"},
            )


if __name__ == "__main__":
    unittest.main()

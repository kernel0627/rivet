from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rivet.tools.builtins.filesystem import SearchTextTool
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import ToolProposal
from rivet.tools.executor import ToolExecutor
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.command import ProcessResult


class CapturingRunner:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None

    async def run(self, argv, **kwargs) -> ProcessResult:
        self.argv = tuple(argv)
        return ProcessResult(
            argv=self.argv,
            cwd=".",
            exit_code=1,
            stdout="",
            stderr="",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=1,
            command_digest="digest",
        )


class SearchSecurityTests(unittest.IsolatedAsyncioTestCase):
    @patch(
        "rivet.tools.builtins.filesystem.shutil.which",
        return_value="/usr/bin/rg",
    )
    async def test_query_starting_with_option_is_only_pattern_argument(
        self,
        _which,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = CapturingRunner()
            executor = ToolExecutor(
                ToolCatalog([SearchTextTool()]),
                WorkspaceBoundary(Path(directory)),
            )
            outcome = executor.prepare(
                ToolProposal.from_arguments(
                    tool_call_id="1",
                    ordinal=0,
                    name="search_text",
                    arguments={"query": "--pre=/tmp/untrusted-program"},
                )
            )
            assert outcome.prepared is not None

            result = await executor.execute(
                outcome.prepared,
                services={"process_runner": runner},
            )

            self.assertTrue(result.ok)
            assert runner.argv is not None
            self.assertIn("--no-config", runner.argv)
            pattern_index = runner.argv.index("-e")
            self.assertEqual(
                runner.argv[pattern_index + 1],
                "--pre=/tmp/untrusted-program",
            )
            self.assertEqual(runner.argv[-2], "--")
            self.assertEqual(runner.argv[-1], str(Path(directory).resolve()))


if __name__ == "__main__":
    unittest.main()

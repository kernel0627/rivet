from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rivet.tools.builtins.git import GitDiffTool, GitStatusTool
from rivet.tools.builtins.process import RunCommandTool, RunTestsTool
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    PermissionDecision,
    PermissionOutcome,
    ToolProposal,
)
from rivet.tools.executor import ToolExecutor
from rivet.tools.results import ErrorKind
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.command import ProcessResult


class FakeRunner:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    async def run(self, argv, **kwargs) -> ProcessResult:
        normalized = tuple(argv)
        self.calls.append((normalized, kwargs))
        return ProcessResult(
            argv=normalized,
            cwd=str(kwargs.get("cwd", ".")),
            exit_code=self.exit_code,
            stdout="output\n",
            stderr="failure\n" if self.exit_code else "",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=1,
            command_digest="d" * 64,
        )


class ProcessAndGitToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.boundary = WorkspaceBoundary(self.root)

    async def test_run_command_passes_untrusted_text_as_one_argv_item(self) -> None:
        executor = ToolExecutor(
            ToolCatalog([RunCommandTool()]),
            self.boundary,
        )
        runner = FakeRunner()
        outcome = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="1",
                ordinal=0,
                name="run_command",
                arguments={"argv": ["printf", "$(touch outside)"]},
            )
        )
        assert outcome.prepared is not None
        permission = PermissionDecision(
            PermissionOutcome.ALLOW,
            outcome.prepared.prepared_digest,
        )

        result = await executor.execute(
            outcome.prepared,
            permission_decision=permission,
            services={"process_runner": runner},
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            runner.calls[0][0],
            ("printf", "$(touch outside)"),
        )

    async def test_failed_test_command_is_verification_failure(self) -> None:
        executor = ToolExecutor(
            ToolCatalog([RunTestsTool()]),
            self.boundary,
        )
        runner = FakeRunner(exit_code=1)
        outcome = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="1",
                ordinal=0,
                name="run_tests",
                arguments={"argv": ["python", "-m", "pytest"]},
            )
        )
        assert outcome.prepared is not None
        permission = PermissionDecision(
            PermissionOutcome.ALLOW,
            outcome.prepared.prepared_digest,
        )

        result = await executor.execute(
            outcome.prepared,
            permission_decision=permission,
            services={"process_runner": runner},
        )

        self.assertEqual(result.error_kind, ErrorKind.VERIFICATION_FAILED)
        self.assertEqual(result.content[0].exit_code, 1)

    @patch("rivet.tools.builtins.git.shutil.which", return_value="/usr/bin/git")
    async def test_git_diff_disables_external_programs_and_terminates_options(
        self,
        _which,
    ) -> None:
        executor = ToolExecutor(
            ToolCatalog([GitDiffTool()]),
            self.boundary,
        )
        runner = FakeRunner()
        outcome = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="1",
                ordinal=0,
                name="git_diff",
                arguments={"paths": ["--malicious"]},
            )
        )
        assert outcome.prepared is not None

        result = await executor.execute(
            outcome.prepared,
            services={"process_runner": runner},
        )

        self.assertTrue(result.ok)
        argv, kwargs = runner.calls[0]
        self.assertIn("--no-ext-diff", argv)
        self.assertIn("--no-textconv", argv)
        separator = argv.index("--")
        self.assertEqual(argv[separator + 1], "--malicious")
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")

    @patch("rivet.tools.builtins.git.shutil.which", return_value="/usr/bin/git")
    async def test_git_status_is_safe_read_without_permission_prompt(
        self,
        _which,
    ) -> None:
        executor = ToolExecutor(
            ToolCatalog([GitStatusTool()]),
            self.boundary,
        )
        runner = FakeRunner()
        outcome = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="1",
                ordinal=0,
                name="git_status",
                arguments={},
            )
        )
        assert outcome.prepared is not None

        result = await executor.execute(
            outcome.prepared,
            services={"process_runner": runner},
        )

        self.assertTrue(result.ok)
        self.assertIn("GIT_OPTIONAL_LOCKS", runner.calls[0][1]["env"])


if __name__ == "__main__":
    unittest.main()

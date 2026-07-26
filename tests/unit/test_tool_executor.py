from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rivet.tools.builtins.filesystem import ReadFileTool
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    EffectClass,
    PermissionClass,
    PermissionDecision,
    PermissionOutcome,
    PermissionScope,
    PreparedTool,
    ToolArguments,
    ToolExecutionContext,
    ToolPreparation,
    ToolPrepareContext,
    ToolProposal,
    ToolSpec,
)
from rivet.tools.executor import ToolExecutor
from rivet.tools.middleware import OutputBudget, OutputBudgetLimiter
from rivet.tools.results import (
    ErrorKind,
    SideEffectState,
    TextBlock,
    ToolResultStatus,
)
from rivet.workspace.boundary import WorkspaceBoundary


class NoArguments(ToolArguments):
    pass


class CancelledExecuteTool:
    spec = ToolSpec(
        name="cancelled_execute",
        version="1.0.0",
        description="Raise cancellation after a non-read action starts.",
        input_model=NoArguments,
        output_types=(TextBlock,),
        effect=EffectClass.EXECUTE,
        permission=PermissionClass.PROCESS_EXECUTE,
        default_timeout=1.0,
        idempotent=False,
        parallel_safe=False,
    )

    def prepare(
        self,
        arguments: NoArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        return ToolPreparation(normalized_arguments=arguments.model_dump(mode="json"))

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ):
        raise asyncio.CancelledError


def make_proposal(
    tool_call_id: str,
    name: str,
    arguments: dict | None = None,
    *,
    raw_arguments: str | None = None,
    ordinal: int = 0,
) -> ToolProposal:
    if raw_arguments is not None:
        return ToolProposal(
            tool_call_id=tool_call_id,
            ordinal=ordinal,
            name=name,
            raw_arguments=raw_arguments,
        )
    return ToolProposal.from_arguments(
        tool_call_id=tool_call_id,
        ordinal=ordinal,
        name=name,
        arguments=arguments or {},
    )


class ToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "sample.py").write_text(
            "first = 1\nsecond = 2\n",
            encoding="utf-8",
        )
        self.catalog = ToolCatalog([ReadFileTool()])
        self.executor = ToolExecutor(
            self.catalog,
            WorkspaceBoundary(self.root),
        )

    def test_prepare_validates_and_normalizes_pydantic_arguments(self) -> None:
        outcome = self.executor.prepare(
            make_proposal(
                tool_call_id="call-1",
                name="read_file",
                raw_arguments='{"path":"./sample.py"}',
            )
        )

        self.assertTrue(outcome.ok)
        assert outcome.prepared is not None
        self.assertEqual(outcome.prepared.normalized_arguments["path"], "sample.py")
        self.assertEqual(outcome.prepared.normalized_arguments["start_line"], 1)
        self.assertEqual(len(outcome.prepared.prepared_digest), 64)
        self.assertEqual(
            outcome.prepared.recompute_digest(),
            outcome.prepared.prepared_digest,
        )

    def test_prepare_returns_typed_errors(self) -> None:
        unknown = self.executor.prepare(make_proposal("1", "missing", {}))
        invalid = self.executor.prepare(
            make_proposal("2", "read_file", {"path": "sample.py", "extra": True})
        )
        escaping = self.executor.prepare(make_proposal("3", "read_file", {"path": "../outside.py"}))

        assert unknown.error is not None
        assert invalid.error is not None
        assert escaping.error is not None
        self.assertEqual(unknown.error.error_kind, ErrorKind.TOOL_NOT_FOUND)
        self.assertEqual(invalid.error.error_kind, ErrorKind.TOOL_ARGUMENT_ERROR)
        self.assertEqual(escaping.error.error_kind, ErrorKind.WORKSPACE_VIOLATION)

    async def test_execute_safe_read_and_enforces_output_budget(self) -> None:
        executor = ToolExecutor(
            self.catalog,
            WorkspaceBoundary(self.root),
            output_limiter=OutputBudgetLimiter(OutputBudget(max_chars=8)),
        )
        outcome = executor.prepare(make_proposal("1", "read_file", {"path": "sample.py"}))
        assert outcome.prepared is not None

        result = await executor.execute(outcome.prepared)

        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.content[0].code), 8)
        self.assertIsNotNone(result.workspace_revision)

    async def test_execute_rejects_tampered_prepared_action(self) -> None:
        outcome = self.executor.prepare(make_proposal("1", "read_file", {"path": "sample.py"}))
        assert outcome.prepared is not None
        tampered = replace(
            outcome.prepared,
            normalized_arguments={
                **outcome.prepared.normalized_arguments,
                "path": "other.py",
            },
        )

        result = await self.executor.execute(tampered)

        self.assertEqual(result.error_kind, ErrorKind.TOOL_ARGUMENT_ERROR)

    async def test_cancelled_non_read_action_has_uncertain_side_effects(self) -> None:
        executor = ToolExecutor(
            ToolCatalog([CancelledExecuteTool()]),
            WorkspaceBoundary(self.root),
        )
        outcome = executor.prepare(make_proposal("1", "cancelled_execute", {}))
        assert outcome.prepared is not None
        decision = PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest=outcome.prepared.prepared_digest,
            scope=PermissionScope.ONCE,
        )

        result = await executor.execute(
            outcome.prepared,
            permission_decision=decision,
        )

        self.assertEqual(result.status, ToolResultStatus.CANCELLED)
        self.assertEqual(result.error_kind, ErrorKind.TOOL_CANCELLED)
        self.assertEqual(result.side_effect_state, SideEffectState.UNCERTAIN)

    async def test_permission_decision_is_bound_to_prepared_digest(self) -> None:
        outcome = self.executor.prepare(make_proposal("1", "read_file", {"path": "sample.py"}))
        assert outcome.prepared is not None
        decision = PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest="0" * 64,
            scope=PermissionScope.ONCE,
        )

        result = await self.executor.execute(
            outcome.prepared,
            permission_decision=decision,
        )

        self.assertEqual(result.status, ToolResultStatus.DENIED)
        self.assertEqual(result.error_kind, ErrorKind.TOOL_PERMISSION_DENIED)


if __name__ == "__main__":
    unittest.main()

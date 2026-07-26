from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.tools.builtins.patch import ApplyPatchTool
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    PermissionDecision,
    PermissionOutcome,
    ToolProposal,
)
from rivet.tools.executor import ToolExecutor
from rivet.tools.results import ErrorKind, SideEffectState, ToolResultStatus
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.checkpoint import FileCheckpointService


class CountingCheckpointService(FileCheckpointService):
    def __init__(self, artifact_root: Path) -> None:
        super().__init__(artifact_root)
        self.create_count = 0

    def create(self, **kwargs):
        self.create_count += 1
        return super().create(**kwargs)


class PatchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temporary = Path(self.temporary.name)
        self.root = temporary / "workspace"
        self.root.mkdir()
        self.state = temporary / "state"
        self.path = self.root / "sample.py"
        self.path.write_text("value = 1\n", encoding="utf-8")
        self.boundary = WorkspaceBoundary(self.root)

    def _prepare(self, executor: ToolExecutor):
        outcome = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="call-1",
                ordinal=0,
                name="apply_patch",
                arguments={
                    "edits": [
                        {
                            "path": "sample.py",
                            "old_text": "value = 1",
                            "new_text": "value = 2",
                        }
                    ]
                },
            )
        )
        assert outcome.prepared is not None
        return outcome.prepared

    async def test_write_pauses_without_explicit_permission(self) -> None:
        executor = ToolExecutor(
            ToolCatalog([ApplyPatchTool()]),
            self.boundary,
            checkpoint_service=FileCheckpointService(self.state),
        )
        prepared = self._prepare(executor)

        result = await executor.execute(prepared)

        self.assertEqual(result.status, ToolResultStatus.PENDING_PERMISSION)
        self.assertEqual(result.error_kind, ErrorKind.TOOL_PERMISSION_REQUIRED)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 1\n")
        self.assertFalse(self.state.exists())

    async def test_write_requires_checkpoint_service(self) -> None:
        executor = ToolExecutor(ToolCatalog([ApplyPatchTool()]), self.boundary)
        prepared = self._prepare(executor)
        permission = PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest=prepared.prepared_digest,
        )

        result = await executor.execute(
            prepared,
            permission_decision=permission,
        )

        self.assertEqual(result.error_kind, ErrorKind.CHECKPOINT_ERROR)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 1\n")

    async def test_authorized_write_checkpoints_then_applies_atomically(self) -> None:
        executor = ToolExecutor(
            ToolCatalog([ApplyPatchTool()]),
            self.boundary,
            checkpoint_service=FileCheckpointService(self.state),
        )
        prepared = self._prepare(executor)
        permission = PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest=prepared.prepared_digest,
        )

        result = await executor.execute(
            prepared,
            permission_decision=permission,
            execution_metadata={"run_id": "run-1", "turn_id": "turn-1"},
        )

        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.side_effect_state, SideEffectState.APPLIED)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 2\n")
        self.assertIn("checkpoint_id", result.metadata)
        manifests = list((self.state / "manifests").glob("*.json"))
        self.assertEqual(len(manifests), 1)
        self.assertFalse((self.root / ".rivet").exists())

    async def test_two_stage_grant_exposes_checkpoint_before_side_effect(self) -> None:
        checkpoints = CountingCheckpointService(self.state)
        executor = ToolExecutor(
            ToolCatalog([ApplyPatchTool()]),
            self.boundary,
            checkpoint_service=checkpoints,
        )
        prepared = self._prepare(executor)
        permission = PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest=prepared.prepared_digest,
        )

        preflight = await executor.preflight(
            prepared,
            permission_decision=permission,
            execution_metadata={
                "run_id": "run-1",
                "turn_id": "turn-1",
                "tool_execution_id": "execution-1",
            },
        )

        self.assertTrue(preflight.ok)
        assert preflight.grant is not None
        self.assertIsNotNone(preflight.grant.checkpoint)
        self.assertEqual(checkpoints.create_count, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 1\n")

        result = await executor.execute_preflighted(preflight.grant)

        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(checkpoints.create_count, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 2\n")
        assert preflight.grant.checkpoint is not None
        self.assertEqual(
            result.metadata["checkpoint_id"],
            preflight.grant.checkpoint.checkpoint_id,
        )

        repeated = await executor.execute_preflighted(preflight.grant)
        self.assertEqual(repeated.error_kind, ErrorKind.STATE_CONFLICT)


if __name__ == "__main__":
    unittest.main()

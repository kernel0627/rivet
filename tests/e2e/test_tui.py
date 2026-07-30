from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rich.console import Console

from rivet.application import build_application
from rivet.domain import RunStatus
from rivet.interfaces.tui import run_interactive
from rivet.model.fake import FakeModel
from rivet.model.types import ModelResult, ToolProposal


class TuiEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_prompt_resumes_checkpointed_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            state.mkdir()
            target = workspace / "main.py"
            target.write_text("value = 1\n", encoding="utf-8")
            model = FakeModel.scripted(
                [
                    ModelResult(
                        reasoning_content="The requested value needs changing.",
                        tool_proposals=(
                            ToolProposal.from_arguments(
                                tool_call_id="change-value",
                                ordinal=0,
                                name="apply_patch",
                                arguments={
                                    "edits": [
                                        {
                                            "path": "main.py",
                                            "old_text": "value = 1",
                                            "new_text": "value = 2",
                                        }
                                    ]
                                },
                            ),
                        ),
                    ),
                    ModelResult(
                        text="Updated main.py and verified Python syntax."
                    ),
                ]
            )
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={
                    "permissions": {
                        "workspace_write": "ask",
                        "process_execute": "allow",
                    },
                    "retrieval": {"enabled": False},
                },
            )
            console = Console(record=True, force_terminal=False, width=100)
            prompt = AsyncMock(return_value="y")
            try:
                with patch("rivet.interfaces.tui.app._prompt", prompt):
                    outcome = await run_interactive(
                        application,
                        "change value to two",
                        console=console,
                    )
            finally:
                await application.close()

            self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "value = 2\n",
            )
            prompt.assert_awaited_once()
            rendered = console.export_text()
            self.assertIn("Paused: permission_required", rendered)
            self.assertIn("Tool success: apply_patch", rendered)
            self.assertIn("Verification: PASSED", rendered)


if __name__ == "__main__":
    unittest.main()

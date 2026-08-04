from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rich.console import Console

from rivet.application import build_application
from rivet.domain import RunStatus
from rivet.interfaces.tui import run_interactive
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.types import ModelResult, ToolProposal


class TuiEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_tui_accepts_message_and_resumes_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            state.mkdir()
            model = FakeModel(
                responses=(
                    ConditionalResponse(
                        RequestCondition(call_index=0),
                        error=ModelGatewayError(
                            ModelErrorKind.UNAVAILABLE,
                            "temporary outage",
                            retryable=False,
                        ),
                    ),
                    ConditionalResponse(
                        RequestCondition(
                            call_index=1,
                            last_user_contains="try the provider again",
                        ),
                        result=ModelResult(text="recovered"),
                    ),
                )
            )
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={"retrieval": {"enabled": False}},
            )
            console = Console(record=True, force_terminal=False, width=100)
            prompt = AsyncMock(side_effect=("m", "try the provider again"))
            try:
                with patch("rivet.interfaces.tui.app._prompt", prompt):
                    outcome = await run_interactive(
                        application,
                        "finish despite a temporary outage",
                        console=console,
                    )
                events = application.service.events(outcome.run.run_id)
            finally:
                await application.close()

            self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
            self.assertEqual(outcome.final_response, "recovered")
            self.assertIn("user.message", {event.event_type for event in events})
            rendered = console.export_text()
            self.assertIn("[Continue] Paused: provider_unavailable", rendered)
            self.assertIn("[Continue] Run resumed", rendered)

    async def test_cancelling_active_tui_marks_run_cancelled(self) -> None:
        class BlockingModel(FakeModel):
            async def stream(self, request):
                self._select(request)
                await asyncio.sleep(3_600)
                if False:
                    yield

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            state.mkdir()
            model = BlockingModel.scripted([ModelResult(text="too late")])
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={"retrieval": {"enabled": False}},
            )
            console = Console(record=True, force_terminal=False, width=100)
            try:
                running = asyncio.create_task(run_interactive(application, "wait", console=console))
                while not model.requests:
                    await asyncio.sleep(0)
                running.cancel()
                outcome = await running
            finally:
                await application.close()

            self.assertEqual(outcome.run.status, RunStatus.CANCELLED)
            self.assertIn(
                "[Result] Cancelled: user_cancelled",
                console.export_text(),
            )

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
                    ModelResult(text="Updated main.py and verified Python syntax."),
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
            prompt = AsyncMock(side_effect=("r", "k"))
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
            self.assertEqual(outcome.run.permission_grants, ("workspace_write",))
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "value = 2\n",
            )
            self.assertEqual(prompt.await_count, 2)
            rendered = console.export_text()
            self.assertIn("[Continue] Paused: permission_required", rendered)
            self.assertIn(
                "[Continue] Allowed for this run: workspace_write",
                rendered,
            )
            self.assertIn("[Edit] success: apply_patch", rendered)
            self.assertIn("Changed: main.py", rendered)
            self.assertIn("[Test] Verification: PASSED", rendered)
            self.assertIn("[Result] Completed", rendered)

    async def test_completed_write_can_rewind_latest_checkpoint(self) -> None:
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
                        )
                    ),
                    ModelResult(text="Updated main.py and verified Python syntax."),
                ]
            )
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={
                    "permissions": {
                        "workspace_write": "allow",
                        "process_execute": "allow",
                    },
                    "retrieval": {"enabled": False},
                },
            )
            console = Console(record=True, force_terminal=False, width=100)
            try:
                with patch(
                    "rivet.interfaces.tui.app._prompt",
                    AsyncMock(return_value="r"),
                ):
                    outcome = await run_interactive(
                        application,
                        "change value to two",
                        console=console,
                    )
                checkpoints = application.service.checkpoints(outcome.run.run_id)
            finally:
                await application.close()

            self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(checkpoints[0].status.value, "REWOUND")
            self.assertIn("[Edit] Rewound: main.py", console.export_text())


if __name__ == "__main__":
    unittest.main()

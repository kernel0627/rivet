from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.application import build_application
from rivet.domain import RunStatus, Session
from rivet.model.fake import FakeModel
from rivet.model.types import ModelResult, ToolProposal
from rivet.workspace.checkpoint import RewindConflict


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_run_and_close_keep_runtime_state_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            model = FakeModel.scripted([ModelResult(text="ready")])
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={
                    "model": {"model": "fake", "provider": "fake"},
                    "tui": {"enabled": False},
                },
            )
            try:
                outcome = await application.service.run("say ready")
                self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
                self.assertEqual(outcome.final_response, "ready")
                self.assertTrue(application.layout.database_path.is_file())
                self.assertFalse((workspace / ".rivet").exists())
                trace = application.layout.logs_root / "events.jsonl"
                self.assertIn("run.completed", trace.read_text(encoding="utf-8"))
            finally:
                await application.close()

    async def test_checkpoint_can_rewind_once_without_overwriting_external_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            target = workspace / "main.py"
            target.write_text("value = 1\n", encoding="utf-8")
            model = FakeModel.scripted(
                [
                    ModelResult(
                        tool_proposals=(
                            ToolProposal.from_arguments(
                                tool_call_id="write-1",
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
                    ModelResult(text="updated"),
                ]
            )
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={
                    "model": {"model": "fake", "provider": "fake"},
                    "permissions": {"workspace_write": "allow"},
                    "retrieval": {"enabled": True},
                    "tui": {"enabled": False},
                },
            )
            try:
                outcome = await application.service.run("update the value")
                self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
                retriever = application.service.runtime.settings.tool_services[
                    "retriever"
                ]
                self.assertTrue(retriever.search("value 2", limit=5))
                checkpoint = application.service.checkpoints(outcome.run.run_id)[0]
                target.write_text("external = True\n", encoding="utf-8")
                with self.assertRaises(RewindConflict):
                    await application.service.rewind(
                        outcome.run.run_id,
                        checkpoint.checkpoint_id,
                    )

                target.write_text("value = 2\n", encoding="utf-8")
                result = await application.service.rewind(
                    outcome.run.run_id,
                    checkpoint.checkpoint_id,
                )
                self.assertEqual(result.restored_paths, ("main.py",))
                self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
                self.assertTrue(retriever.search("value 1", limit=5))
                self.assertEqual(
                    application.service.checkpoints(outcome.run.run_id)[0].status.value,
                    "REWOUND",
                )
                event_types = [
                    event.event_type
                    for event in application.service.state.list_events(
                        outcome.run.run_id
                    )
                ]
                self.assertIn("checkpoint.rewound", event_types)
                self.assertIn("index.refreshed", event_types)
            finally:
                await application.close()

    async def test_related_runs_share_persisted_session_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            state = root / "state"
            workspace.mkdir()
            model = FakeModel.scripted(
                [
                    ModelResult(text="the service uses port 8080"),
                    ModelResult(text="the prior answer was 8080"),
                ]
            )
            application = build_application(
                workspace,
                model_gateway=model,
                state_root=state,
                overrides={
                    "model": {"model": "fake", "provider": "fake"},
                    "tui": {"enabled": False},
                },
            )
            try:
                session = Session.create(
                    application.service.workspace_record().workspace_id
                )
                first = await application.service.run(
                    "which port does the service use?",
                    session=session,
                )
                second = await application.service.run(
                    "repeat the answer from the prior Run",
                    session=session,
                )

                self.assertEqual(second.run.parent_run_id, first.run.run_id)
                self.assertEqual(len(application.service.runs(session.session_id)), 2)
                second_request_text = "\n".join(
                    message.content or "" for message in model.requests[1].messages
                )
                self.assertIn("the service uses port 8080", second_request_text)
                self.assertIn(first.run.run_id, second_request_text)
            finally:
                await application.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rivet.domain import (
    Artifact,
    Checkpoint,
    CheckpointStatus,
    Event,
    EventActor,
    ModelCallRecord,
    ModelCallStatus,
    Run,
    RunStatus,
    Session,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
    VerificationResult,
    VerificationStatus,
    Workspace,
)
from rivet.domain.common import utc_now
from rivet.state.protocol import (
    LeaseConflictError,
    RecordNotFoundError,
    StateConflictError,
    StateIntegrityError,
    StateMutation,
)
from rivet.state.sqlite import SQLiteStateStore


class SQLiteStateStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = SQLiteStateStore(self.root / "state.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _foundation(self) -> tuple[Workspace, Session, Run]:
        workspace = Workspace.create(self.root / "workspace")
        session = Session.create(workspace.workspace_id)
        run = Run.create(session.session_id, "inspect", workspace.current_revision)
        event = Event.create(
            session_id=session.session_id,
            run_id=run.run_id,
            sequence=1,
            event_type="run.created",
            actor=EventActor.RUNTIME,
        )
        self.store.commit(
            StateMutation(
                workspaces=(workspace,),
                sessions=(session,),
                run=run,
                events=(event,),
            )
        )
        return workspace, session, run

    def _lease(self, run: Run) -> str:
        return self.store.acquire_run_lease(
            run.run_id,
            "test-runtime",
            ttl_seconds=60,
        ).token

    def test_migration_is_idempotent_and_creates_required_tables(self) -> None:
        self.store.initialize()
        connection = sqlite3.connect(self.root / "state.sqlite3")
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertTrue(
                {
                    "schema_migrations",
                    "workspaces",
                    "sessions",
                    "runs",
                    "run_snapshots",
                    "turns",
                    "model_calls",
                    "tool_executions",
                    "permission_requests",
                    "permission_decisions",
                    "checkpoints",
                    "verification_results",
                    "events",
                    "artifacts",
                    "leases",
                }.issubset(tables)
            )
            versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
            self.assertEqual(versions, [(1,)])
        finally:
            connection.close()

    def test_create_and_update_run_commit_event_and_snapshot_atomically(self) -> None:
        _, session, created = self._foundation()
        loaded = self.store.load_run(created.run_id)
        self.assertEqual(loaded, created)
        self.assertEqual(self.store.load_run(created.run_id, revision=0), created)

        running = replace(
            created,
            status=RunStatus.RUNNING,
            revision=1,
            updated_at=utc_now(),
        )
        event = Event.create(
            session_id=session.session_id,
            run_id=created.run_id,
            sequence=2,
            event_type="run.started",
            actor=EventActor.RUNTIME,
        )
        result = self.store.commit(
            StateMutation(
                run=running,
                expected_run_revision=0,
                lease_token=self._lease(created),
                events=(event,),
            )
        )
        self.assertEqual(result.run_revision, 1)
        self.assertEqual(result.last_event_sequence, 2)
        self.assertEqual(self.store.load_run(created.run_id), running)
        self.assertEqual(
            [item.event_type for item in self.store.list_events(created.run_id)],
            ["run.created", "run.started"],
        )

    def test_revision_conflict_rolls_back_snapshot_and_event(self) -> None:
        _, session, created = self._foundation()
        running = replace(
            created,
            status=RunStatus.RUNNING,
            revision=1,
            updated_at=utc_now(),
        )
        self.store.commit(
            StateMutation(
                run=running,
                expected_run_revision=0,
                lease_token=self._lease(created),
                events=(
                    Event.create(
                        session_id=session.session_id,
                        run_id=created.run_id,
                        sequence=2,
                        event_type="run.started",
                        actor=EventActor.RUNTIME,
                    ),
                ),
            )
        )
        stale = replace(running, revision=2, updated_at=utc_now())
        with self.assertRaises(StateConflictError):
            self.store.commit(
                StateMutation(
                    run=stale,
                    expected_run_revision=0,
                    lease_token=self._lease(created),
                    events=(
                        Event.create(
                            session_id=session.session_id,
                            run_id=created.run_id,
                            sequence=3,
                            event_type="run.stale",
                            actor=EventActor.RUNTIME,
                        ),
                    ),
                )
            )
        self.assertEqual(self.store.load_run(created.run_id).revision, 1)
        self.assertEqual(len(self.store.list_events(created.run_id)), 2)
        with self.assertRaises(RecordNotFoundError):
            self.store.load_run(created.run_id, revision=2)

    def test_event_sequence_gap_rolls_back_run_update(self) -> None:
        _, session, created = self._foundation()
        running = replace(
            created,
            status=RunStatus.RUNNING,
            revision=1,
            updated_at=utc_now(),
        )
        with self.assertRaises(StateConflictError):
            self.store.commit(
                StateMutation(
                    run=running,
                    expected_run_revision=0,
                    lease_token=self._lease(created),
                    events=(
                        Event.create(
                            session_id=session.session_id,
                            run_id=created.run_id,
                            sequence=3,
                            event_type="run.started",
                            actor=EventActor.RUNTIME,
                        ),
                    ),
                )
            )
        self.assertEqual(self.store.load_run(created.run_id).revision, 0)

    def test_database_enforces_only_one_nonterminal_turn(self) -> None:
        _, session, created = self._foundation()
        first = Turn.create(created.run_id, 1)
        second = Turn.create(created.run_id, 2)
        running = replace(
            created,
            status=RunStatus.RUNNING,
            revision=1,
            updated_at=utc_now(),
        )
        with self.assertRaises(StateIntegrityError):
            self.store.commit(
                StateMutation(
                    run=running,
                    expected_run_revision=0,
                    lease_token=self._lease(created),
                    turns=(first, second),
                    events=(
                        Event.create(
                            session_id=session.session_id,
                            run_id=created.run_id,
                            sequence=2,
                            event_type="turn.created",
                            actor=EventActor.RUNTIME,
                        ),
                    ),
                )
            )
        self.assertEqual(self.store.load_run(created.run_id).revision, 0)

    def test_lifecycle_records_round_trip_in_one_atomic_commit(self) -> None:
        _, session, created = self._foundation()
        turn = Turn.create(created.run_id, 1)
        call = ModelCallRecord(
            model_call_id="call_1",
            turn_id=turn.turn_id,
            attempt_no=1,
            provider="fake",
            model="fake-1",
            status=ModelCallStatus.CREATED,
            context_id="context_1",
            request_digest="1" * 64,
        )
        execution = ToolExecutionRecord(
            execution_id="execution_1",
            turn_id=turn.turn_id,
            model_call_id=call.model_call_id,
            tool_call_id="tool_call_1",
            ordinal=0,
            attempt_no=1,
            tool_name="read_file",
            tool_version="1",
            status=ToolExecutionStatus.PROPOSED,
        )
        checkpoint = Checkpoint(
            checkpoint_id="checkpoint_1",
            run_id=created.run_id,
            turn_id=turn.turn_id,
            created_before_execution_id=execution.execution_id,
            status=CheckpointStatus.CREATING,
            scope=("src/main.py",),
            workspace_revision=created.workspace_current_revision,
        )
        verification = VerificationResult(
            verification_id="verification_1",
            run_id=created.run_id,
            status=VerificationStatus.INCONCLUSIVE,
            checks=(),
        )
        artifact = Artifact(
            artifact_id=f"art_{'2' * 64}",
            sha256="2" * 64,
            media_type="text/plain",
            size_bytes=1,
        )
        running = replace(
            created,
            status=RunStatus.RUNNING,
            active_turn_id=turn.turn_id,
            revision=1,
            updated_at=utc_now(),
        )
        self.store.commit(
            StateMutation(
                run=running,
                expected_run_revision=0,
                lease_token=self._lease(created),
                turns=(turn,),
                model_calls=(call,),
                tool_executions=(execution,),
                checkpoints=(checkpoint,),
                verification_results=(verification,),
                artifacts=(artifact,),
                events=(
                    Event.create(
                        session_id=session.session_id,
                        run_id=created.run_id,
                        turn_id=turn.turn_id,
                        sequence=2,
                        event_type="turn.created",
                        actor=EventActor.RUNTIME,
                    ),
                ),
            )
        )
        self.assertEqual(self.store.load_turn(turn.turn_id), turn)
        self.assertEqual(self.store.load_model_call(call.model_call_id), call)
        self.assertEqual(self.store.load_tool_execution(execution.execution_id), execution)
        self.assertEqual(self.store.load_checkpoint(checkpoint.checkpoint_id), checkpoint)
        self.assertEqual(
            self.store.load_verification_result(verification.verification_id),
            verification,
        )
        self.assertEqual(self.store.load_artifact(artifact.artifact_id), artifact)

    def test_database_enforces_only_one_successful_model_call_per_turn(self) -> None:
        _, session, created = self._foundation()
        turn = Turn.create(created.run_id, 1)
        running = replace(
            created,
            status=RunStatus.RUNNING,
            active_turn_id=turn.turn_id,
            revision=1,
            updated_at=utc_now(),
        )
        lease_token = self._lease(created)
        self.store.commit(
            StateMutation(
                run=running,
                expected_run_revision=0,
                lease_token=lease_token,
                turns=(turn,),
                events=(
                    Event.create(
                        session_id=session.session_id,
                        run_id=created.run_id,
                        turn_id=turn.turn_id,
                        sequence=2,
                        event_type="turn.created",
                        actor=EventActor.RUNTIME,
                    ),
                ),
            )
        )
        now = utc_now()
        calls = tuple(
            ModelCallRecord(
                model_call_id=f"call_{attempt}",
                turn_id=turn.turn_id,
                attempt_no=attempt,
                provider="fake",
                model="fake-1",
                status=ModelCallStatus.SUCCEEDED,
                context_id=f"context_{attempt}",
                request_digest=str(attempt) * 64,
                normalized_response={"text": "done"},
                started_at=now,
                ended_at=now,
            )
            for attempt in (1, 2)
        )
        next_run = replace(running, revision=2, updated_at=utc_now())
        with self.assertRaises(StateIntegrityError):
            self.store.commit(
                StateMutation(
                    run=next_run,
                    expected_run_revision=1,
                    lease_token=lease_token,
                    model_calls=calls,
                    events=(
                        Event.create(
                            session_id=session.session_id,
                            run_id=created.run_id,
                            turn_id=turn.turn_id,
                            sequence=3,
                            event_type="model_call.completed",
                            actor=EventActor.RUNTIME,
                        ),
                    ),
                )
            )
        self.assertEqual(self.store.load_run(created.run_id).revision, 1)

    def test_existing_run_cannot_be_updated_without_live_lease(self) -> None:
        _, session, created = self._foundation()
        running = replace(
            created,
            status=RunStatus.RUNNING,
            revision=1,
            updated_at=utc_now(),
        )
        with self.assertRaises(LeaseConflictError):
            self.store.commit(
                StateMutation(
                    run=running,
                    expected_run_revision=0,
                    events=(
                        Event.create(
                            session_id=session.session_id,
                            run_id=created.run_id,
                            sequence=2,
                            event_type="run.started",
                            actor=EventActor.RUNTIME,
                        ),
                    ),
                )
            )
        self.assertEqual(self.store.load_run(created.run_id).revision, 0)


if __name__ == "__main__":
    unittest.main()

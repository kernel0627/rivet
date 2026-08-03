from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from rivet.domain import (
    Artifact,
    Checkpoint,
    CheckpointStatus,
    Event,
    ModelCallRecord,
    ModelCallStatus,
    Run,
    Session,
    SessionStatus,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
    VerificationResult,
    Workspace,
    validate_run_transition,
    validate_turn_transition,
)
from rivet.domain.common import (
    datetime_from_text,
    datetime_to_text,
    json_dumps,
    json_loads,
    require_aware,
    require_identifier,
    utc_now,
)
from rivet.state.protocol import (
    CommitResult,
    LeaseConflictError,
    RecordNotFoundError,
    RunLease,
    StateConflictError,
    StateIntegrityError,
    StateMutation,
    StateStoreError,
)
from rivet.state.sqlite.migrations import apply_migrations

T = TypeVar("T")


class SQLiteStateStore:
    """SQLite-backed snapshots, lifecycle records, Events, and Run leases."""

    def __init__(self, database_path: Path, *, initialize: bool = True) -> None:
        self.database_path = database_path.expanduser().resolve(strict=False)
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._configure()
        if initialize:
            self.initialize()

    def _configure(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 5000")

    def initialize(self) -> None:
        with self._lock:
            self._ensure_open()
            apply_migrations(self._connection)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> SQLiteStateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def commit(self, mutation: StateMutation) -> CommitResult:
        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for workspace in mutation.workspaces:
                    self._upsert_workspace(workspace)
                for session in mutation.sessions:
                    self._upsert_session(session)

                self._persist_run(
                    mutation.run,
                    mutation.expected_run_revision,
                    mutation.lease_token,
                )

                for turn in mutation.turns:
                    self._upsert_turn(turn)
                for call in mutation.model_calls:
                    self._upsert_model_call(call)
                for execution in mutation.tool_executions:
                    self._upsert_tool_execution(execution)
                for checkpoint in mutation.checkpoints:
                    self._upsert_checkpoint(checkpoint)
                for verification in mutation.verification_results:
                    self._insert_verification(verification)
                for artifact in mutation.artifacts:
                    self._insert_artifact(artifact)

                last_sequence = self._append_events(mutation.events, mutation.run)
                self._validate_mutation_membership(mutation)
                self._connection.commit()
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise StateIntegrityError(str(error)) from error
            except Exception:
                self._connection.rollback()
                raise

        run = mutation.run
        return CommitResult(
            run_id=run.run_id if run else None,
            run_revision=run.revision if run else None,
            last_event_sequence=last_sequence,
        )

    def _persist_run(
        self,
        run: Run | None,
        expected_revision: int | None,
        lease_token: str | None,
    ) -> Run | None:
        if run is None:
            return None
        row = self._connection.execute(
            "SELECT snapshot_json, revision FROM runs WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        snapshot = json_dumps(run.to_dict())
        if row is None:
            if expected_revision is not None:
                raise StateConflictError("new Run cannot have an expected revision")
            if run.revision != 0:
                raise StateConflictError("new Run must start at revision zero")
            self._connection.execute(
                """
                INSERT INTO runs(
                    run_id, session_id, parent_run_id, objective, status,
                    active_turn_id, revision, snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.session_id,
                    run.parent_run_id,
                    run.objective,
                    run.status.value,
                    run.active_turn_id,
                    run.revision,
                    snapshot,
                    datetime_to_text(run.created_at),
                    datetime_to_text(run.updated_at),
                ),
            )
            previous = None
        else:
            actual_revision = int(row["revision"])
            if expected_revision is None or actual_revision != expected_revision:
                raise StateConflictError(
                    f"Run {run.run_id} revision is {actual_revision}, expected {expected_revision}"
                )
            self._require_live_lease(run.run_id, lease_token)
            previous = Run.from_dict(json_loads(str(row["snapshot_json"])))
            validate_run_transition(previous, run)
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET session_id = ?, parent_run_id = ?, objective = ?, status = ?,
                    active_turn_id = ?, revision = ?, snapshot_json = ?, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    run.session_id,
                    run.parent_run_id,
                    run.objective,
                    run.status.value,
                    run.active_turn_id,
                    run.revision,
                    snapshot,
                    datetime_to_text(run.updated_at),
                    run.run_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(f"Run {run.run_id} changed concurrently")

        self._connection.execute(
            """
            INSERT INTO run_snapshots(run_id, revision, snapshot_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run.run_id, run.revision, snapshot, datetime_to_text(utc_now())),
        )
        return previous

    def _require_live_lease(self, run_id: str, lease_token: str | None) -> None:
        if lease_token is None:
            raise LeaseConflictError(f"Run {run_id} update requires a write lease")
        row = self._connection.execute(
            "SELECT token, expires_at FROM leases WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None or str(row["token"]) != lease_token:
            raise LeaseConflictError(f"Run {run_id} lease token is invalid")
        expires_at = datetime_from_text(str(row["expires_at"]))
        assert expires_at is not None
        if expires_at <= utc_now():
            raise LeaseConflictError(f"Run {run_id} write lease has expired")

    def _upsert_workspace(self, workspace: Workspace) -> None:
        existing = self._connection.execute(
            """
            SELECT canonical_root, base_revision, created_at
            FROM workspaces WHERE workspace_id = ?
            """,
            (workspace.workspace_id,),
        ).fetchone()
        if existing is not None:
            immutable = (
                str(existing["canonical_root"]) == workspace.canonical_root
                and str(existing["base_revision"]) == workspace.base_revision
                and str(existing["created_at"]) == datetime_to_text(workspace.created_at)
            )
            if not immutable:
                raise StateConflictError("Workspace identity and base revision are immutable")
        self._connection.execute(
            """
            INSERT INTO workspaces(
                workspace_id, canonical_root, display_name, repository_type,
                base_revision, current_revision, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                canonical_root = excluded.canonical_root,
                display_name = excluded.display_name,
                repository_type = excluded.repository_type,
                base_revision = excluded.base_revision,
                current_revision = excluded.current_revision,
                snapshot_json = excluded.snapshot_json
            """,
            (
                workspace.workspace_id,
                workspace.canonical_root,
                workspace.display_name,
                workspace.repository_type.value,
                workspace.base_revision,
                workspace.current_revision,
                json_dumps(workspace.to_dict()),
                datetime_to_text(workspace.created_at),
            ),
        )

    def _upsert_session(self, session: Session) -> None:
        existing = self._connection.execute(
            "SELECT workspace_id, created_at FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["workspace_id"]) != session.workspace_id:
                raise StateConflictError("Session cannot move to another Workspace")
            if str(existing["created_at"]) != datetime_to_text(session.created_at):
                raise StateConflictError("Session created_at is immutable")
            previous = self.load_session(session.session_id)
            if previous.status is SessionStatus.ARCHIVED and session.status is SessionStatus.ACTIVE:
                raise StateConflictError("ARCHIVED Sessions cannot be reactivated")
            if session.updated_at < previous.updated_at:
                raise StateConflictError("Session updated_at cannot move backwards")
        self._connection.execute(
            """
            INSERT INTO sessions(
                session_id, workspace_id, status, snapshot_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                status = excluded.status,
                snapshot_json = excluded.snapshot_json,
                updated_at = excluded.updated_at
            """,
            (
                session.session_id,
                session.workspace_id,
                session.status.value,
                json_dumps(session.to_dict()),
                datetime_to_text(session.created_at),
                datetime_to_text(session.updated_at),
            ),
        )

    def _upsert_turn(self, turn: Turn) -> None:
        row = self._connection.execute(
            "SELECT snapshot_json FROM turns WHERE turn_id = ?",
            (turn.turn_id,),
        ).fetchone()
        snapshot = json_dumps(turn.to_dict())
        if row is None:
            if turn.revision != 0:
                raise StateConflictError("new Turn must start at revision zero")
            self._connection.execute(
                """
                INSERT INTO turns(
                    turn_id, run_id, ordinal, status, phase, revision,
                    snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.run_id,
                    turn.ordinal,
                    turn.status.value,
                    turn.phase.value,
                    turn.revision,
                    snapshot,
                    datetime_to_text(turn.created_at),
                ),
            )
            return
        previous = Turn.from_dict(json_loads(str(row["snapshot_json"])))
        if previous == turn:
            return
        validate_turn_transition(previous, turn)
        self._connection.execute(
            """
            UPDATE turns
            SET status = ?, phase = ?, revision = ?, snapshot_json = ?
            WHERE turn_id = ?
            """,
            (turn.status.value, turn.phase.value, turn.revision, snapshot, turn.turn_id),
        )

    def _upsert_model_call(self, call: ModelCallRecord) -> None:
        row = self._connection.execute(
            "SELECT snapshot_json FROM model_calls WHERE model_call_id = ?",
            (call.model_call_id,),
        ).fetchone()
        snapshot = json_dumps(call.to_dict())
        if row is not None:
            previous = ModelCallRecord.from_dict(json_loads(str(row["snapshot_json"])))
            if previous == call:
                return
            immutable_fields = (
                "turn_id",
                "attempt_no",
                "provider",
                "model",
                "context_id",
                "request_digest",
                "schema_version",
            )
            if any(
                getattr(previous, field_name) != getattr(call, field_name)
                for field_name in immutable_fields
            ):
                raise StateConflictError("ModelCall identity and request fields are immutable")
            if previous.status.terminal:
                raise StateConflictError("terminal ModelCall records are immutable")
            allowed = {
                ModelCallStatus.CREATED: {
                    ModelCallStatus.IN_FLIGHT,
                    ModelCallStatus.FAILED,
                    ModelCallStatus.CANCELLED,
                },
                ModelCallStatus.IN_FLIGHT: {
                    ModelCallStatus.SUCCEEDED,
                    ModelCallStatus.FAILED,
                    ModelCallStatus.INTERRUPTED,
                    ModelCallStatus.CANCELLED,
                },
            }
            if call.status not in allowed.get(previous.status, set()):
                raise StateConflictError(
                    f"invalid ModelCall transition {previous.status.value} -> {call.status.value}"
                )
        self._connection.execute(
            """
            INSERT INTO model_calls(
                model_call_id, turn_id, attempt_no, provider, model, status,
                request_digest, snapshot_json, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_call_id) DO UPDATE SET
                status = excluded.status,
                snapshot_json = excluded.snapshot_json,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at
            """,
            (
                call.model_call_id,
                call.turn_id,
                call.attempt_no,
                call.provider,
                call.model,
                call.status.value,
                call.request_digest,
                snapshot,
                datetime_to_text(call.started_at),
                datetime_to_text(call.ended_at),
            ),
        )

    def _upsert_tool_execution(self, execution: ToolExecutionRecord) -> None:
        row = self._connection.execute(
            "SELECT snapshot_json FROM tool_executions WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()
        snapshot = json_dumps(execution.to_dict())
        if row is not None:
            previous = ToolExecutionRecord.from_dict(json_loads(str(row["snapshot_json"])))
            if previous == execution:
                return
            immutable_fields = (
                "turn_id",
                "model_call_id",
                "tool_call_id",
                "ordinal",
                "attempt_no",
                "retry_of",
                "tool_name",
                "tool_version",
                "schema_version",
            )
            if any(
                getattr(previous, field_name) != getattr(execution, field_name)
                for field_name in immutable_fields
            ):
                raise StateConflictError("ToolExecution identity fields are immutable")
            if previous.status.terminal:
                raise StateConflictError("terminal ToolExecution records are immutable")
            allowed = {
                ToolExecutionStatus.PROPOSED: {
                    ToolExecutionStatus.PREPARED,
                    ToolExecutionStatus.FAILED,
                    ToolExecutionStatus.CANCELLED,
                },
                ToolExecutionStatus.PREPARED: {
                    ToolExecutionStatus.WAITING_PERMISSION,
                    ToolExecutionStatus.READY,
                    ToolExecutionStatus.DENIED,
                    ToolExecutionStatus.FAILED,
                    ToolExecutionStatus.CANCELLED,
                },
                ToolExecutionStatus.WAITING_PERMISSION: {
                    ToolExecutionStatus.READY,
                    ToolExecutionStatus.DENIED,
                    ToolExecutionStatus.CANCELLED,
                },
                ToolExecutionStatus.READY: {
                    ToolExecutionStatus.RUNNING,
                    ToolExecutionStatus.CANCELLED,
                },
                ToolExecutionStatus.RUNNING: {
                    ToolExecutionStatus.SUCCEEDED,
                    ToolExecutionStatus.FAILED,
                    ToolExecutionStatus.TIMED_OUT,
                    ToolExecutionStatus.CANCELLED,
                    ToolExecutionStatus.INTERRUPTED,
                },
            }
            if execution.status not in allowed.get(previous.status, set()):
                raise StateConflictError(
                    "invalid ToolExecution transition "
                    f"{previous.status.value} -> {execution.status.value}"
                )
        self._connection.execute(
            """
            INSERT INTO tool_executions(
                execution_id, turn_id, model_call_id, tool_call_id, ordinal,
                attempt_no, retry_of, tool_name, tool_version, status,
                prepared_digest, checkpoint_id, snapshot_json, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) DO UPDATE SET
                status = excluded.status,
                prepared_digest = excluded.prepared_digest,
                checkpoint_id = excluded.checkpoint_id,
                snapshot_json = excluded.snapshot_json,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at
            """,
            (
                execution.execution_id,
                execution.turn_id,
                execution.model_call_id,
                execution.tool_call_id,
                execution.ordinal,
                execution.attempt_no,
                execution.retry_of,
                execution.tool_name,
                execution.tool_version,
                execution.status.value,
                execution.prepared_digest,
                execution.checkpoint_id,
                snapshot,
                datetime_to_text(execution.started_at),
                datetime_to_text(execution.ended_at),
            ),
        )

    def _upsert_checkpoint(self, checkpoint: Checkpoint) -> None:
        snapshot = json_dumps(checkpoint.to_dict())
        row = self._connection.execute(
            "SELECT snapshot_json, status FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint.checkpoint_id,),
        ).fetchone()
        if row is not None and str(row["snapshot_json"]) != snapshot:
            previous = Checkpoint.from_dict(json_loads(str(row["snapshot_json"])))
            immutable_fields = (
                "run_id",
                "turn_id",
                "created_before_execution_id",
                "scope",
                "workspace_revision",
                "created_at",
                "schema_version",
            )
            if any(
                getattr(previous, field_name) != getattr(checkpoint, field_name)
                for field_name in immutable_fields
            ):
                raise StateConflictError("Checkpoint identity and scope are immutable")
            allowed = {
                CheckpointStatus.CREATING: {
                    CheckpointStatus.READY,
                    CheckpointStatus.INVALID,
                    CheckpointStatus.FAILED,
                },
                CheckpointStatus.READY: {
                    CheckpointStatus.INVALID,
                    CheckpointStatus.REWOUND,
                },
            }
            if checkpoint.status not in allowed.get(previous.status, set()):
                raise StateConflictError("terminal Checkpoint records are immutable")
        self._connection.execute(
            """
            INSERT INTO checkpoints(
                checkpoint_id, run_id, turn_id, created_before_execution_id,
                status, manifest_digest, artifact_id, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checkpoint_id) DO UPDATE SET
                status = excluded.status,
                manifest_digest = excluded.manifest_digest,
                artifact_id = excluded.artifact_id,
                snapshot_json = excluded.snapshot_json
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.run_id,
                checkpoint.turn_id,
                checkpoint.created_before_execution_id,
                checkpoint.status.value,
                checkpoint.manifest_digest,
                checkpoint.artifact_ref.artifact_id if checkpoint.artifact_ref else None,
                snapshot,
                datetime_to_text(checkpoint.created_at),
            ),
        )

    def _insert_verification(self, result: VerificationResult) -> None:
        snapshot = json_dumps(result.to_dict())
        row = self._connection.execute(
            "SELECT snapshot_json FROM verification_results WHERE verification_id = ?",
            (result.verification_id,),
        ).fetchone()
        if row is not None:
            if str(row["snapshot_json"]) != snapshot:
                raise StateConflictError("VerificationResult records are immutable")
            return
        self._connection.execute(
            """
            INSERT INTO verification_results(
                verification_id, run_id, status, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                result.verification_id,
                result.run_id,
                result.status.value,
                snapshot,
                datetime_to_text(result.created_at),
            ),
        )

    def _insert_artifact(self, artifact: Artifact) -> None:
        snapshot = json_dumps(artifact.to_dict())
        row = self._connection.execute(
            """
            SELECT sha256, media_type, size_bytes, redaction_status
            FROM artifacts WHERE artifact_id = ?
            """,
            (artifact.artifact_id,),
        ).fetchone()
        if row is not None:
            same_content_metadata = (
                str(row["sha256"]) == artifact.sha256
                and str(row["media_type"]) == artifact.media_type
                and int(row["size_bytes"]) == artifact.size_bytes
                and str(row["redaction_status"]) == artifact.redaction_status.value
            )
            if not same_content_metadata:
                raise StateConflictError("Artifact metadata is immutable")
            return
        self._connection.execute(
            """
            INSERT INTO artifacts(
                artifact_id, sha256, media_type, size_bytes, redaction_status,
                snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.sha256,
                artifact.media_type,
                artifact.size_bytes,
                artifact.redaction_status.value,
                snapshot,
                datetime_to_text(artifact.created_at),
            ),
        )

    def _append_events(self, events: tuple[Event, ...], run: Run | None) -> int | None:
        if not events:
            return None
        if run is None:
            raise StateIntegrityError("events require a Run")
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS last_sequence FROM events WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        expected = int(row["last_sequence"]) + 1
        for event in events:
            if event.run_id != run.run_id or event.session_id != run.session_id:
                raise StateIntegrityError("Event identity must match its Run")
            if event.sequence != expected:
                raise StateConflictError(
                    f"Event sequence for Run {run.run_id} must be {expected}, "
                    f"received {event.sequence}"
                )
            self._connection.execute(
                """
                INSERT INTO events(
                    event_id, session_id, run_id, turn_id, sequence, event_type,
                    actor, causation_id, correlation_id, occurred_at,
                    schema_version, payload_json, envelope_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.run_id,
                    event.turn_id,
                    event.sequence,
                    event.event_type,
                    event.actor.value,
                    event.causation_id,
                    event.correlation_id,
                    datetime_to_text(event.occurred_at),
                    event.schema_version,
                    json_dumps(dict(event.payload)),
                    json_dumps(event.to_dict()),
                ),
            )
            expected += 1
        return expected - 1

    def _validate_mutation_membership(self, mutation: StateMutation) -> None:
        run = mutation.run
        if run is None:
            return
        for turn in mutation.turns:
            if turn.run_id != run.run_id:
                raise StateIntegrityError("Turn belongs to a different Run")
        for checkpoint in mutation.checkpoints:
            if checkpoint.run_id != run.run_id:
                raise StateIntegrityError("Checkpoint belongs to a different Run")
        for verification in mutation.verification_results:
            if verification.run_id != run.run_id:
                raise StateIntegrityError("VerificationResult belongs to a different Run")
        for call in mutation.model_calls:
            self._require_turn_in_run(call.turn_id, run.run_id)
        for execution in mutation.tool_executions:
            self._require_turn_in_run(execution.turn_id, run.run_id)
            call_row = self._connection.execute(
                "SELECT turn_id FROM model_calls WHERE model_call_id = ?",
                (execution.model_call_id,),
            ).fetchone()
            if call_row is None or str(call_row["turn_id"]) != execution.turn_id:
                raise StateIntegrityError("ToolExecution ModelCall/Turn relationship is invalid")
        for event in mutation.events:
            if event.turn_id is not None:
                self._require_turn_in_run(event.turn_id, run.run_id)
        nonterminal_rows = self._connection.execute(
            """
            SELECT turn_id FROM turns
            WHERE run_id = ? AND status IN ('CREATED', 'ACTIVE', 'WAITING')
            """,
            (run.run_id,),
        ).fetchall()
        if nonterminal_rows:
            only_turn_id = str(nonterminal_rows[0]["turn_id"])
            if run.active_turn_id != only_turn_id:
                raise StateIntegrityError(
                    "Run active_turn_id must reference its one non-terminal Turn"
                )
        elif run.active_turn_id is not None:
            raise StateIntegrityError("Run active_turn_id must reference a non-terminal Turn")

    def _require_turn_in_run(self, turn_id: str, run_id: str) -> None:
        row = self._connection.execute(
            "SELECT run_id FROM turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if row is None or str(row["run_id"]) != run_id:
            raise StateIntegrityError(f"Turn {turn_id} does not belong to Run {run_id}")

    def load_workspace(self, workspace_id: str) -> Workspace:
        return self._load_snapshot("workspaces", "workspace_id", workspace_id, Workspace.from_dict)

    def load_session(self, session_id: str) -> Session:
        return self._load_snapshot("sessions", "session_id", session_id, Session.from_dict)

    def list_sessions(self, workspace_id: str) -> tuple[Session, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT snapshot_json
                FROM sessions
                WHERE workspace_id = ?
                ORDER BY created_at, session_id
                """,
                (workspace_id,),
            ).fetchall()
        return tuple(
            Session.from_dict(json_loads(str(row["snapshot_json"])))
            for row in rows
        )

    def load_run(self, run_id: str, revision: int | None = None) -> Run:
        if revision is None:
            return self._load_snapshot("runs", "run_id", run_id, Run.from_dict)
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot_json FROM run_snapshots WHERE run_id = ? AND revision = ?",
                (run_id, revision),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Run snapshot {run_id}@{revision} was not found")
        return Run.from_dict(json_loads(str(row["snapshot_json"])))

    def list_runs(self, session_id: str) -> tuple[Run, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT snapshot_json
                FROM runs
                WHERE session_id = ?
                ORDER BY created_at, run_id
                """,
                (session_id,),
            ).fetchall()
        return tuple(
            Run.from_dict(json_loads(str(row["snapshot_json"])))
            for row in rows
        )

    def load_turn(self, turn_id: str) -> Turn:
        return self._load_snapshot("turns", "turn_id", turn_id, Turn.from_dict)

    def load_model_call(self, model_call_id: str) -> ModelCallRecord:
        return self._load_snapshot(
            "model_calls", "model_call_id", model_call_id, ModelCallRecord.from_dict
        )

    def list_model_calls(self, run_id: str) -> tuple[ModelCallRecord, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT model_calls.snapshot_json
                FROM model_calls
                JOIN turns ON turns.turn_id = model_calls.turn_id
                WHERE turns.run_id = ?
                ORDER BY turns.ordinal, model_calls.attempt_no
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            ModelCallRecord.from_dict(json_loads(str(row["snapshot_json"])))
            for row in rows
        )

    def load_tool_execution(self, execution_id: str) -> ToolExecutionRecord:
        return self._load_snapshot(
            "tool_executions",
            "execution_id",
            execution_id,
            ToolExecutionRecord.from_dict,
        )

    def list_tool_executions(
        self,
        run_id: str,
    ) -> tuple[ToolExecutionRecord, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT tool_executions.snapshot_json
                FROM tool_executions
                JOIN turns ON turns.turn_id = tool_executions.turn_id
                WHERE turns.run_id = ?
                ORDER BY turns.ordinal, tool_executions.ordinal,
                         tool_executions.attempt_no
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            ToolExecutionRecord.from_dict(json_loads(str(row["snapshot_json"])))
            for row in rows
        )

    def load_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        return self._load_snapshot(
            "checkpoints", "checkpoint_id", checkpoint_id, Checkpoint.from_dict
        )

    def list_checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT snapshot_json
                FROM checkpoints
                WHERE run_id = ?
                ORDER BY created_at, checkpoint_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            Checkpoint.from_dict(json_loads(str(row["snapshot_json"])))
            for row in rows
        )

    def load_verification_result(self, verification_id: str) -> VerificationResult:
        return self._load_snapshot(
            "verification_results",
            "verification_id",
            verification_id,
            VerificationResult.from_dict,
        )

    def load_artifact(self, artifact_id: str) -> Artifact:
        return self._load_snapshot("artifacts", "artifact_id", artifact_id, Artifact.from_dict)

    def _load_snapshot(
        self,
        table: str,
        key_column: str,
        identifier: str,
        factory: Callable[[Mapping[str, Any]], T],
    ) -> T:
        allowed = {
            ("workspaces", "workspace_id"),
            ("sessions", "session_id"),
            ("runs", "run_id"),
            ("turns", "turn_id"),
            ("model_calls", "model_call_id"),
            ("tool_executions", "execution_id"),
            ("checkpoints", "checkpoint_id"),
            ("verification_results", "verification_id"),
            ("artifacts", "artifact_id"),
        }
        if (table, key_column) not in allowed:
            raise ValueError("unsupported snapshot table")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                f"SELECT snapshot_json FROM {table} WHERE {key_column} = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"{table} record {identifier} was not found")
        return factory(json_loads(str(row["snapshot_json"])))

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> tuple[Event, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT envelope_json FROM events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (run_id, after_sequence, limit),
            ).fetchall()
        return tuple(Event.from_dict(json_loads(str(row["envelope_json"]))) for row in rows)

    def next_event_sequence(self, run_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["value"]) + 1

    def acquire_run_lease(
        self,
        run_id: str,
        owner_id: str,
        *,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> RunLease:
        require_identifier(owner_id, "owner_id")
        if not isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = now or utc_now()
        require_aware(current, "now")
        current = current.astimezone(timezone.utc)
        expires = current + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is not None:
                    existing = self._lease_from_row(row)
                    if existing.expires_at > current and existing.owner_id != owner_id:
                        raise LeaseConflictError(f"Run {run_id} is leased by {existing.owner_id}")
                    token = (
                        existing.token
                        if existing.expires_at > current and existing.owner_id == owner_id
                        else f"lease_{uuid4().hex}"
                    )
                    generation = existing.generation + (
                        0 if existing.expires_at > current and existing.owner_id == owner_id else 1
                    )
                else:
                    token = f"lease_{uuid4().hex}"
                    generation = 1
                self._connection.execute(
                    """
                    INSERT INTO leases(
                        run_id, owner_id, token, acquired_at, expires_at, generation
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        token = excluded.token,
                        acquired_at = excluded.acquired_at,
                        expires_at = excluded.expires_at,
                        generation = excluded.generation
                    """,
                    (
                        run_id,
                        owner_id,
                        token,
                        datetime_to_text(current),
                        datetime_to_text(expires),
                        generation,
                    ),
                )
                self._connection.commit()
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise StateIntegrityError(str(error)) from error
            except Exception:
                self._connection.rollback()
                raise
        return RunLease(run_id, owner_id, token, current, expires, generation)

    def renew_run_lease(
        self,
        run_id: str,
        token: str,
        *,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> RunLease:
        if not isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = now or utc_now()
        require_aware(current, "now")
        current = current.astimezone(timezone.utc)
        expires = current + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise LeaseConflictError(f"Run {run_id} has no lease")
                existing = self._lease_from_row(row)
                if existing.token != token or existing.expires_at <= current:
                    raise LeaseConflictError("lease token is invalid or expired")
                self._connection.execute(
                    "UPDATE leases SET expires_at = ? WHERE run_id = ? AND token = ?",
                    (datetime_to_text(expires), run_id, token),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return replace(existing, expires_at=expires)

    def release_run_lease(self, run_id: str, token: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM leases WHERE run_id = ? AND token = ?",
                (run_id, token),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> RunLease:
        acquired = datetime_from_text(str(row["acquired_at"]))
        expires = datetime_from_text(str(row["expires_at"]))
        assert acquired is not None and expires is not None
        return RunLease(
            run_id=str(row["run_id"]),
            owner_id=str(row["owner_id"]),
            token=str(row["token"]),
            acquired_at=acquired,
            expires_at=expires,
            generation=int(row["generation"]),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise StateStoreError("SQLiteStateStore is closed")

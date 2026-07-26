from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rivet.domain.common import datetime_to_text, utc_now


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="formal_domain_state_foundation",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                canonical_root TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                repository_type TEXT NOT NULL,
                base_revision TEXT NOT NULL,
                current_revision TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                parent_run_id TEXT REFERENCES runs(run_id),
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                active_turn_id TEXT,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, revision)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (run_id, ordinal)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_turns_one_nonterminal
            ON turns(run_id)
            WHERE status IN ('CREATED', 'ACTIVE', 'WAITING')
            """,
            """
            CREATE TABLE IF NOT EXISTS model_calls (
                model_call_id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                UNIQUE (turn_id, attempt_no)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_model_calls_one_success
            ON model_calls(turn_id)
            WHERE status = 'SUCCEEDED'
            """,
            """
            CREATE TABLE IF NOT EXISTS tool_executions (
                execution_id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                model_call_id TEXT NOT NULL REFERENCES model_calls(model_call_id),
                tool_call_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
                retry_of TEXT REFERENCES tool_executions(execution_id),
                tool_name TEXT NOT NULL,
                tool_version TEXT NOT NULL,
                status TEXT NOT NULL,
                prepared_digest TEXT,
                checkpoint_id TEXT,
                snapshot_json TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                UNIQUE (model_call_id, tool_call_id, attempt_no)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS permission_requests (
                request_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                execution_id TEXT NOT NULL REFERENCES tool_executions(execution_id),
                prepared_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS permission_decisions (
                decision_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL REFERENCES permission_requests(request_id),
                decision TEXT NOT NULL,
                scope TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                turn_id TEXT NOT NULL REFERENCES turns(turn_id),
                created_before_execution_id TEXT NOT NULL,
                status TEXT NOT NULL,
                manifest_digest TEXT,
                artifact_id TEXT,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS verification_results (
                verification_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                turn_id TEXT REFERENCES turns(turn_id),
                sequence INTEGER NOT NULL CHECK (sequence >= 1),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                causation_id TEXT,
                correlation_id TEXT,
                occurred_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                UNIQUE (run_id, sequence)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_events_run_type
            ON events(run_id, event_type, sequence)
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                redaction_status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_artifacts_sha256 ON artifacts(sha256)
            """,
            """
            CREATE TABLE IF NOT EXISTS leases (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                owner_id TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK (generation >= 1)
            )
            """,
        ),
    ),
)


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        int(row[0])
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime_to_text(utc_now()),
                ),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

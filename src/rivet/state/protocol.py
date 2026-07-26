from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from rivet.domain import (
    Artifact,
    Checkpoint,
    Event,
    ModelCallRecord,
    Run,
    Session,
    ToolExecutionRecord,
    Turn,
    VerificationResult,
    Workspace,
)


class StateStoreError(RuntimeError):
    """Base error for durable state operations."""


class RecordNotFoundError(StateStoreError):
    """Requested state does not exist."""


class StateConflictError(StateStoreError):
    """Optimistic revision or lifecycle state did not match."""


class StateIntegrityError(StateStoreError):
    """Persisted records would violate a cross-record invariant."""


class LeaseConflictError(StateConflictError):
    """Another live Runtime owns the Run write lease."""


@dataclass(frozen=True, slots=True)
class StateMutation:
    """One atomic state change and its append-only evidence.

    Lifecycle changes for a Run must include the next Run snapshot and at
    least one Event. Workspace, Session, and Artifact metadata can be
    established before the first Run exists.
    """

    run: Run | None = None
    expected_run_revision: int | None = None
    lease_token: str | None = None
    workspaces: tuple[Workspace, ...] = ()
    sessions: tuple[Session, ...] = ()
    turns: tuple[Turn, ...] = ()
    model_calls: tuple[ModelCallRecord, ...] = ()
    tool_executions: tuple[ToolExecutionRecord, ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()
    verification_results: tuple[VerificationResult, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    events: tuple[Event, ...] = ()

    def __post_init__(self) -> None:
        if self.run is None and self.expected_run_revision is not None:
            raise ValueError("expected_run_revision requires a run snapshot")
        if self.run is None and self.lease_token is not None:
            raise ValueError("lease_token requires a run snapshot")
        lifecycle_records = (
            self.turns
            or self.model_calls
            or self.tool_executions
            or self.checkpoints
            or self.verification_results
            or self.events
        )
        if lifecycle_records and self.run is None:
            raise ValueError("Run lifecycle records require a run snapshot")
        if self.run is not None and not self.events:
            raise ValueError("Run state changes require at least one Event")


@dataclass(frozen=True, slots=True)
class CommitResult:
    run_id: str | None
    run_revision: int | None
    last_event_sequence: int | None


@dataclass(frozen=True, slots=True)
class RunLease:
    run_id: str
    owner_id: str
    token: str
    acquired_at: datetime
    expires_at: datetime
    generation: int


@runtime_checkable
class StateStore(Protocol):
    def initialize(self) -> None: ...

    def commit(self, mutation: StateMutation) -> CommitResult: ...

    def load_workspace(self, workspace_id: str) -> Workspace: ...

    def load_session(self, session_id: str) -> Session: ...

    def list_sessions(self, workspace_id: str) -> Sequence[Session]: ...

    def load_run(self, run_id: str, revision: int | None = None) -> Run: ...

    def list_runs(self, session_id: str) -> Sequence[Run]: ...

    def load_turn(self, turn_id: str) -> Turn: ...

    def load_model_call(self, model_call_id: str) -> ModelCallRecord: ...

    def list_model_calls(self, run_id: str) -> Sequence[ModelCallRecord]: ...

    def load_tool_execution(self, execution_id: str) -> ToolExecutionRecord: ...

    def list_tool_executions(
        self,
        run_id: str,
    ) -> Sequence[ToolExecutionRecord]: ...

    def load_checkpoint(self, checkpoint_id: str) -> Checkpoint: ...

    def list_checkpoints(self, run_id: str) -> Sequence[Checkpoint]: ...

    def load_verification_result(self, verification_id: str) -> VerificationResult: ...

    def load_artifact(self, artifact_id: str) -> Artifact: ...

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> Sequence[Event]: ...

    def next_event_sequence(self, run_id: str) -> int: ...

    def acquire_run_lease(
        self,
        run_id: str,
        owner_id: str,
        *,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> RunLease: ...

    def renew_run_lease(
        self,
        run_id: str,
        token: str,
        *,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> RunLease: ...

    def release_run_lease(self, run_id: str, token: str) -> bool: ...

    def close(self) -> None: ...

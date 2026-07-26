from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from rivet.domain import (
    Checkpoint,
    CheckpointStatus,
    Event,
    EventActor,
    RepositoryType,
    Run,
    RunBudget,
    Session,
    SideEffectState,
    ToolExecutionStatus,
    Workspace,
)
from rivet.domain.common import utc_now
from rivet.observability.events import EventPublisher
from rivet.runtime import (
    CancelRun,
    ResumeRun,
    RunOutcome,
    RuntimeEngine,
    StartRun,
)
from rivet.state.protocol import RecordNotFoundError, StateMutation, StateStore
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.checkpoint import FileCheckpointService, RewindResult


@dataclass
class ApplicationService:
    workspace_root: Path
    boundary: WorkspaceBoundary
    runtime: RuntimeEngine
    state: StateStore
    config_snapshot: dict[str, object]
    default_budget: RunBudget
    checkpoint_service: FileCheckpointService | None = None
    event_publisher: EventPublisher | None = None
    index_refresher: Any | None = None

    async def run(
        self,
        objective: str,
        *,
        session: Session | None = None,
        budget: RunBudget | None = None,
    ) -> RunOutcome:
        workspace = self.workspace_record()
        active_session = session or Session.create(workspace.workspace_id)
        previous_runs = tuple(self.state.list_runs(active_session.session_id))
        snapshot = await self.runtime.start_run(
            StartRun(
                workspace=workspace,
                session=active_session,
                objective=objective,
                budget=budget or self.default_budget,
                config_snapshot=self.config_snapshot,
                parent_run_id=(
                    previous_runs[-1].run_id if previous_runs else None
                ),
            )
        )
        return await self.runtime.drive(snapshot.run.run_id)

    async def resume(
        self,
        run_id: str,
        pause_token: str,
        *,
        user_message: str | None = None,
        permission_decisions: dict[str, str] | None = None,
        allow_repeated_action_once: bool = False,
        budget: RunBudget | None = None,
    ) -> RunOutcome:
        return await self.runtime.resume_run(
            ResumeRun(
                run_id=run_id,
                pause_token=pause_token,
                user_message=user_message,
                permission_decisions=permission_decisions or {},
                allow_repeated_action_once=allow_repeated_action_once,
                budget=budget,
            )
        )

    async def cancel(self, run_id: str, *, reason: str = "user_cancelled") -> Run:
        snapshot = await self.runtime.cancel_run(CancelRun(run_id=run_id, reason=reason))
        return snapshot.run

    def inspect(self, run_id: str) -> Run:
        return self.state.load_run(run_id)

    def checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]:
        self.state.load_run(run_id)
        return tuple(self.state.list_checkpoints(run_id))

    def sessions(self) -> tuple[Session, ...]:
        return tuple(
            self.state.list_sessions(self.workspace_record().workspace_id)
        )

    def runs(self, session_id: str) -> tuple[Run, ...]:
        self.state.load_session(session_id)
        return tuple(self.state.list_runs(session_id))

    def events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[Event, ...]:
        self.state.load_run(run_id)
        events: list[Event] = []
        cursor = after_sequence
        while True:
            batch = tuple(
                self.state.list_events(
                    run_id,
                    after_sequence=cursor,
                    limit=1_000,
                )
            )
            if not batch:
                return tuple(events)
            events.extend(batch)
            cursor = batch[-1].sequence
            if len(batch) < 1_000:
                return tuple(events)

    async def rewind(self, run_id: str, checkpoint_id: str) -> RewindResult:
        if self.checkpoint_service is None:
            raise RuntimeError("checkpoint rewind is not configured")
        owner = f"rewind-{uuid.uuid4().hex}"
        lease = self.state.acquire_run_lease(run_id, owner, ttl_seconds=60)
        try:
            run = self.state.load_run(run_id)
            checkpoint = self.state.load_checkpoint(checkpoint_id)
            if checkpoint.run_id != run_id:
                raise ValueError("checkpoint belongs to a different Run")
            if checkpoint.status is not CheckpointStatus.READY:
                raise ValueError(
                    f"checkpoint is {checkpoint.status.value}, expected READY"
                )
            execution = self.state.load_tool_execution(
                checkpoint.created_before_execution_id
            )
            if (
                execution.status is not ToolExecutionStatus.SUCCEEDED
                or execution.side_effect_state is not SideEffectState.APPLIED
                or execution.checkpoint_id != checkpoint_id
            ):
                raise ValueError(
                    "checkpoint is not attached to a successful applied write"
                )
            expected_after = _write_after_hashes(execution.result_summary)
            result = self.checkpoint_service.rewind(
                boundary=self.boundary,
                checkpoint_id=checkpoint_id,
                expected_after_hashes=expected_after,
            )
            index_payload: dict[str, object] | None = None
            index_event_type: str | None = None
            if self.index_refresher is not None:
                try:
                    report = self.index_refresher.refresh()
                    index_payload = {
                        "index_version": report.index_version,
                        "indexed_files": report.indexed_files,
                        "deleted_files": report.deleted_files,
                    }
                    index_event_type = "index.refreshed"
                except Exception as error:
                    index_payload = {
                        "error_type": type(error).__name__,
                        "message": str(error)[:1_000],
                    }
                    index_event_type = "index.refresh_failed"
            now = utc_now()
            updated_checkpoint = replace(
                checkpoint,
                status=CheckpointStatus.REWOUND,
            )
            updated_run = replace(
                run,
                workspace_current_revision=result.workspace_revision,
                revision=run.revision + 1,
                updated_at=now,
            )
            event = Event.create(
                session_id=run.session_id,
                run_id=run.run_id,
                turn_id=checkpoint.turn_id,
                sequence=self.state.next_event_sequence(run.run_id),
                event_type="checkpoint.rewound",
                actor=EventActor.USER,
                payload={
                    "checkpoint_id": checkpoint_id,
                    "restored_paths": list(result.restored_paths),
                    "removed_paths": list(result.removed_paths),
                    "workspace_revision": result.workspace_revision,
                },
            )
            events = [event]
            if index_event_type is not None and index_payload is not None:
                events.append(
                    Event.create(
                        session_id=run.session_id,
                        run_id=run.run_id,
                        turn_id=checkpoint.turn_id,
                        sequence=event.sequence + 1,
                        event_type=index_event_type,
                        actor=EventActor.RUNTIME,
                        payload=index_payload,
                    )
                )
            self.state.commit(
                StateMutation(
                    run=updated_run,
                    expected_run_revision=run.revision,
                    lease_token=lease.token,
                    checkpoints=(updated_checkpoint,),
                    events=tuple(events),
                )
            )
            if self.event_publisher is not None:
                await self.event_publisher.publish(tuple(events))
            return result
        finally:
            self.state.release_run_lease(run_id, lease.token)

    def workspace_record(self) -> Workspace:
        revision = self.boundary.revision(self.boundary.resolve("."))
        candidate = Workspace.create(
            self.workspace_root,
            repository_type=(
                RepositoryType.GIT
                if (self.workspace_root / ".git").exists()
                else RepositoryType.PLAIN
            ),
            base_revision=revision,
            current_revision=revision,
        )
        try:
            existing = self.state.load_workspace(candidate.workspace_id)
        except RecordNotFoundError:
            return candidate
        return replace(existing, current_revision=revision)


def _write_after_hashes(
    result_summary: Mapping[str, object] | None,
) -> dict[str, str | None]:
    if result_summary is None:
        raise ValueError("tool execution lacks result evidence")
    metadata = result_summary.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("tool execution lacks write metadata")
    writes = metadata.get("writes")
    if not isinstance(writes, (list, tuple)):
        raise ValueError("tool execution lacks write-after hashes")
    result: dict[str, str | None] = {}
    for item in writes:
        if not isinstance(item, Mapping):
            raise ValueError("tool write evidence is invalid")
        path = item.get("path")
        after = item.get("after_sha256")
        if not isinstance(path, str) or not isinstance(after, str):
            raise ValueError("tool write evidence is missing path or after_sha256")
        result[path] = after
    if not result:
        raise ValueError("tool execution has no rewindable writes")
    return result

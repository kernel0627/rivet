from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from rivet.context import ContextBudget, ContextRequest
from rivet.context.budget import ContextBudgetExceeded
from rivet.domain import (
    Artifact,
    Checkpoint,
    CheckpointStatus,
    ErrorInfo,
    Event,
    EventActor,
    ModelCallRecord,
    ModelCallStatus,
    ModelUsage,
    Run,
    RunStatus,
    StopAction,
    StopDecision,
    StopScope,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
    TurnPhase,
    TurnStatus,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from rivet.domain import (
    EffectClass as DomainEffectClass,
)
from rivet.domain import (
    ErrorKind as DomainErrorKind,
)
from rivet.domain import (
    PermissionDecision as DomainPermissionDecision,
)
from rivet.domain import (
    SideEffectState as DomainSideEffectState,
)
from rivet.domain.artifacts import RedactionStatus
from rivet.domain.common import new_id, utc_now
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.types import (
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResult,
    ToolProposal,
    Usage,
)
from rivet.observability import EventPublisher, NullEventPublisher
from rivet.reviewer import Reviewer, ReviewerError, ReviewRequest, ReviewResult
from rivet.runtime.contracts import (
    CancelRun,
    ResumeRun,
    RunOutcome,
    RunSnapshot,
    RuntimeClock,
    RuntimeCommandError,
    RuntimeIdFactory,
    RuntimeSettings,
    StartRun,
)
from rivet.runtime.policy import DefaultStopPolicy
from rivet.runtime.projection import project_events
from rivet.runtime.repetition import (
    assess_repetition,
    digest_result,
    fingerprint_action,
)
from rivet.state.artifacts import ContentAddressedArtifactStore
from rivet.state.protocol import StateMutation, StateStore
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    EffectClass,
    ExecutionGrant,
    PermissionDecision,
    PermissionOutcome,
    PermissionScope,
)
from rivet.tools.executor import ToolExecutor
from rivet.tools.results import (
    ErrorKind,
    SideEffectState,
    ToolResult,
    ToolResultStatus,
)
from rivet.verification import (
    VerificationPlan,
    VerificationRequest,
    Verifier,
)


class SystemClock:
    def now(self) -> datetime:
        return utc_now()


class UuidIdFactory:
    def new(self, prefix: str) -> str:
        return new_id(prefix)


class _ModelInvocationFailure(RuntimeError):
    def __init__(self, run: Run, error: ModelGatewayError) -> None:
        super().__init__(str(error))
        self.run = run
        self.error = error


class _ReviewerInvocationFailure(RuntimeError):
    def __init__(self, run: Run, error: ReviewerError) -> None:
        super().__init__(str(error))
        self.run = run
        self.error = error


class RuntimeEngine:
    """The sole coordinator that advances persisted Run state."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        context_engine: Any,
        model_gateway: Any,
        tool_catalog: ToolCatalog,
        tool_executor: ToolExecutor,
        settings: RuntimeSettings | None = None,
        stop_policy: DefaultStopPolicy | None = None,
        event_publisher: EventPublisher | None = None,
        artifact_store: ContentAddressedArtifactStore | None = None,
        clock: RuntimeClock | None = None,
        id_factory: RuntimeIdFactory | None = None,
        verifier: Verifier | None = None,
        verification_plan_factory: Any | None = None,
        reviewer: Reviewer | None = None,
    ) -> None:
        self.state = state_store
        self.context_engine = context_engine
        self.model_gateway = model_gateway
        self.tools = tool_catalog
        self.tool_executor = tool_executor
        self.settings = settings or RuntimeSettings()
        self.stop_policy = stop_policy or DefaultStopPolicy()
        self.events = event_publisher or NullEventPublisher()
        self.artifact_store = artifact_store
        self.clock = clock or SystemClock()
        self.ids = id_factory or UuidIdFactory()
        self.verifier = verifier
        self.verification_plan_factory = verification_plan_factory
        self.reviewer = reviewer
        self.owner_id = self.settings.owner_id or f"runtime_{uuid4().hex}"

    async def start_run(self, command: StartRun) -> RunSnapshot:
        run = Run.create(
            session_id=command.session.session_id,
            objective=command.objective.strip(),
            workspace_revision=command.workspace.current_revision,
            budget=command.budget,
            config_snapshot=command.config_snapshot,
            parent_run_id=command.parent_run_id,
        )
        event = Event.create(
            session_id=run.session_id,
            run_id=run.run_id,
            sequence=1,
            event_type="run.created",
            actor=EventActor.RUNTIME,
            payload={
                "objective": run.objective,
                "workspace_id": command.workspace.workspace_id,
            },
        )
        self.state.commit(
            StateMutation(
                run=run,
                workspaces=(command.workspace,),
                sessions=(command.session,),
                events=(event,),
            )
        )
        await self.events.publish((event,))
        return RunSnapshot(run=run, active_turn=None, last_event_sequence=1)

    async def drive(self, run_id: str) -> RunOutcome:
        run = self.state.load_run(run_id)
        if run.status.terminal or run.status is RunStatus.PAUSED:
            return self._outcome(run)
        lease = self.state.acquire_run_lease(
            run_id,
            self.owner_id,
            ttl_seconds=self.settings.lease_ttl_seconds,
        )
        published_after = self._last_sequence(run_id)
        try:
            run = self.state.load_run(run_id)
            if run.status is RunStatus.CREATED:
                run = await self._update_run(
                    run,
                    lease.token,
                    event_type="run.started",
                    status=RunStatus.RUNNING,
                )
            if run.status is not RunStatus.RUNNING:
                return self._outcome(run, after_sequence=published_after)
            while run.status is RunStatus.RUNNING:
                self.state.renew_run_lease(
                    run.run_id,
                    lease.token,
                    ttl_seconds=self.settings.lease_ttl_seconds,
                )
                decision = self.stop_policy.before_turn(run)
                if decision is not None:
                    run = await self._pause(
                        run,
                        lease.token,
                        decision,
                        cursor={"kind": "new_turn"},
                    )
                    break
                run = await self._run_new_turn(run, lease.token)
            return self._outcome(run, after_sequence=published_after)
        finally:
            self.state.release_run_lease(run_id, lease.token)

    async def resume_run(self, command: ResumeRun) -> RunOutcome:
        run = self.state.load_run(command.run_id)
        if run.status is not RunStatus.PAUSED:
            raise RuntimeCommandError("only a PAUSED Run can be resumed")
        if run.pause_token != command.pause_token:
            raise RuntimeCommandError("pause token is stale or invalid")
        lease = self.state.acquire_run_lease(
            run.run_id,
            self.owner_id,
            ttl_seconds=self.settings.lease_ttl_seconds,
        )
        published_after = self._last_sequence(run.run_id)
        try:
            cursor = _decode_cursor(run.resume_cursor)
            turn = self._active_turn(run)
            turn_update: Turn | None = None
            if turn is not None and turn.status is TurnStatus.WAITING:
                turn_update = replace(
                    turn,
                    status=TurnStatus.ACTIVE,
                    phase=TurnPhase.PREPARE_TOOLS,
                    revision=turn.revision + 1,
                )
            resumed = replace(
                run,
                status=RunStatus.RUNNING,
                budget=command.budget or run.budget,
                stop_decision=None,
                pause_token=None,
                resume_cursor=None,
                revision=run.revision + 1,
                updated_at=self.clock.now(),
            )
            event_specs: list[tuple[str, EventActor, Mapping[str, Any], str | None]] = [
                (
                    "run.resumed",
                    EventActor.USER,
                    {"cursor_kind": cursor.get("kind", "unknown")},
                    turn.turn_id if turn else None,
                )
            ]
            if command.user_message:
                event_specs.append(
                    (
                        "user.message",
                        EventActor.USER,
                        {
                            "message": Message(
                                role=MessageRole.USER,
                                content=command.user_message.strip(),
                            ).to_dict()
                        },
                        turn.turn_id if turn else None,
                    )
                )
            resumed = await self._commit(
                previous=run,
                current=resumed,
                lease_token=lease.token,
                events=event_specs,
                turns=(turn_update,) if turn_update else (),
            )
            if cursor.get("kind") == "tool_batch":
                resumed = await self._resume_tool_batch(
                    resumed,
                    lease.token,
                    cursor,
                    command,
                )
            if resumed.status is RunStatus.RUNNING:
                while resumed.status is RunStatus.RUNNING:
                    decision = self.stop_policy.before_turn(resumed)
                    if decision is not None:
                        resumed = await self._pause(
                            resumed,
                            lease.token,
                            decision,
                            cursor={"kind": "new_turn"},
                        )
                        break
                    resumed = await self._run_new_turn(resumed, lease.token)
            return self._outcome(resumed, after_sequence=published_after)
        finally:
            self.state.release_run_lease(run.run_id, lease.token)

    async def cancel_run(self, command: CancelRun) -> RunSnapshot:
        run = self.state.load_run(command.run_id)
        if run.status.terminal:
            return self._snapshot(run)
        lease = self.state.acquire_run_lease(
            run.run_id,
            self.owner_id,
            ttl_seconds=self.settings.lease_ttl_seconds,
        )
        try:
            turn = self._active_turn(run)
            turn_update = None
            if turn is not None and not turn.status.terminal:
                turn_update = replace(
                    turn,
                    status=TurnStatus.CANCELLED,
                    ended_at=self.clock.now(),
                    revision=turn.revision + 1,
                )
            decision = StopDecision(
                action=StopAction.CANCEL,
                reason=command.reason,
                scope=StopScope.RUN,
                evidence={},
            )
            cancelled = replace(
                run,
                status=RunStatus.CANCELLED,
                active_turn_id=None,
                stop_decision=decision,
                pause_token=None,
                resume_cursor=None,
                revision=run.revision + 1,
                updated_at=self.clock.now(),
            )
            cancelled = await self._commit(
                previous=run,
                current=cancelled,
                lease_token=lease.token,
                events=(
                    (
                        "run.cancelled",
                        EventActor.USER,
                        {"reason": command.reason},
                        turn.turn_id if turn else None,
                    ),
                ),
                turns=(turn_update,) if turn_update else (),
            )
            return self._snapshot(cancelled)
        finally:
            self.state.release_run_lease(run.run_id, lease.token)

    async def recover_run(self, run_id: str) -> RunSnapshot:
        run = self.state.load_run(run_id)
        if run.status is not RunStatus.RUNNING:
            return self._snapshot(run)
        lease = self.state.acquire_run_lease(
            run_id,
            self.owner_id,
            ttl_seconds=self.settings.lease_ttl_seconds,
        )
        try:
            recovering = replace(
                run,
                status=RunStatus.RECOVERING,
                revision=run.revision + 1,
                updated_at=self.clock.now(),
            )
            recovering = await self._commit(
                previous=run,
                current=recovering,
                lease_token=lease.token,
                events=(("recovery.started", EventActor.RUNTIME, {}, run.active_turn_id),),
            )
            turn = self._active_turn(recovering)
            turn_update = None
            if turn is not None and not turn.status.terminal:
                turn_update = replace(
                    turn,
                    status=TurnStatus.CANCELLED,
                    ended_at=self.clock.now(),
                    revision=turn.revision + 1,
                )
            now = self.clock.now()
            model_updates: list[ModelCallRecord] = []
            for call in self.state.list_model_calls(run_id):
                if call.status is ModelCallStatus.IN_FLIGHT:
                    model_updates.append(
                        replace(
                            call,
                            status=ModelCallStatus.INTERRUPTED,
                            error=ErrorInfo(
                                kind=DomainErrorKind.MODEL_TRANSPORT_ERROR,
                                message="Runtime process ended during the model call",
                                retryable=True,
                            ),
                            ended_at=now,
                        )
                    )
                elif call.status is ModelCallStatus.CREATED:
                    model_updates.append(
                        replace(
                            call,
                            status=ModelCallStatus.CANCELLED,
                            started_at=call.started_at or now,
                            ended_at=now,
                        )
                    )

            tool_updates: list[ToolExecutionRecord] = []
            uncertain_execution_ids: list[str] = []
            for execution in self.state.list_tool_executions(run_id):
                if execution.status.terminal:
                    continue
                was_running = execution.status is ToolExecutionStatus.RUNNING
                uncertain = (
                    was_running
                    and execution.effect_class is not DomainEffectClass.READ
                )
                if uncertain:
                    uncertain_execution_ids.append(execution.execution_id)
                tool_updates.append(
                    replace(
                        execution,
                        status=(
                            ToolExecutionStatus.INTERRUPTED
                            if was_running
                            else ToolExecutionStatus.CANCELLED
                        ),
                        error=(
                            ErrorInfo(
                                kind=DomainErrorKind.TOOL_CANCELLED,
                                message="Runtime process ended during tool execution",
                                retryable=not uncertain,
                            )
                            if was_running
                            else None
                        ),
                        side_effect_state=(
                            DomainSideEffectState.UNCERTAIN
                            if uncertain
                            else DomainSideEffectState.NONE
                        ),
                        started_at=execution.started_at or now,
                        ended_at=now,
                    )
                )

            decision = (
                StopDecision(
                    action=StopAction.PAUSE,
                    reason="uncertain_side_effect",
                    scope=StopScope.RUN,
                    resumable=True,
                    resume_requirements=(
                        "inspect_workspace_and_reconcile_interrupted_tools",
                    ),
                    evidence={"execution_ids": uncertain_execution_ids},
                    user_message=(
                        "A tool was interrupted after execution started; "
                        "inspect the workspace before resuming."
                    ),
                )
                if uncertain_execution_ids
                else self.stop_policy.process_interrupted()
            )
            paused = replace(
                recovering,
                status=RunStatus.PAUSED,
                active_turn_id=None,
                stop_decision=decision,
                pause_token=self.ids.new("pause"),
                resume_cursor=_encode_cursor({"kind": "new_turn"}),
                revision=recovering.revision + 1,
                updated_at=self.clock.now(),
            )
            paused = await self._commit(
                previous=recovering,
                current=paused,
                lease_token=lease.token,
                events=(
                    (
                        "recovery.reconciled",
                        EventActor.RUNTIME,
                        {
                            "decision": decision.to_dict(),
                            "model_calls_reconciled": len(model_updates),
                            "tool_executions_reconciled": len(tool_updates),
                            "uncertain_execution_ids": uncertain_execution_ids,
                        },
                        turn.turn_id if turn else None,
                    ),
                    (
                        "run.paused",
                        EventActor.RUNTIME,
                        {"reason": decision.reason},
                        None,
                    ),
                ),
                turns=(turn_update,) if turn_update else (),
                model_calls=tuple(model_updates),
                tool_executions=tuple(tool_updates),
            )
            return self._snapshot(paused)
        finally:
            self.state.release_run_lease(run_id, lease.token)

    async def _run_new_turn(self, run: Run, lease_token: str) -> Run:
        now = self.clock.now()
        turn = Turn(
            turn_id=self.ids.new("turn"),
            run_id=run.run_id,
            ordinal=run.usage.turns + 1,
            status=TurnStatus.ACTIVE,
            phase=TurnPhase.BUILD_CONTEXT,
            started_at=now,
            created_at=now,
        )
        current = replace(
            run,
            active_turn_id=turn.turn_id,
            usage=replace(run.usage, turns=run.usage.turns + 1),
            revision=run.revision + 1,
            updated_at=now,
        )
        current = await self._commit(
            previous=run,
            current=current,
            lease_token=lease_token,
            events=(("turn.started", EventActor.RUNTIME, {"ordinal": turn.ordinal}, turn.turn_id),),
            turns=(turn,),
        )
        projection = project_events(self._all_events(run.run_id))
        try:
            envelope = await self.context_engine.build(
                ContextRequest(
                    objective=current.objective,
                    budget=ContextBudget(
                        max_input_tokens=self.settings.context_input_tokens_per_call,
                        reserved_output_tokens=self.settings.output_tokens_per_call,
                        model_context_window=self.settings.model_context_window,
                    ),
                    system_instructions=self.settings.system_instructions,
                    project_instructions=self.settings.project_instructions,
                    session_summary=self._session_summary(current),
                    recent_messages=projection.messages,
                    tool_schemas=self.tools.model_schemas(),
                    run_id=current.run_id,
                    workspace_revision=current.workspace_current_revision,
                )
            )
        except ContextBudgetExceeded as error:
            failed_turn = replace(
                turn,
                status=TurnStatus.FAILED,
                ended_at=self.clock.now(),
                revision=turn.revision + 1,
            )
            return await self._pause(
                current,
                lease_token,
                StopDecision(
                    action=StopAction.PAUSE,
                    reason="budget_exhausted",
                    scope=StopScope.RUN,
                    resumable=True,
                    resume_requirements=("compact_context_or_increase_limit",),
                    evidence={
                        "required_tokens": error.required_tokens,
                        "available_tokens": error.available_tokens,
                    },
                ),
                cursor={"kind": "new_turn"},
                active_turn=failed_turn,
                clear_active_turn=True,
            )

        request = envelope.to_model_request(model=self.settings.model_name)
        call = ModelCallRecord(
            model_call_id=self.ids.new("model_call"),
            turn_id=turn.turn_id,
            attempt_no=1,
            provider=self.settings.provider_name,
            model=self.settings.model_name,
            status=ModelCallStatus.IN_FLIGHT,
            context_id=envelope.context_id,
            request_digest=request.digest,
            started_at=self.clock.now(),
        )
        calling_turn = replace(
            turn,
            phase=TurnPhase.CALL_MODEL,
            context_id=envelope.context_id,
            revision=turn.revision + 1,
        )
        calling_run = replace(
            current,
            revision=current.revision + 1,
            updated_at=self.clock.now(),
        )
        calling_run = await self._commit(
            previous=current,
            current=calling_run,
            lease_token=lease_token,
            events=(
                (
                    "context.built",
                    EventActor.RUNTIME,
                    {
                        "context_id": envelope.context_id,
                        "context_digest": envelope.digest,
                        "token_estimate": envelope.token_estimate.to_dict(),
                    },
                    turn.turn_id,
                ),
                (
                    "model_call.started",
                    EventActor.MODEL,
                    {"model_call_id": call.model_call_id, "request_digest": request.digest},
                    turn.turn_id,
                ),
            ),
            turns=(calling_turn,),
            model_calls=(call,),
        )
        while True:
            error_run: Run | None = None
            model_error: ModelGatewayError | None = None
            try:
                calling_run, result = await self._invoke_model(
                    calling_run,
                    calling_turn,
                    request,
                    lease_token,
                )
            except _ModelInvocationFailure as failure:
                error_run = failure.run
                model_error = failure.error
            except asyncio.CancelledError:
                error_run = calling_run
                model_error = ModelGatewayError(
                    ModelErrorKind.CANCELLED,
                    "model request was cancelled",
                )
            except ModelGatewayError as error:
                error_run = calling_run
                model_error = error
            except Exception as error:
                error_run = calling_run
                model_error = ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    f"{type(error).__name__}: {str(error)[:1000]}",
                )

            if model_error is None:
                break
            assert error_run is not None
            model_call_limit = error_run.budget.max_model_calls
            budget_allows_retry = (
                model_call_limit is None
                or error_run.usage.model_calls + 1 < model_call_limit
            )
            if (
                model_error.retryable
                and call.attempt_no <= self.settings.model_max_retries
                and budget_allows_retry
            ):
                calling_run, call = await self._retry_model_call(
                    error_run,
                    calling_turn,
                    call,
                    model_error,
                    lease_token,
                )
                continue
            return await self._finish_model_error(
                error_run,
                calling_turn,
                call,
                model_error,
                lease_token,
            )

        completed_call = replace(
            call,
            status=ModelCallStatus.SUCCEEDED,
            normalized_response=result.to_dict(),
            usage=ModelUsage(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cached_input_tokens=result.usage.cached_input_tokens,
                reasoning_tokens=result.usage.reasoning_tokens,
            ),
            ended_at=self.clock.now(),
        )
        next_phase = (
            TurnPhase.PREPARE_TOOLS if result.tool_proposals else TurnPhase.DECIDE
        )
        model_turn = replace(
            calling_turn,
            phase=next_phase,
            revision=calling_turn.revision + 1,
        )
        model_run = replace(
            calling_run,
            usage=replace(
                calling_run.usage,
                model_calls=calling_run.usage.model_calls + 1,
                input_tokens=calling_run.usage.input_tokens + result.usage.input_tokens,
                output_tokens=calling_run.usage.output_tokens + result.usage.output_tokens,
            ),
            revision=calling_run.revision + 1,
            updated_at=self.clock.now(),
        )
        model_run = await self._commit(
            previous=calling_run,
            current=model_run,
            lease_token=lease_token,
            events=(
                (
                    "model_call.completed",
                    EventActor.MODEL,
                    {
                        "model_call_id": call.model_call_id,
                        "finish_reason": result.finish_reason,
                        "message": result.assistant_message.to_dict(),
                        "usage": result.usage.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
            turns=(model_turn,),
            model_calls=(completed_call,),
        )
        if not result.tool_proposals:
            return await self._complete_with_answer(
                model_run,
                model_turn,
                result,
                lease_token,
            )
        return await self._execute_tool_batch(
            model_run,
            model_turn,
            completed_call,
            result.tool_proposals,
            envelope.digest,
            lease_token,
        )

    async def _retry_model_call(
        self,
        run: Run,
        turn: Turn,
        call: ModelCallRecord,
        error: ModelGatewayError,
        lease_token: str,
    ) -> tuple[Run, ModelCallRecord]:
        now = self.clock.now()
        failed_call = replace(
            call,
            status=ModelCallStatus.FAILED,
            error=_model_error_info(error),
            ended_at=now,
        )
        next_call = ModelCallRecord(
            model_call_id=self.ids.new("model_call"),
            turn_id=turn.turn_id,
            attempt_no=call.attempt_no + 1,
            provider=call.provider,
            model=call.model,
            status=ModelCallStatus.IN_FLIGHT,
            context_id=call.context_id,
            request_digest=call.request_digest,
            started_at=now,
        )
        retrying = replace(
            run,
            usage=replace(
                run.usage,
                model_calls=run.usage.model_calls + 1,
            ),
            revision=run.revision + 1,
            updated_at=now,
        )
        retrying = await self._commit(
            previous=run,
            current=retrying,
            lease_token=lease_token,
            events=(
                (
                    "model_call.failed",
                    EventActor.MODEL,
                    {
                        "model_call_id": call.model_call_id,
                        "attempt_no": call.attempt_no,
                        "error": error.to_dict(),
                        "will_retry": True,
                    },
                    turn.turn_id,
                ),
                (
                    "model_call.started",
                    EventActor.MODEL,
                    {
                        "model_call_id": next_call.model_call_id,
                        "attempt_no": next_call.attempt_no,
                        "request_digest": next_call.request_digest,
                    },
                    turn.turn_id,
                ),
            ),
            model_calls=(failed_call, next_call),
        )
        return retrying, next_call

    async def _invoke_model(
        self,
        run: Run,
        turn: Turn,
        request: ModelRequest,
        lease_token: str,
    ) -> tuple[Run, ModelResult]:
        if not self.settings.stream_model:
            return run, await self.model_gateway.complete(request)
        stream_method = getattr(self.model_gateway, "stream", None)
        if not callable(stream_method):
            return run, await self.model_gateway.complete(request)

        current = run
        events: list[ModelEvent] = []
        completed: ModelEvent | None = None
        expected_sequence = 0
        try:
            async for model_event in stream_method(request):
                if not isinstance(model_event, ModelEvent):
                    raise ModelGatewayError(
                        ModelErrorKind.PROTOCOL,
                        "model stream yielded an invalid event",
                    )
                if model_event.sequence != expected_sequence:
                    raise ModelGatewayError(
                        ModelErrorKind.PROTOCOL,
                        "model stream event sequence is not contiguous",
                    )
                if completed is not None:
                    raise ModelGatewayError(
                        ModelErrorKind.PROTOCOL,
                        "model stream emitted events after completion",
                    )
                expected_sequence += 1
                events.append(model_event)
                if model_event.type is ModelEventType.RESPONSE_COMPLETED:
                    completed = model_event
                previous = current
                current = replace(
                    current,
                    revision=current.revision + 1,
                    updated_at=self.clock.now(),
                )
                current = await self._commit(
                    previous=previous,
                    current=current,
                    lease_token=lease_token,
                    events=(
                        (
                            f"model.stream.{model_event.type.value}",
                            EventActor.MODEL,
                            {"model_event": model_event.to_dict()},
                            turn.turn_id,
                        ),
                    ),
                )
        except asyncio.CancelledError as error:
            raise _ModelInvocationFailure(
                current,
                ModelGatewayError(
                    ModelErrorKind.CANCELLED,
                    "model request was cancelled",
                ),
            ) from error
        except ModelGatewayError as error:
            raise _ModelInvocationFailure(current, error) from error
        except Exception as error:
            raise _ModelInvocationFailure(
                current,
                ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    f"{type(error).__name__}: {str(error)[:1000]}",
                ),
            ) from error
        if completed is None:
            raise _ModelInvocationFailure(
                current,
                ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "model stream ended without a completion event",
                ),
            )
        try:
            return current, ModelResult(
                text=completed.text,
                reasoning_content=completed.reasoning_content,
                tool_proposals=completed.tool_proposals,
                finish_reason=completed.finish_reason,
                usage=completed.usage or _last_stream_usage(events),
                provider_request_id=completed.provider_request_id,
                events=tuple(events),
            )
        except ValueError as error:
            raise _ModelInvocationFailure(
                current,
                ModelGatewayError(
                    ModelErrorKind.PROTOCOL,
                    "model stream completion was invalid",
                ),
            ) from error

    async def _execute_tool_batch(
        self,
        run: Run,
        turn: Turn,
        model_call: ModelCallRecord,
        proposals: Sequence[ToolProposal],
        context_digest: str,
        lease_token: str,
        *,
        start_index: int = 0,
        existing_execution: ToolExecutionRecord | None = None,
        permission_decision: PermissionDecision | None = None,
        allow_repeat: bool = False,
    ) -> Run:
        if (
            start_index == 0
            and existing_execution is None
            and permission_decision is None
            and len(proposals) > 1
        ):
            parallel_result = await self._try_parallel_read_batch(
                run,
                turn,
                model_call,
                proposals,
                context_digest,
                lease_token,
            )
            if parallel_result is not None:
                return parallel_result
        results: list[ToolResult] = []
        current_run = run
        current_turn = turn
        for index in range(start_index, len(proposals)):
            tool_limit = current_run.budget.max_tool_executions
            if (
                tool_limit is not None
                and current_run.usage.tool_executions >= tool_limit
            ):
                completed_turn = replace(
                    current_turn,
                    status=TurnStatus.COMPLETED,
                    phase=TurnPhase.DECIDE,
                    ended_at=self.clock.now(),
                    revision=current_turn.revision + 1,
                )
                return await self._pause(
                    current_run,
                    lease_token,
                    self.stop_policy.budget_exhausted(("tool_executions",)),
                    cursor={"kind": "new_turn"},
                    active_turn=completed_turn,
                    clear_active_turn=True,
                )
            proposal = proposals[index]
            execution = existing_execution if index == start_index else None
            preparation = self.tool_executor.prepare(proposal)
            if preparation.error is not None:
                current_run, result = await self._record_prepare_error(
                    current_run,
                    current_turn,
                    model_call,
                    proposal,
                    preparation.error,
                    lease_token,
                )
                results.append(result)
                continue
            assert preparation.prepared is not None
            prepared = preparation.prepared
            if execution is not None:
                if execution.prepared_digest != prepared.prepared_digest:
                    raise RuntimeCommandError("resumed prepared action digest changed")
            else:
                execution = ToolExecutionRecord(
                    execution_id=self.ids.new("tool_execution"),
                    turn_id=current_turn.turn_id,
                    model_call_id=model_call.model_call_id,
                    tool_call_id=proposal.tool_call_id,
                    ordinal=proposal.ordinal,
                    attempt_no=1,
                    tool_name=prepared.name,
                    tool_version=prepared.version,
                    status=ToolExecutionStatus.PREPARED,
                    normalized_arguments=prepared.normalized_arguments,
                    effect_class=_domain_effect(prepared.effect),
                    prepared_digest=prepared.prepared_digest,
                    side_effect_state=DomainSideEffectState.NOT_STARTED,
                )
                next_run = replace(
                    current_run,
                    revision=current_run.revision + 1,
                    updated_at=self.clock.now(),
                )
                current_run = await self._commit(
                    previous=current_run,
                    current=next_run,
                    lease_token=lease_token,
                    events=(
                        (
                            "tool.prepared",
                            EventActor.TOOL,
                            {
                                "execution_id": execution.execution_id,
                                "tool_name": prepared.name,
                                "prepared_digest": prepared.prepared_digest,
                            },
                            current_turn.turn_id,
                        ),
                    ),
                    tool_executions=(execution,),
                )

            fingerprint = fingerprint_action(
                prepared,
                workspace_revision=current_run.workspace_current_revision,
                context_digest=context_digest,
            )
            projection_events = self._all_events(current_run.run_id)
            repetition = assess_repetition(
                projection_events,
                fingerprint,
                max_consecutive=self.settings.max_consecutive_identical_actions,
                override_after_sequence=(
                    self._last_sequence(current_run.run_id) if allow_repeat else None
                ),
            )
            if repetition.repeated and not allow_repeat:
                decision = self.stop_policy.repeated_action(
                    action_key=repetition.action_key,
                    prior_count=repetition.prior_consecutive_count,
                )
                cursor = _tool_cursor(
                    current_turn,
                    model_call,
                    proposals,
                    index,
                    context_digest,
                    execution,
                )
                return await self._pause(
                    current_run,
                    lease_token,
                    decision,
                    cursor=cursor,
                )

            preflight = await self.tool_executor.preflight(
                prepared,
                permission_decision=permission_decision if index == start_index else None,
                execution_metadata={
                    "run_id": current_run.run_id,
                    "turn_id": current_turn.turn_id,
                    "tool_execution_id": execution.execution_id,
                },
            )
            permission_decision = None
            if preflight.error is not None:
                if preflight.error.status is ToolResultStatus.PENDING_PERMISSION:
                    waiting = replace(
                        execution,
                        status=ToolExecutionStatus.WAITING_PERMISSION,
                        permission_decision=DomainPermissionDecision.PENDING,
                    )
                    waiting_turn = replace(
                        current_turn,
                        status=TurnStatus.WAITING,
                        phase=TurnPhase.WAIT_PERMISSION,
                        revision=current_turn.revision + 1,
                    )
                    decision = self.stop_policy.permission_required(
                        tool_name=prepared.name,
                        prepared_digest=prepared.prepared_digest,
                    )
                    cursor = _tool_cursor(
                        waiting_turn,
                        model_call,
                        proposals,
                        index,
                        context_digest,
                        waiting,
                    )
                    return await self._pause(
                        current_run,
                        lease_token,
                        decision,
                        cursor=cursor,
                        active_turn=waiting_turn,
                        tool_executions=(waiting,),
                    )
                current_run, result = await self._finish_preflight_error(
                    current_run,
                    current_turn,
                    execution,
                    preflight.error,
                    fingerprint.action_key,
                    lease_token,
                )
                results.append(result)
                continue
            assert preflight.grant is not None
            current_run, result = await self._execute_grant(
                current_run,
                current_turn,
                execution,
                preflight.grant,
                fingerprint.action_key,
                lease_token,
            )
            results.append(result)
            if result.side_effect_state in {
                SideEffectState.PARTIAL,
                SideEffectState.UNCERTAIN,
            }:
                failed_turn = replace(
                    current_turn,
                    status=TurnStatus.FAILED,
                    phase=TurnPhase.DECIDE,
                    ended_at=self.clock.now(),
                    revision=current_turn.revision + 1,
                )
                decision = StopDecision(
                    action=StopAction.PAUSE,
                    reason="uncertain_side_effect",
                    scope=StopScope.RUN,
                    resumable=True,
                    resume_requirements=("review_workspace_and_checkpoint",),
                    evidence={"execution_id": execution.execution_id},
                )
                return await self._pause(
                    current_run,
                    lease_token,
                    decision,
                    cursor={"kind": "new_turn"},
                    active_turn=failed_turn,
                    clear_active_turn=True,
                )
            if result.status is ToolResultStatus.CANCELLED:
                return await self._cancel_after_tool(
                    current_run,
                    current_turn,
                    execution.execution_id,
                    lease_token,
                )

        completed_turn = replace(
            current_turn,
            status=TurnStatus.COMPLETED,
            phase=TurnPhase.DECIDE,
            ended_at=self.clock.now(),
            revision=current_turn.revision + 1,
        )
        decision = self.stop_policy.after_turn(
            context=_decision_context(None, tuple(results), current_run)
        )
        next_run = replace(
            current_run,
            active_turn_id=None,
            stop_decision=decision,
            revision=current_run.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=current_run,
            current=next_run,
            lease_token=lease_token,
            events=(
                (
                    "stop.decided",
                    EventActor.RUNTIME,
                    {"decision": decision.to_dict()},
                    current_turn.turn_id,
                ),
            ),
            turns=(completed_turn,),
        )

    async def _try_parallel_read_batch(
        self,
        run: Run,
        turn: Turn,
        model_call: ModelCallRecord,
        proposals: Sequence[ToolProposal],
        context_digest: str,
        lease_token: str,
    ) -> Run | None:
        ordered = tuple(sorted(proposals, key=lambda proposal: proposal.ordinal))
        tool_limit = run.budget.max_tool_executions
        if (
            tool_limit is not None
            and run.usage.tool_executions + len(ordered) > tool_limit
        ):
            return None
        if len({proposal.ordinal for proposal in ordered}) != len(ordered):
            return None
        prepared_items: list[
            tuple[ToolProposal, Any, ToolExecutionRecord, str]
        ] = []
        action_keys: set[str] = set()
        projection_events = self._all_events(run.run_id)
        for proposal in ordered:
            preparation = self.tool_executor.prepare(proposal)
            prepared = preparation.prepared
            if (
                preparation.error is not None
                or prepared is None
                or prepared.effect is not EffectClass.READ
                or not prepared.parallel_safe
            ):
                return None
            fingerprint = fingerprint_action(
                prepared,
                workspace_revision=run.workspace_current_revision,
                context_digest=context_digest,
            )
            if fingerprint.action_key in action_keys:
                return None
            action_keys.add(fingerprint.action_key)
            repetition = assess_repetition(
                projection_events,
                fingerprint,
                max_consecutive=self.settings.max_consecutive_identical_actions,
            )
            if repetition.repeated:
                return None
            execution = ToolExecutionRecord(
                execution_id=self.ids.new("tool_execution"),
                turn_id=turn.turn_id,
                model_call_id=model_call.model_call_id,
                tool_call_id=proposal.tool_call_id,
                ordinal=proposal.ordinal,
                attempt_no=1,
                tool_name=prepared.name,
                tool_version=prepared.version,
                status=ToolExecutionStatus.PREPARED,
                normalized_arguments=prepared.normalized_arguments,
                effect_class=DomainEffectClass.READ,
                prepared_digest=prepared.prepared_digest,
                side_effect_state=DomainSideEffectState.NOT_STARTED,
            )
            prepared_items.append(
                (proposal, prepared, execution, fingerprint.action_key)
            )

        preflights = await asyncio.gather(
            *(
                self.tool_executor.preflight(
                    prepared,
                    execution_metadata={
                        "run_id": run.run_id,
                        "turn_id": turn.turn_id,
                        "tool_execution_id": execution.execution_id,
                    },
                )
                for _proposal, prepared, execution, _action_key in prepared_items
            )
        )
        if any(preflight.grant is None or preflight.error is not None for preflight in preflights):
            return None

        prepared_run = replace(
            run,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        prepared_run = await self._commit(
            previous=run,
            current=prepared_run,
            lease_token=lease_token,
            events=tuple(
                (
                    "tool.prepared",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": prepared.name,
                        "prepared_digest": prepared.prepared_digest,
                        "parallel_batch": True,
                    },
                    turn.turn_id,
                )
                for _proposal, prepared, execution, _action_key in prepared_items
            ),
            tool_executions=tuple(
                execution
                for _proposal, _prepared, execution, _action_key in prepared_items
            ),
        )
        ready_records = tuple(
            replace(
                execution,
                status=ToolExecutionStatus.READY,
                permission_decision=DomainPermissionDecision.NOT_REQUIRED,
            )
            for _proposal, _prepared, execution, _action_key in prepared_items
        )
        ready_run = replace(
            prepared_run,
            revision=prepared_run.revision + 1,
            updated_at=self.clock.now(),
        )
        ready_run = await self._commit(
            previous=prepared_run,
            current=ready_run,
            lease_token=lease_token,
            events=tuple(
                (
                    "permission.decided",
                    EventActor.RUNTIME,
                    {
                        "execution_id": execution.execution_id,
                        "decision": "allow",
                        "parallel_batch": True,
                    },
                    turn.turn_id,
                )
                for execution in ready_records
            ),
            tool_executions=ready_records,
        )
        started_at = self.clock.now()
        running_records = tuple(
            replace(
                execution,
                status=ToolExecutionStatus.RUNNING,
                started_at=started_at,
            )
            for execution in ready_records
        )
        running_run = replace(
            ready_run,
            revision=ready_run.revision + 1,
            updated_at=started_at,
        )
        running_run = await self._commit(
            previous=ready_run,
            current=running_run,
            lease_token=lease_token,
            events=tuple(
                (
                    "tool.started",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": execution.tool_name,
                        "parallel_batch": True,
                    },
                    turn.turn_id,
                )
                for execution in running_records
            ),
            tool_executions=running_records,
        )
        grants = tuple(preflight.grant for preflight in preflights)
        assert all(grant is not None for grant in grants)
        results = await asyncio.gather(
            *(
                self._execute_parallel_grant(grant)
                for grant in grants
                if grant is not None
            )
        )
        ended_at = self.clock.now()
        terminal_records = tuple(
            _terminal_execution(execution, result, ended_at)
            for execution, result in zip(
                running_records,
                results,
                strict=True,
            )
        )
        completed_run = replace(
            running_run,
            usage=replace(
                running_run.usage,
                tool_executions=(
                    running_run.usage.tool_executions + len(results)
                ),
            ),
            revision=running_run.revision + 1,
            updated_at=ended_at,
        )
        completed_run = await self._commit(
            previous=running_run,
            current=completed_run,
            lease_token=lease_token,
            events=tuple(
                (
                    "tool.completed",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": execution.tool_name,
                        "status": result.status.value,
                        "action_key": action_key,
                        "result_digest": digest_result(result.to_model_payload()),
                        "effect": EffectClass.READ.value,
                        "changed_paths": [],
                        "parallel_batch": True,
                        "message": Message(
                            role=MessageRole.TOOL,
                            content=result.to_model_text(),
                            tool_call_id=proposal.tool_call_id,
                            name=execution.tool_name,
                        ).to_dict(),
                    },
                    turn.turn_id,
                )
                for (
                    proposal,
                    _prepared,
                    execution,
                    action_key,
                ), result in zip(prepared_items, results, strict=True)
            ),
            tool_executions=terminal_records,
        )
        cancelled_execution_ids = [
            execution.execution_id
            for execution, result in zip(
                terminal_records,
                results,
                strict=True,
            )
            if result.status is ToolResultStatus.CANCELLED
        ]
        if cancelled_execution_ids:
            return await self._cancel_after_tool(
                completed_run,
                turn,
                cancelled_execution_ids[0],
                lease_token,
            )
        completed_turn = replace(
            turn,
            status=TurnStatus.COMPLETED,
            phase=TurnPhase.DECIDE,
            ended_at=self.clock.now(),
            revision=turn.revision + 1,
        )
        decision = self.stop_policy.after_turn(
            context=_decision_context(None, tuple(results), completed_run)
        )
        final_run = replace(
            completed_run,
            active_turn_id=None,
            stop_decision=decision,
            revision=completed_run.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=completed_run,
            current=final_run,
            lease_token=lease_token,
            events=(
                (
                    "stop.decided",
                    EventActor.RUNTIME,
                    {"decision": decision.to_dict()},
                    turn.turn_id,
                ),
            ),
            turns=(completed_turn,),
            )

    async def _cancel_after_tool(
        self,
        run: Run,
        turn: Turn,
        execution_id: str,
        lease_token: str,
    ) -> Run:
        decision = StopDecision(
            action=StopAction.CANCEL,
            reason="user_cancelled",
            scope=StopScope.RUN,
            evidence={"execution_id": execution_id},
        )
        cancelled_turn = replace(
            turn,
            status=TurnStatus.CANCELLED,
            phase=TurnPhase.DECIDE,
            ended_at=self.clock.now(),
            revision=turn.revision + 1,
        )
        cancelled_run = replace(
            run,
            status=RunStatus.CANCELLED,
            active_turn_id=None,
            stop_decision=decision,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=run,
            current=cancelled_run,
            lease_token=lease_token,
            events=(
                (
                    "stop.decided",
                    EventActor.RUNTIME,
                    {"decision": decision.to_dict()},
                    turn.turn_id,
                ),
                (
                    "run.cancelled",
                    EventActor.RUNTIME,
                    {
                        "reason": decision.reason,
                        "execution_id": execution_id,
                    },
                    turn.turn_id,
                ),
            ),
            turns=(cancelled_turn,),
        )

    async def _execute_parallel_grant(
        self,
        grant: ExecutionGrant,
    ) -> ToolResult:
        try:
            return await self.tool_executor.execute_preflighted(
                grant,
                services=self.settings.tool_services,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return ToolResult.error(
                ErrorKind.INTERNAL_ERROR,
                f"{type(error).__name__}: {str(error)[:1_000]}",
            )

    async def _execute_grant(
        self,
        run: Run,
        turn: Turn,
        execution: ToolExecutionRecord,
        grant: ExecutionGrant,
        action_key: str,
        lease_token: str,
    ) -> tuple[Run, ToolResult]:
        checkpoint, artifact = self._domain_checkpoint(run, turn, execution, grant)
        ready = replace(
            execution,
            status=ToolExecutionStatus.READY,
            permission_decision=(
                DomainPermissionDecision.GRANTED
                if grant.prepared.effect is not EffectClass.READ
                else DomainPermissionDecision.NOT_REQUIRED
            ),
            checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        )
        ready_run = replace(
            run,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        ready_run = await self._commit(
            previous=run,
            current=ready_run,
            lease_token=lease_token,
            events=(
                (
                    "checkpoint.created" if checkpoint else "permission.decided",
                    EventActor.RUNTIME,
                    {
                        "execution_id": execution.execution_id,
                        "checkpoint_id": checkpoint.checkpoint_id if checkpoint else None,
                        "decision": grant.permission_decision.outcome.value,
                    },
                    turn.turn_id,
                ),
            ),
            tool_executions=(ready,),
            checkpoints=(checkpoint,) if checkpoint else (),
            artifacts=(artifact,) if artifact else (),
        )
        running = replace(
            ready,
            status=ToolExecutionStatus.RUNNING,
            started_at=self.clock.now(),
        )
        running_run = replace(
            ready_run,
            revision=ready_run.revision + 1,
            updated_at=self.clock.now(),
        )
        running_run = await self._commit(
            previous=ready_run,
            current=running_run,
            lease_token=lease_token,
            events=(
                (
                    "tool.started",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": execution.tool_name,
                    },
                    turn.turn_id,
                ),
            ),
            tool_executions=(running,),
        )
        result = await self.tool_executor.execute_preflighted(
            grant,
            services=self.settings.tool_services,
        )
        index_event: tuple[str, EventActor, Mapping[str, Any], str | None] | None = None
        if grant.prepared.effect is EffectClass.WRITE and result.ok:
            index_event = await self._refresh_workspace_index(turn.turn_id)
        terminal = _terminal_execution(running, result, self.clock.now())
        tool_message = Message(
            role=MessageRole.TOOL,
            content=result.to_model_text(),
            tool_call_id=execution.tool_call_id,
            name=execution.tool_name,
        )
        after_revision = result.workspace_revision or running_run.workspace_current_revision
        completed_run = replace(
            running_run,
            workspace_current_revision=after_revision,
            usage=replace(
                running_run.usage,
                tool_executions=running_run.usage.tool_executions + 1,
            ),
            revision=running_run.revision + 1,
            updated_at=self.clock.now(),
        )
        completed_run = await self._commit(
            previous=running_run,
            current=completed_run,
            lease_token=lease_token,
            events=(
                *(
                    (index_event,)
                    if index_event is not None
                    else ()
                ),
                (
                    "tool.completed",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": execution.tool_name,
                        "status": result.status.value,
                        "action_key": action_key,
                        "result_digest": digest_result(result.to_model_payload()),
                        "effect": grant.prepared.effect.value,
                        "changed_paths": (
                            [target.relative_path for target in grant.prepared.resolved_targets]
                            if grant.prepared.effect is EffectClass.WRITE and result.ok
                            else []
                        ),
                        "message": tool_message.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
            tool_executions=(terminal,),
        )
        return completed_run, result

    async def _refresh_workspace_index(
        self,
        turn_id: str,
    ) -> tuple[str, EventActor, Mapping[str, Any], str | None] | None:
        indexer = self.settings.tool_services.get("workspace_indexer")
        if indexer is None:
            return None
        try:
            report = indexer.refresh()
            payload = (
                asdict(report)
                if hasattr(report, "__dataclass_fields__")
                else {"result": str(report)[:2_000]}
            )
            return ("index.refreshed", EventActor.RUNTIME, payload, turn_id)
        except Exception as error:
            return (
                "index.refresh_failed",
                EventActor.RUNTIME,
                {
                    "error_type": type(error).__name__,
                    "message": str(error)[:1_000],
                },
                turn_id,
            )

    async def _record_prepare_error(
        self,
        run: Run,
        turn: Turn,
        model_call: ModelCallRecord,
        proposal: ToolProposal,
        result: ToolResult,
        lease_token: str,
    ) -> tuple[Run, ToolResult]:
        now = self.clock.now()
        execution = ToolExecutionRecord(
            execution_id=self.ids.new("tool_execution"),
            turn_id=turn.turn_id,
            model_call_id=model_call.model_call_id,
            tool_call_id=proposal.tool_call_id,
            ordinal=proposal.ordinal,
            attempt_no=1,
            tool_name=proposal.name,
            tool_version=(
                self.tools.get(proposal.name).spec.version
                if self.tools.get(proposal.name)
                else "unknown"
            ),
            status=ToolExecutionStatus.FAILED,
            error=_tool_error_info(result),
            side_effect_state=DomainSideEffectState.NOT_STARTED,
            started_at=now,
            ended_at=now,
        )
        message = Message(
            role=MessageRole.TOOL,
            content=result.to_model_text(),
            tool_call_id=proposal.tool_call_id,
            name=proposal.name,
        )
        next_run = replace(
            run,
            usage=replace(
                run.usage,
                tool_executions=run.usage.tool_executions + 1,
            ),
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        next_run = await self._commit(
            previous=run,
            current=next_run,
            lease_token=lease_token,
            events=(
                (
                    "tool.completed",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "tool_name": proposal.name,
                        "status": result.status.value,
                        "result_digest": digest_result(result.to_model_payload()),
                        "message": message.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
            tool_executions=(execution,),
        )
        return next_run, result

    async def _finish_preflight_error(
        self,
        run: Run,
        turn: Turn,
        execution: ToolExecutionRecord,
        result: ToolResult,
        action_key: str,
        lease_token: str,
    ) -> tuple[Run, ToolResult]:
        status = (
            ToolExecutionStatus.DENIED
            if result.status is ToolResultStatus.DENIED
            else ToolExecutionStatus.FAILED
        )
        terminal = replace(
            execution,
            status=status,
            permission_decision=(
                DomainPermissionDecision.DENIED
                if status is ToolExecutionStatus.DENIED
                else execution.permission_decision
            ),
            error=None if status is ToolExecutionStatus.DENIED else _tool_error_info(result),
            ended_at=self.clock.now(),
        )
        message = Message(
            role=MessageRole.TOOL,
            content=result.to_model_text(),
            tool_call_id=execution.tool_call_id,
            name=execution.tool_name,
        )
        next_run = replace(
            run,
            usage=replace(run.usage, tool_executions=run.usage.tool_executions + 1),
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        next_run = await self._commit(
            previous=run,
            current=next_run,
            lease_token=lease_token,
            events=(
                (
                    "tool.completed",
                    EventActor.TOOL,
                    {
                        "execution_id": execution.execution_id,
                        "status": result.status.value,
                        "action_key": action_key,
                        "result_digest": digest_result(result.to_model_payload()),
                        "message": message.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
            tool_executions=(terminal,),
        )
        return next_run, result

    async def _finish_model_error(
        self,
        run: Run,
        turn: Turn,
        call: ModelCallRecord,
        error: ModelGatewayError,
        lease_token: str,
    ) -> Run:
        decision = self.stop_policy.provider_error(error)
        call_status = (
            ModelCallStatus.CANCELLED
            if error.kind is ModelErrorKind.CANCELLED
            else ModelCallStatus.FAILED
        )
        failed_call = replace(
            call,
            status=call_status,
            error=(
                None
                if call_status is ModelCallStatus.CANCELLED
                else _model_error_info(error)
            ),
            ended_at=self.clock.now(),
        )
        accounted = replace(
            run,
            usage=replace(
                run.usage,
                model_calls=run.usage.model_calls + 1,
            ),
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        accounted = await self._commit(
            previous=run,
            current=accounted,
            lease_token=lease_token,
            events=(
                (
                    (
                        "model_call.cancelled"
                        if call_status is ModelCallStatus.CANCELLED
                        else "model_call.failed"
                    ),
                    EventActor.MODEL,
                    {
                        "model_call_id": call.model_call_id,
                        "attempt_no": call.attempt_no,
                        "error": error.to_dict(),
                        "will_retry": False,
                    },
                    turn.turn_id,
                ),
            ),
            model_calls=(failed_call,),
        )
        failed_turn = replace(
            turn,
            status=(
                TurnStatus.CANCELLED
                if decision.action is StopAction.CANCEL
                else TurnStatus.FAILED
            ),
            ended_at=self.clock.now(),
            revision=turn.revision + 1,
        )
        if decision.action is StopAction.PAUSE:
            return await self._pause(
                accounted,
                lease_token,
                decision,
                cursor={"kind": "new_turn"},
                active_turn=failed_turn,
                clear_active_turn=True,
            )
        terminal_status = (
            RunStatus.CANCELLED
            if decision.action is StopAction.CANCEL
            else RunStatus.FAILED
        )
        terminal = replace(
            accounted,
            status=terminal_status,
            active_turn_id=None,
            stop_decision=decision,
            revision=accounted.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=accounted,
            current=terminal,
            lease_token=lease_token,
            events=(
                (
                    "run.cancelled" if terminal_status is RunStatus.CANCELLED else "run.failed",
                    EventActor.RUNTIME,
                    {"reason": decision.reason},
                    turn.turn_id,
                ),
            ),
            turns=(failed_turn,),
        )

    async def _complete_with_answer(
        self,
        run: Run,
        turn: Turn,
        result: ModelResult,
        lease_token: str,
    ) -> Run:
        changed_paths = self._changed_paths(run.run_id)
        if changed_paths:
            run, verification = await self._verify_changes(
                run,
                turn,
                changed_paths,
                lease_token,
            )
            if verification is None or verification.status is VerificationStatus.INCONCLUSIVE:
                completed_turn = replace(
                    turn,
                    status=TurnStatus.COMPLETED,
                    phase=TurnPhase.DECIDE,
                    ended_at=self.clock.now(),
                    revision=turn.revision + 1,
                )
                decision = StopDecision(
                    action=StopAction.PAUSE,
                    reason="verification_inconclusive",
                    scope=StopScope.RUN,
                    resumable=True,
                    resume_requirements=("configure_or_rerun_verification",),
                    evidence={
                        "changed_paths": list(changed_paths),
                        "verification_id": (
                            verification.verification_id if verification else None
                        ),
                    },
                )
                return await self._pause(
                    run,
                    lease_token,
                    decision,
                    cursor={"kind": "new_turn"},
                    active_turn=completed_turn,
                    clear_active_turn=True,
                )
            if verification.status is VerificationStatus.FAILED:
                completed_turn = replace(
                    turn,
                    status=TurnStatus.COMPLETED,
                    phase=TurnPhase.DECIDE,
                    ended_at=self.clock.now(),
                    revision=turn.revision + 1,
                )
                decision = StopDecision(
                    action=StopAction.CONTINUE,
                    reason="verification_failed",
                    scope=StopScope.RUN,
                    evidence={
                        "verification_id": verification.verification_id,
                        "retry_recommendation": verification.retry_recommendation,
                    },
                )
                continuing = replace(
                    run,
                    active_turn_id=None,
                    stop_decision=decision,
                    revision=run.revision + 1,
                    updated_at=self.clock.now(),
                )
                return await self._commit(
                    previous=run,
                    current=continuing,
                    lease_token=lease_token,
                    events=(
                        (
                            "stop.decided",
                            EventActor.RUNTIME,
                            {"decision": decision.to_dict()},
                            turn.turn_id,
                        ),
                    ),
                    turns=(completed_turn,),
                )
            if verification.status is VerificationStatus.CANCELLED:
                decision = StopDecision(
                    action=StopAction.CANCEL,
                    reason="verification_cancelled",
                    scope=StopScope.RUN,
                    evidence={"verification_id": verification.verification_id},
                )
                cancelled_turn = replace(
                    turn,
                    status=TurnStatus.CANCELLED,
                    ended_at=self.clock.now(),
                    revision=turn.revision + 1,
                )
                cancelled = replace(
                    run,
                    status=RunStatus.CANCELLED,
                    active_turn_id=None,
                    stop_decision=decision,
                    revision=run.revision + 1,
                    updated_at=self.clock.now(),
                )
                return await self._commit(
                    previous=run,
                    current=cancelled,
                    lease_token=lease_token,
                    events=(
                        (
                            "run.cancelled",
                            EventActor.RUNTIME,
                            {"reason": decision.reason},
                            turn.turn_id,
                        ),
                    ),
                    turns=(cancelled_turn,),
                )
            if self.reviewer is not None:
                try:
                    run, review = await self._review_changes(
                        run,
                        turn,
                        result,
                        changed_paths,
                        verification,
                        lease_token,
                    )
                except _ReviewerInvocationFailure as failure:
                    run = failure.run
                    completed_turn = replace(
                        turn,
                        status=TurnStatus.COMPLETED,
                        phase=TurnPhase.DECIDE,
                        ended_at=self.clock.now(),
                        revision=turn.revision + 1,
                    )
                    decision = StopDecision(
                        action=StopAction.PAUSE,
                        reason="reviewer_unavailable",
                        scope=StopScope.RUN,
                        resumable=True,
                        resume_requirements=("reviewer_available_or_disabled",),
                        evidence={"error": str(failure.error)[:1_000]},
                    )
                    return await self._pause(
                        run,
                        lease_token,
                        decision,
                        cursor={"kind": "new_turn"},
                        active_turn=completed_turn,
                        clear_active_turn=True,
                    )
                if not review.approved(
                    self.settings.reviewer_blocking_severities
                ):
                    completed_turn = replace(
                        turn,
                        status=TurnStatus.COMPLETED,
                        phase=TurnPhase.DECIDE,
                        ended_at=self.clock.now(),
                        revision=turn.revision + 1,
                    )
                    decision = StopDecision(
                        action=StopAction.CONTINUE,
                        reason="reviewer_changes_requested",
                        scope=StopScope.RUN,
                        evidence={
                            "findings": [
                                finding.to_dict() for finding in review.findings
                            ]
                        },
                    )
                    continuing = replace(
                        run,
                        active_turn_id=None,
                        stop_decision=decision,
                        revision=run.revision + 1,
                        updated_at=self.clock.now(),
                    )
                    return await self._commit(
                        previous=run,
                        current=continuing,
                        lease_token=lease_token,
                        events=(
                            (
                                "stop.decided",
                                EventActor.RUNTIME,
                                {"decision": decision.to_dict()},
                                turn.turn_id,
                            ),
                        ),
                        turns=(completed_turn,),
                    )
        decision = self.stop_policy.after_turn(
            _decision_context(result, (), run)
        )
        if decision.action is not StopAction.COMPLETE:
            raise RuntimeCommandError("final text did not produce a COMPLETE decision")
        completed_turn = replace(
            turn,
            status=TurnStatus.COMPLETED,
            phase=TurnPhase.DECIDE,
            ended_at=self.clock.now(),
            revision=turn.revision + 1,
        )
        completed = replace(
            run,
            status=RunStatus.COMPLETED,
            active_turn_id=None,
            final_response=(result.text or "").strip(),
            stop_decision=decision,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=run,
            current=completed,
            lease_token=lease_token,
            events=(
                (
                    "stop.decided",
                    EventActor.RUNTIME,
                    {"decision": decision.to_dict()},
                    turn.turn_id,
                ),
                (
                    "run.completed",
                    EventActor.RUNTIME,
                    {"final_response": completed.final_response},
                    turn.turn_id,
                ),
            ),
            turns=(completed_turn,),
        )

    async def _verify_changes(
        self,
        run: Run,
        turn: Turn,
        changed_paths: tuple[str, ...],
        lease_token: str,
    ) -> tuple[Run, VerificationResult | None]:
        if self.verifier is None:
            return run, None
        started = replace(
            run,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        started = await self._commit(
            previous=run,
            current=started,
            lease_token=lease_token,
            events=(
                (
                    "verification.started",
                    EventActor.VERIFIER,
                    {"changed_paths": list(changed_paths)},
                    turn.turn_id,
                ),
            ),
        )
        try:
            plan = (
                self.verification_plan_factory(
                    run,
                    changed_paths,
                    self._all_events(run.run_id),
                )
                if self.verification_plan_factory is not None
                else VerificationPlan(
                    allowed_changed_paths=changed_paths,
                    require_diff=True,
                )
            )
            if not isinstance(plan, VerificationPlan):
                raise TypeError(
                    "verification_plan_factory must return VerificationPlan"
                )
            result = await self.verifier.verify(
                VerificationRequest(
                    run_id=run.run_id,
                    plan=plan,
                    changed_paths=changed_paths,
                    diff_text=self._latest_diff(run.run_id),
                    diagnostics=self._latest_diagnostics(run.run_id),
                )
            )
        except asyncio.CancelledError:
            result = VerificationResult(
                verification_id=self.ids.new("verification"),
                run_id=run.run_id,
                status=VerificationStatus.CANCELLED,
                checks=(
                    VerificationCheck(
                        name="verification_cancelled",
                        status=VerificationStatus.CANCELLED,
                        summary="verification was cancelled",
                    ),
                ),
                changed_paths=changed_paths,
            )
        except Exception as error:
            summary = f"{type(error).__name__}: {str(error)[:1_000]}"
            result = VerificationResult(
                verification_id=self.ids.new("verification"),
                run_id=run.run_id,
                status=VerificationStatus.INCONCLUSIVE,
                checks=(
                    VerificationCheck(
                        name="verification_runtime",
                        status=VerificationStatus.INCONCLUSIVE,
                        summary=summary,
                    ),
                ),
                diagnostics=(
                    {
                        "kind": "verification_runtime_error",
                        "message": summary,
                    },
                ),
                changed_paths=changed_paths,
                retry_recommendation=(
                    "repair verifier configuration and rerun verification"
                ),
            )
        message = Message(
            role=MessageRole.USER,
            content=json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_label="verification_result",
            metadata={"authority": "runtime_verifier"},
        )
        completed = replace(
            started,
            revision=started.revision + 1,
            updated_at=self.clock.now(),
        )
        completed = await self._commit(
            previous=started,
            current=completed,
            lease_token=lease_token,
            events=(
                (
                    "verification.completed",
                    EventActor.VERIFIER,
                    {
                        "verification_id": result.verification_id,
                        "status": result.status.value,
                        "message": message.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
            verification_results=(result,),
        )
        return completed, result

    async def _review_changes(
        self,
        run: Run,
        turn: Turn,
        model_result: ModelResult,
        changed_paths: tuple[str, ...],
        verification: VerificationResult,
        lease_token: str,
    ) -> tuple[Run, ReviewResult]:
        assert self.reviewer is not None
        started = replace(
            run,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        started = await self._commit(
            previous=run,
            current=started,
            lease_token=lease_token,
            events=(
                (
                    "reviewer.started",
                    EventActor.RUNTIME,
                    {"changed_paths": list(changed_paths)},
                    turn.turn_id,
                ),
            ),
        )
        try:
            review = await self.reviewer.review(
                ReviewRequest(
                    run_id=run.run_id,
                    objective=run.objective,
                    proposed_answer=model_result.text or "",
                    changed_paths=changed_paths,
                    diff_text=self._latest_diff(run.run_id) or "",
                    verification=verification,
                )
            )
        except ReviewerError as error:
            failed = replace(
                started,
                revision=started.revision + 1,
                updated_at=self.clock.now(),
            )
            failed = await self._commit(
                previous=started,
                current=failed,
                lease_token=lease_token,
                events=(
                    (
                        "reviewer.failed",
                        EventActor.RUNTIME,
                        {"error": str(error)[:1_000]},
                        turn.turn_id,
                    ),
                ),
            )
            raise _ReviewerInvocationFailure(failed, error) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            wrapped = ReviewerError(
                f"reviewer failed: {type(error).__name__}: {str(error)[:1_000]}"
            )
            failed = replace(
                started,
                revision=started.revision + 1,
                updated_at=self.clock.now(),
            )
            failed = await self._commit(
                previous=started,
                current=failed,
                lease_token=lease_token,
                events=(
                    (
                        "reviewer.failed",
                        EventActor.RUNTIME,
                        {"error": str(wrapped)[:1_000]},
                        turn.turn_id,
                    ),
                ),
            )
            raise _ReviewerInvocationFailure(failed, wrapped) from error
        message = Message(
            role=MessageRole.USER,
            content=json.dumps(
                review.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_label="reviewer_result",
            metadata={"authority": "runtime_reviewer"},
        )
        completed = replace(
            started,
            revision=started.revision + 1,
            updated_at=self.clock.now(),
        )
        completed = await self._commit(
            previous=started,
            current=completed,
            lease_token=lease_token,
            events=(
                (
                    "reviewer.completed",
                    EventActor.RUNTIME,
                    {
                        "approved": review.approved(
                            self.settings.reviewer_blocking_severities
                        ),
                        "message": message.to_dict(),
                    },
                    turn.turn_id,
                ),
            ),
        )
        return completed, review

    async def _resume_tool_batch(
        self,
        run: Run,
        lease_token: str,
        cursor: Mapping[str, Any],
        command: ResumeRun,
    ) -> Run:
        turn = self.state.load_turn(str(cursor["turn_id"]))
        model_call = self.state.load_model_call(str(cursor["model_call_id"]))
        execution = self.state.load_tool_execution(str(cursor["execution_id"]))
        proposals = tuple(
            ToolProposal.from_dict(item) for item in cursor.get("proposals", [])
        )
        index = int(cursor["next_index"])
        prepared_digest = str(cursor["prepared_digest"])
        raw_decision = command.permission_decisions.get(prepared_digest)
        decision = None
        if raw_decision:
            try:
                outcome = PermissionOutcome(raw_decision)
            except ValueError as error:
                raise RuntimeCommandError(
                    f"invalid permission outcome: {raw_decision}"
                ) from error
            decision = PermissionDecision(
                outcome=outcome,
                prepared_digest=prepared_digest,
                scope=(
                    PermissionScope.DENY
                    if outcome is PermissionOutcome.DENY
                    else PermissionScope.ONCE
                ),
                reason="resume decision",
            )
        return await self._execute_tool_batch(
            run,
            turn,
            model_call,
            proposals,
            str(cursor["context_digest"]),
            lease_token,
            start_index=index,
            existing_execution=execution,
            permission_decision=decision,
            allow_repeat=command.allow_repeated_action_once,
        )

    async def _pause(
        self,
        run: Run,
        lease_token: str,
        decision: StopDecision,
        *,
        cursor: Mapping[str, Any],
        active_turn: Turn | None = None,
        clear_active_turn: bool = False,
        turns: tuple[Turn, ...] = (),
        model_calls: tuple[ModelCallRecord, ...] = (),
        tool_executions: tuple[ToolExecutionRecord, ...] = (),
    ) -> Run:
        if decision.action is not StopAction.PAUSE:
            raise ValueError("_pause requires a PAUSE decision")
        pause_token = self.ids.new("pause")
        paused = replace(
            run,
            status=RunStatus.PAUSED,
            active_turn_id=None if clear_active_turn else run.active_turn_id,
            stop_decision=decision,
            pause_token=pause_token,
            resume_cursor=_encode_cursor(cursor),
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        turn_records = turns + ((active_turn,) if active_turn is not None else ())
        return await self._commit(
            previous=run,
            current=paused,
            lease_token=lease_token,
            events=(
                (
                    "stop.decided",
                    EventActor.RUNTIME,
                    {"decision": decision.to_dict()},
                    run.active_turn_id,
                ),
                (
                    "run.paused",
                    EventActor.RUNTIME,
                    {"reason": decision.reason, "pause_token": pause_token},
                    run.active_turn_id,
                ),
            ),
            turns=turn_records,
            model_calls=model_calls,
            tool_executions=tool_executions,
        )

    async def _update_run(
        self,
        run: Run,
        lease_token: str,
        *,
        event_type: str,
        status: RunStatus,
    ) -> Run:
        current = replace(
            run,
            status=status,
            revision=run.revision + 1,
            updated_at=self.clock.now(),
        )
        return await self._commit(
            previous=run,
            current=current,
            lease_token=lease_token,
            events=((event_type, EventActor.RUNTIME, {}, run.active_turn_id),),
        )

    async def _commit(
        self,
        *,
        previous: Run,
        current: Run,
        lease_token: str,
        events: Sequence[
            tuple[str, EventActor, Mapping[str, Any], str | None]
        ],
        turns: tuple[Turn, ...] = (),
        model_calls: tuple[ModelCallRecord, ...] = (),
        tool_executions: tuple[ToolExecutionRecord, ...] = (),
        checkpoints: tuple[Checkpoint, ...] = (),
        artifacts: tuple[Artifact, ...] = (),
        verification_results: tuple[VerificationResult, ...] = (),
    ) -> Run:
        sequence = self.state.next_event_sequence(current.run_id)
        envelopes = tuple(
            Event.create(
                session_id=current.session_id,
                run_id=current.run_id,
                turn_id=turn_id,
                sequence=sequence + offset,
                event_type=event_type,
                actor=actor,
                payload=payload,
            )
            for offset, (event_type, actor, payload, turn_id) in enumerate(events)
        )
        self.state.commit(
            StateMutation(
                run=current,
                expected_run_revision=previous.revision,
                lease_token=lease_token,
                turns=turns,
                model_calls=model_calls,
                tool_executions=tool_executions,
                checkpoints=checkpoints,
                artifacts=artifacts,
                verification_results=verification_results,
                events=envelopes,
            )
        )
        await self.events.publish(envelopes)
        return current

    def _domain_checkpoint(
        self,
        run: Run,
        turn: Turn,
        execution: ToolExecutionRecord,
        grant: ExecutionGrant,
    ) -> tuple[Checkpoint | None, Artifact | None]:
        manifest = grant.checkpoint
        if manifest is None:
            return None, None
        if self.artifact_store is None:
            raise RuntimeCommandError(
                "write grants require a Runtime artifact store for checkpoint evidence"
            )
        payload = json.dumps(
            asdict(manifest),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        artifact = self.artifact_store.put_text(
            payload,
            media_type="application/vnd.rivet.checkpoint+json",
            redaction_status=RedactionStatus.CLEAN,
        )
        checkpoint = Checkpoint(
            checkpoint_id=manifest.checkpoint_id,
            run_id=run.run_id,
            turn_id=turn.turn_id,
            created_before_execution_id=execution.execution_id,
            status=CheckpointStatus.READY,
            scope=tuple(entry.path for entry in manifest.affected_paths),
            workspace_revision=manifest.workspace_revision,
            manifest_digest=manifest.manifest_digest,
            artifact_ref=artifact.as_ref(),
            created_at=datetime.fromtimestamp(manifest.created_at, timezone.utc),
        )
        return checkpoint, artifact

    def _snapshot(self, run: Run) -> RunSnapshot:
        return RunSnapshot(
            run=run,
            active_turn=self._active_turn(run),
            last_event_sequence=self._last_sequence(run.run_id),
        )

    def _outcome(self, run: Run, *, after_sequence: int = 0) -> RunOutcome:
        return RunOutcome(
            snapshot=self._snapshot(run),
            decision=run.stop_decision,
            events=tuple(
                event
                for event in self._all_events(run.run_id)
                if event.sequence > after_sequence
            ),
        )

    def _active_turn(self, run: Run) -> Turn | None:
        return (
            self.state.load_turn(run.active_turn_id)
            if run.active_turn_id is not None
            else None
        )

    def _all_events(self, run_id: str) -> tuple[Event, ...]:
        events: list[Event] = []
        cursor = 0
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

    def _session_summary(self, run: Run) -> str | None:
        prior_runs = [
            candidate
            for candidate in self.state.list_runs(run.session_id)
            if candidate.run_id != run.run_id
        ]
        if not prior_runs:
            return None
        lines = ["Prior Runs in this Session:"]
        for candidate in prior_runs[-8:]:
            lines.append(
                json.dumps(
                    {
                        "run_id": candidate.run_id,
                        "objective": candidate.objective,
                        "status": candidate.status.value,
                        "final_response": candidate.final_response,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        summary = "\n".join(lines)
        return summary[-12_000:]

    def _last_sequence(self, run_id: str) -> int:
        return self.state.next_event_sequence(run_id) - 1

    def _changed_paths(self, run_id: str) -> tuple[str, ...]:
        paths: set[str] = set()
        for event in self._all_events(run_id):
            if event.event_type != "tool.completed":
                continue
            for path in event.payload.get("changed_paths", ()):
                if isinstance(path, str):
                    paths.add(path)
        return tuple(sorted(paths))

    def _latest_diff(self, run_id: str) -> str | None:
        diffs: list[str] = []
        for event in self._all_events(run_id):
            if event.event_type != "tool.completed":
                continue
            raw = event.payload.get("message")
            if not isinstance(raw, Mapping):
                continue
            try:
                message = Message.from_dict(raw)
                payload = json.loads(message.content or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for block in payload.get("content", ()):
                if isinstance(block, Mapping) and block.get("kind") == "diff":
                    value = block.get("diff")
                    if isinstance(value, str):
                        diffs.append(value)
        return "\n".join(diffs) if diffs else None

    def _latest_diagnostics(self, run_id: str) -> tuple[Mapping[str, Any], ...]:
        diagnostics: list[Mapping[str, Any]] = []
        for event in self._all_events(run_id):
            if event.event_type != "tool.completed":
                continue
            raw = event.payload.get("message")
            if not isinstance(raw, Mapping):
                continue
            try:
                message = Message.from_dict(raw)
                payload = json.loads(message.content or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            diagnostics.extend(
                item
                for item in payload.get("diagnostics", ())
                if isinstance(item, Mapping)
            )
        return tuple(diagnostics)


def _decision_context(
    result: ModelResult | None,
    tool_results: tuple[ToolResult, ...],
    run: Run,
) -> Any:
    from rivet.runtime.policy import DecisionContext

    return DecisionContext(run=run, model_result=result, tool_results=tool_results)


def _encode_cursor(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_cursor(value: str | None) -> dict[str, Any]:
    if not value:
        return {"kind": "new_turn"}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeCommandError("resume cursor is invalid")
    return parsed


def _tool_cursor(
    turn: Turn,
    model_call: ModelCallRecord,
    proposals: Sequence[ToolProposal],
    index: int,
    context_digest: str,
    execution: ToolExecutionRecord,
) -> dict[str, Any]:
    return {
        "kind": "tool_batch",
        "turn_id": turn.turn_id,
        "model_call_id": model_call.model_call_id,
        "proposals": [proposal.to_dict() for proposal in proposals],
        "next_index": index,
        "context_digest": context_digest,
        "execution_id": execution.execution_id,
        "prepared_digest": execution.prepared_digest,
    }


def _domain_effect(value: EffectClass) -> DomainEffectClass:
    return DomainEffectClass(value.value.upper())


def _domain_side_effect(value: SideEffectState) -> DomainSideEffectState:
    return DomainSideEffectState(value.value.upper())


def _terminal_execution(
    execution: ToolExecutionRecord,
    result: ToolResult,
    ended_at: datetime,
) -> ToolExecutionRecord:
    statuses = {
        ToolResultStatus.SUCCESS: ToolExecutionStatus.SUCCEEDED,
        ToolResultStatus.ERROR: ToolExecutionStatus.FAILED,
        ToolResultStatus.DENIED: ToolExecutionStatus.DENIED,
        ToolResultStatus.TIMED_OUT: ToolExecutionStatus.TIMED_OUT,
        ToolResultStatus.CANCELLED: ToolExecutionStatus.CANCELLED,
        ToolResultStatus.PENDING_PERMISSION: ToolExecutionStatus.FAILED,
    }
    status = statuses[result.status]
    return replace(
        execution,
        status=status,
        result_summary=result.to_model_payload(),
        error=(
            _tool_error_info(result)
            if status
            in {
                ToolExecutionStatus.FAILED,
                ToolExecutionStatus.TIMED_OUT,
                ToolExecutionStatus.INTERRUPTED,
            }
            else None
        ),
        permission_decision=(
            DomainPermissionDecision.DENIED
            if status is ToolExecutionStatus.DENIED
            else execution.permission_decision
        ),
        side_effect_state=_domain_side_effect(result.side_effect_state),
        workspace_revision_after=result.workspace_revision,
        ended_at=ended_at,
    )


def _last_stream_usage(events: Sequence[ModelEvent]) -> Usage:
    for event in reversed(events):
        if event.usage is not None:
            return event.usage
    return Usage()


def _tool_error_info(result: ToolResult) -> ErrorInfo:
    mapping = {
        ErrorKind.TOOL_NOT_FOUND: DomainErrorKind.TOOL_NOT_FOUND,
        ErrorKind.TOOL_ARGUMENT_ERROR: DomainErrorKind.TOOL_ARGUMENT_ERROR,
        ErrorKind.TOOL_PERMISSION_REQUIRED: DomainErrorKind.TOOL_PERMISSION_DENIED,
        ErrorKind.TOOL_PERMISSION_DENIED: DomainErrorKind.TOOL_PERMISSION_DENIED,
        ErrorKind.TOOL_EXECUTION_ERROR: DomainErrorKind.TOOL_EXECUTION_ERROR,
        ErrorKind.TOOL_TIMEOUT: DomainErrorKind.TOOL_TIMEOUT,
        ErrorKind.TOOL_CANCELLED: DomainErrorKind.TOOL_CANCELLED,
        ErrorKind.WORKSPACE_VIOLATION: DomainErrorKind.WORKSPACE_VIOLATION,
        ErrorKind.WORKSPACE_CHANGED: DomainErrorKind.WORKSPACE_CHANGED,
        ErrorKind.CHECKPOINT_ERROR: DomainErrorKind.CHECKPOINT_ERROR,
        ErrorKind.STATE_CONFLICT: DomainErrorKind.STATE_CONFLICT,
        ErrorKind.VERIFICATION_FAILED: DomainErrorKind.VERIFICATION_FAILED,
        ErrorKind.INTERNAL_ERROR: DomainErrorKind.INTERNAL_ERROR,
    }
    return ErrorInfo(
        kind=mapping.get(result.error_kind, DomainErrorKind.INTERNAL_ERROR),
        message=result.error_message or "tool execution failed",
        retryable=result.retryable,
    )


def _model_error_info(error: ModelGatewayError) -> ErrorInfo:
    mapping = {
        ModelErrorKind.TRANSPORT: DomainErrorKind.MODEL_TRANSPORT_ERROR,
        ModelErrorKind.PROTOCOL: DomainErrorKind.MODEL_PROTOCOL_ERROR,
        ModelErrorKind.RATE_LIMIT: DomainErrorKind.MODEL_RATE_LIMIT,
        ModelErrorKind.AUTH: DomainErrorKind.MODEL_AUTH_ERROR,
        ModelErrorKind.CONTEXT_OVERFLOW: DomainErrorKind.CONTEXT_OVERFLOW,
        ModelErrorKind.UNAVAILABLE: DomainErrorKind.MODEL_TRANSPORT_ERROR,
        ModelErrorKind.CANCELLED: DomainErrorKind.USER_CANCELLED,
    }
    return ErrorInfo(
        kind=mapping[error.kind],
        message=str(error),
        retryable=error.retryable,
        details={
            "status_code": error.status_code,
            "provider_request_id": error.provider_request_id,
            "model_error_kind": error.kind.value,
        },
    )

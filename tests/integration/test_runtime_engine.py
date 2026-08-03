from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rivet.context import DefaultContextEngine
from rivet.domain import (
    EffectClass as DomainEffectClass,
)
from rivet.domain import (
    Event,
    EventActor,
    ModelCallRecord,
    ModelCallStatus,
    RunBudget,
    RunStatus,
    Session,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
    TurnPhase,
    TurnStatus,
    Workspace,
)
from rivet.domain import (
    PermissionDecision as DomainPermissionDecision,
)
from rivet.domain.common import utc_now
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.types import ModelResult, ToolProposal
from rivet.reviewer import ReviewFinding, ReviewResult
from rivet.runtime import (
    CancelRun,
    ResumeRun,
    RuntimeEngine,
    RuntimeSettings,
    StartRun,
)
from rivet.state.artifacts import ContentAddressedArtifactStore
from rivet.state.protocol import StateMutation
from rivet.state.sqlite import SQLiteStateStore
from rivet.tools.builtins import (
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    RunTestsTool,
)
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    EffectClass,
    PermissionClass,
    PreparedTool,
    ToolArguments,
    ToolExecutionContext,
    ToolPreparation,
    ToolPrepareContext,
    ToolSpec,
)
from rivet.tools.executor import ToolExecutor
from rivet.tools.results import TextBlock
from rivet.verification import (
    DefaultVerifier,
    VerificationCommand,
    VerificationPlan,
)
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.checkpoint import FileCheckpointService
from rivet.workspace.command import ProcessRunner


class PartialWriteArguments(ToolArguments):
    path: str


class PartialWriteTool:
    spec = ToolSpec(
        name="partial_write_test",
        version="1.0.0",
        description="Fail after a write may have partially completed.",
        input_model=PartialWriteArguments,
        output_types=(TextBlock,),
        effect=EffectClass.WRITE,
        permission=PermissionClass.WORKSPACE_WRITE,
        default_timeout=1.0,
        idempotent=False,
        parallel_safe=False,
    )

    def prepare(
        self,
        arguments: PartialWriteArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        return ToolPreparation(
            normalized_arguments={"path": target.relative_path},
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ):
        raise RuntimeError("simulated partial write")


class RuntimeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace_root = root / "workspace"
        self.workspace_root.mkdir()
        self.state_root = root / "state"
        self.state_root.mkdir()
        self.boundary = WorkspaceBoundary(self.workspace_root)
        self.workspace = Workspace.create(
            self.workspace_root,
            base_revision=self.boundary.revision(self.boundary.resolve(".")),
        )
        self.session = Session.create(self.workspace.workspace_id)

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    def engine(
        self,
        model: FakeModel,
        *,
        budget: RunBudget | None = None,
        writes: bool = False,
        repeat_limit: int = 2,
        reviewer: object | None = None,
        model_retries: int = 0,
        lease_ttl_seconds: float = 60.0,
        extra_tools: tuple[object, ...] = (),
    ):
        store = SQLiteStateStore(self.state_root / f"{id(model)}.sqlite3")
        tools = [ListFilesTool(), ReadFileTool()]
        if writes:
            tools.append(ApplyPatchTool())
        tools.extend(extra_tools)
        catalog = ToolCatalog(tools)
        executor = ToolExecutor(
            catalog,
            self.boundary,
            checkpoint_service=(
                FileCheckpointService(self.state_root / f"{id(model)}-checkpoints")
                if writes
                else None
            ),
        )
        engine = RuntimeEngine(
            state_store=store,
            context_engine=DefaultContextEngine(),
            model_gateway=model,
            tool_catalog=catalog,
            tool_executor=executor,
            artifact_store=(
                ContentAddressedArtifactStore(
                    self.state_root / f"{id(model)}-artifacts"
                )
                if writes
                else None
            ),
            verifier=(DefaultVerifier(ProcessRunner(self.boundary)) if writes else None),
            verification_plan_factory=(
                (
                    lambda _run, changed_paths, _events: VerificationPlan(
                        commands=(
                            VerificationCommand(
                                name="acceptance",
                                argv=(sys.executable, "-c", "raise SystemExit(0)"),
                            ),
                        ),
                        allowed_changed_paths=changed_paths,
                        require_diff=True,
                    )
                )
                if writes
                else None
            ),
            reviewer=reviewer,
            settings=RuntimeSettings(
                max_consecutive_identical_actions=repeat_limit,
                context_input_tokens_per_call=8_000,
                output_tokens_per_call=512,
                model_max_retries=model_retries,
                lease_ttl_seconds=lease_ttl_seconds,
            ),
        )
        return engine, store, budget or RunBudget(max_turns=5)

    async def test_drive_renews_lease_during_slow_model_stream(self) -> None:
        class SlowStreamingModel(FakeModel):
            async def stream(self, request):
                await asyncio.sleep(0.12)
                async for event in super().stream(request):
                    yield event

        model = SlowStreamingModel.scripted(
            [ModelResult(text="completed after a slow provider response")]
        )
        engine, store, budget = self.engine(model, lease_ttl_seconds=0.06)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="wait for a slow streamed response",
                budget=budget,
            )
        )

        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        self.assertEqual(
            outcome.run.final_response,
            "completed after a slow provider response",
        )
        store.close()

    async def test_partial_write_failure_pauses_for_workspace_review(self) -> None:
        target = self.workspace_root / "main.py"
        target.write_text("value = 1\n", encoding="utf-8")
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="partial-write",
                            ordinal=0,
                            name="partial_write_test",
                            arguments={"path": "main.py"},
                        ),
                    )
                ),
            ]
        )
        engine, store, budget = self.engine(
            model,
            writes=True,
            extra_tools=(PartialWriteTool(),),
        )
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="exercise partial write handling",
                budget=budget,
            )
        )
        permission_pause = await engine.drive(started.run.run_id)
        digest = str(permission_pause.run.stop_decision.evidence["prepared_digest"])

        outcome = await engine.resume_run(
            ResumeRun(
                run_id=permission_pause.run.run_id,
                pause_token=permission_pause.run.pause_token,
                permission_decisions={digest: "allow"},
            )
        )

        self.assertEqual(outcome.run.status, RunStatus.PAUSED)
        self.assertEqual(outcome.run.stop_decision.reason, "uncertain_side_effect")
        self.assertIsNone(outcome.run.active_turn_id)
        execution = store.list_tool_executions(outcome.run.run_id)[0]
        self.assertEqual(execution.status, ToolExecutionStatus.FAILED)
        store.close()

    async def test_failed_test_result_continues_after_known_command_completion(
        self,
    ) -> None:
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="failing-test",
                            ordinal=0,
                            name="run_tests",
                            arguments={
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "raise SystemExit(1)",
                                ]
                            },
                        ),
                    )
                ),
                ModelResult(text="The failing test is understood; continue fixing."),
            ]
        )
        engine, store, budget = self.engine(
            model,
            extra_tools=(RunTestsTool(),),
        )
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="observe a failing test and continue",
                budget=budget,
            )
        )
        permission_pause = await engine.drive(started.run.run_id)
        digest = str(permission_pause.run.stop_decision.evidence["prepared_digest"])

        outcome = await engine.resume_run(
            ResumeRun(
                run_id=permission_pause.run.run_id,
                pause_token=permission_pause.run.pause_token,
                permission_decisions={digest: "allow"},
            )
        )

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.final_response, "The failing test is understood; continue fixing.")
        execution = store.list_tool_executions(outcome.run.run_id)[0]
        self.assertEqual(execution.status, ToolExecutionStatus.FAILED)
        self.assertEqual(execution.side_effect_state.value, "APPLIED")
        store.close()

    async def test_retryable_provider_error_creates_a_new_attempt_in_same_turn(
        self,
    ) -> None:
        model = FakeModel(
            responses=(
                ConditionalResponse(
                    RequestCondition(call_index=0),
                    error=ModelGatewayError(
                        ModelErrorKind.TRANSPORT,
                        "temporary transport error",
                        retryable=True,
                    ),
                ),
                ConditionalResponse(
                    RequestCondition(call_index=1),
                    result=ModelResult(text="recovered without pausing"),
                ),
            )
        )
        engine, store, budget = self.engine(model, model_retries=1)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="retry provider once",
                budget=budget,
            )
        )
        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.run.usage.model_calls, 2)
        calls = store.list_model_calls(outcome.run.run_id)
        self.assertEqual(
            [call.status for call in calls],
            [ModelCallStatus.FAILED, ModelCallStatus.SUCCEEDED],
        )
        self.assertEqual(calls[1].attempt_no, 2)
        self.assertEqual(outcome.run.usage.turns, 1)
        store.close()

    async def test_unclassified_provider_exception_is_redacted_before_persistence(
        self,
    ) -> None:
        class LeakyFakeModel(FakeModel):
            def _select(self, request):
                self.requests.append(request)
                raise RuntimeError("api_key=do-not-persist-this-value")

        model = LeakyFakeModel(responses=())
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="exercise provider failure redaction",
                budget=budget,
            )
        )

        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.FAILED)
        call = store.list_model_calls(outcome.run.run_id)[0]
        self.assertIsNotNone(call.error)
        assert call.error is not None
        self.assertIn("[REDACTED]", call.error.message)
        persisted = json.dumps(
            [
                event.to_dict()
                for event in store.list_events(outcome.run.run_id)
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("do-not-persist-this-value", persisted)
        store.close()

    async def test_gateway_error_is_redacted_again_at_runtime_boundary(self) -> None:
        model = FakeModel(
            responses=(
                ConditionalResponse(
                    RequestCondition(call_index=0),
                    error=ModelGatewayError(
                        ModelErrorKind.PROTOCOL,
                        "api_key=classified-secret-value",
                    ),
                ),
            )
        )
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="exercise classified failure redaction",
                budget=budget,
            )
        )

        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.FAILED)
        call = store.list_model_calls(outcome.run.run_id)[0]
        self.assertIsNotNone(call.error)
        assert call.error is not None
        self.assertEqual(call.error.message, "api_key=[REDACTED]")
        events = json.dumps(
            [
                event.to_dict()
                for event in store.list_events(outcome.run.run_id)
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("classified-secret-value", events)
        store.close()

    async def test_reviewer_finding_returns_to_agent_before_completion(self) -> None:
        class SequencedReviewer:
            def __init__(self) -> None:
                self.calls = 0

            async def review(self, request: object) -> ReviewResult:
                self.calls += 1
                if self.calls == 1:
                    return ReviewResult(
                        summary="missing edge case",
                        findings=(
                            ReviewFinding(
                                severity="warning",
                                category="coverage",
                                message="explain the edge case",
                                path="main.py",
                            ),
                        ),
                    )
                return ReviewResult(summary="approved")

        target = self.workspace_root / "main.py"
        target.write_text("value = 1\n", encoding="utf-8")
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="write-review",
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
                ModelResult(text="first answer"),
                ModelResult(text="answer with edge-case explanation"),
            ]
        )
        reviewer = SequencedReviewer()
        engine, store, budget = self.engine(
            model,
            writes=True,
            reviewer=reviewer,
        )
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="update and review",
                budget=budget,
            )
        )
        paused = await engine.drive(started.run.run_id)
        digest = str(paused.run.stop_decision.evidence["prepared_digest"])
        completed = await engine.resume_run(
            ResumeRun(
                run_id=paused.run.run_id,
                pause_token=paused.run.pause_token,
                permission_decisions={digest: "allow"},
            )
        )

        self.assertEqual(completed.run.status, RunStatus.COMPLETED)
        self.assertEqual(completed.final_response, "answer with edge-case explanation")
        self.assertEqual(reviewer.calls, 2)
        event_types = [
            event.event_type for event in store.list_events(completed.run.run_id)
        ]
        self.assertEqual(event_types.count("reviewer.completed"), 2)
        self.assertIn("reviewer_changes_requested", {
            str(event.payload.get("decision", {}).get("reason", ""))
            for event in store.list_events(completed.run.run_id)
            if event.event_type == "stop.decided"
        })
        store.close()

    async def test_direct_answer_commits_terminal_run_and_events(self) -> None:
        model = FakeModel.scripted([ModelResult(text="done")])
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="answer directly",
                budget=budget,
            )
        )
        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.final_response, "done")
        self.assertEqual(outcome.run.usage.turns, 1)
        event_types = [event.event_type for event in store.list_events(outcome.run.run_id)]
        self.assertIn("model_call.started", event_types)
        self.assertEqual(event_types[-1], "run.completed")
        store.close()

    async def test_reasoning_content_survives_tool_round_trip(self) -> None:
        model = FakeModel.scripted(
            [
                ModelResult(
                    reasoning_content="I need a workspace listing.",
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="list-1",
                            ordinal=0,
                            name="list_files",
                            arguments={"path": "."},
                        ),
                    ),
                ),
                ModelResult(text="The workspace is empty."),
            ]
        )
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="inspect the workspace",
                budget=budget,
            )
        )

        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        assistant = next(
            message
            for message in model.requests[1].messages
            if message.tool_proposals
        )
        self.assertEqual(
            assistant.reasoning_content,
            "I need a workspace listing.",
        )
        completed_event = next(
            event
            for event in store.list_events(outcome.run.run_id)
            if event.event_type == "model_call.completed"
        )
        self.assertEqual(
            completed_event.payload["message"]["reasoning_content"],
            "I need a workspace listing.",
        )
        store.close()

    async def test_tool_result_is_projected_into_next_model_call(self) -> None:
        (self.workspace_root / "main.py").write_text("print('hello')\n", encoding="utf-8")
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="call-1",
                            ordinal=0,
                            name="list_files",
                            arguments={"path": ".", "max_depth": 2},
                        ),
                    )
                ),
                ModelResult(text="main.py exists"),
            ]
        )
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="inspect files",
                budget=budget,
            )
        )
        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.run.usage.turns, 2)
        self.assertEqual(outcome.run.usage.tool_executions, 1)
        second_request = model.requests[1]
        tool_messages = [
            message for message in second_request.messages if message.role.value == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("main.py", tool_messages[0].content or "")
        store.close()

    async def test_parallel_safe_reads_persist_and_project_in_ordinal_order(
        self,
    ) -> None:
        (self.workspace_root / "main.py").write_text("value = 1\n", encoding="utf-8")
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="list-second",
                            ordinal=1,
                            name="list_files",
                            arguments={},
                        ),
                        ToolProposal.from_arguments(
                            tool_call_id="read-first",
                            ordinal=0,
                            name="read_file",
                            arguments={"path": "main.py"},
                        ),
                    )
                ),
                ModelResult(text="observed both read results"),
            ]
        )
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="read in parallel",
                budget=budget,
            )
        )
        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.COMPLETED)
        completed_events = [
            event
            for event in store.list_events(outcome.run.run_id)
            if event.event_type == "tool.completed"
        ]
        self.assertEqual(
            [event.payload["tool_name"] for event in completed_events],
            ["read_file", "list_files"],
        )
        self.assertTrue(
            all(event.payload["parallel_batch"] for event in completed_events)
        )
        second_call_tools = [
            message.name
            for message in model.requests[1].messages
            if message.role.value == "tool"
        ]
        self.assertEqual(second_call_tools, ["read_file", "list_files"])
        store.close()

    async def test_turn_budget_pauses_after_tool_turn(self) -> None:
        model = FakeModel.scripted(
            [
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="call-1",
                            ordinal=0,
                            name="list_files",
                            arguments={},
                        ),
                    )
                )
            ]
        )
        engine, store, _ = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="keep looking",
                budget=RunBudget(max_turns=1),
            )
        )
        outcome = await engine.drive(started.run.run_id)

        self.assertEqual(outcome.run.status, RunStatus.PAUSED)
        self.assertEqual(outcome.run.stop_decision.reason, "budget_exhausted")
        self.assertIsNotNone(outcome.run.pause_token)
        store.close()

    async def test_permission_resume_executes_checkpointed_write_once(self) -> None:
        target = self.workspace_root / "main.py"
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
                ModelResult(text="updated and verified"),
            ]
        )
        engine, store, budget = self.engine(model, writes=True)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="update value",
                budget=budget,
            )
        )
        paused = await engine.drive(started.run.run_id)
        self.assertEqual(paused.run.status, RunStatus.PAUSED)
        self.assertEqual(paused.run.stop_decision.reason, "permission_required")
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

        digest = str(paused.run.stop_decision.evidence["prepared_digest"])
        completed = await engine.resume_run(
            ResumeRun(
                run_id=paused.run.run_id,
                pause_token=paused.run.pause_token,
                permission_decisions={digest: "allow"},
            )
        )
        self.assertEqual(completed.run.status, RunStatus.COMPLETED)
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
        event_types = [
            event.event_type for event in store.list_events(completed.run.run_id)
        ]
        self.assertEqual(event_types.count("checkpoint.created"), 1)
        self.assertEqual(event_types.count("tool.started"), 1)
        store.close()

    async def test_repeated_action_pauses_before_second_execution_and_can_resume(self) -> None:
        repeated = ToolProposal.from_arguments(
            tool_call_id="list-1",
            ordinal=0,
            name="list_files",
            arguments={},
        )
        model = FakeModel.scripted(
            [
                ModelResult(tool_proposals=(repeated,)),
                ModelResult(
                    tool_proposals=(
                        ToolProposal.from_arguments(
                            tool_call_id="list-2",
                            ordinal=0,
                            name="list_files",
                            arguments={},
                        ),
                    )
                ),
                ModelResult(text="done after confirmation"),
            ]
        )
        engine, store, budget = self.engine(model, repeat_limit=1)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="inspect twice",
                budget=budget,
            )
        )
        paused = await engine.drive(started.run.run_id)
        self.assertEqual(paused.run.status, RunStatus.PAUSED)
        self.assertEqual(paused.run.stop_decision.reason, "repeated_action")
        events = store.list_events(paused.run.run_id)
        self.assertEqual(sum(event.event_type == "tool.started" for event in events), 1)

        completed = await engine.resume_run(
            ResumeRun(
                run_id=paused.run.run_id,
                pause_token=paused.run.pause_token,
                allow_repeated_action_once=True,
            )
        )
        self.assertEqual(completed.run.status, RunStatus.COMPLETED)
        events = store.list_events(completed.run.run_id)
        self.assertEqual(sum(event.event_type == "tool.started" for event in events), 2)
        store.close()

    async def test_provider_unavailable_pauses_and_resume_starts_new_turn(self) -> None:
        model = FakeModel(
            responses=(
                ConditionalResponse(
                    RequestCondition(call_index=0),
                    error=ModelGatewayError(
                        ModelErrorKind.UNAVAILABLE,
                        "provider unavailable",
                        retryable=True,
                    ),
                ),
                ConditionalResponse(
                    RequestCondition(call_index=1),
                    result=ModelResult(text="recovered"),
                ),
            )
        )
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="recover provider",
                budget=budget,
            )
        )
        paused = await engine.drive(started.run.run_id)
        self.assertEqual(paused.run.status, RunStatus.PAUSED)
        self.assertEqual(paused.run.stop_decision.reason, "provider_unavailable")

        completed = await engine.resume_run(
            ResumeRun(
                run_id=paused.run.run_id,
                pause_token=paused.run.pause_token,
            )
        )
        self.assertEqual(completed.final_response, "recovered")
        self.assertEqual(len(model.requests), 2)
        store.close()

    async def test_cancel_created_run_is_terminal_and_idempotent(self) -> None:
        model = FakeModel.scripted([ModelResult(text="unused")])
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="cancel me",
                budget=budget,
            )
        )
        cancelled = await engine.cancel_run(
            CancelRun(started.run.run_id, reason="user_cancelled")
        )
        again = await engine.cancel_run(
            CancelRun(started.run.run_id, reason="ignored")
        )
        self.assertEqual(cancelled.run.status, RunStatus.CANCELLED)
        self.assertEqual(again.run.revision, cancelled.run.revision)
        self.assertEqual(len(model.requests), 0)
        store.close()

    async def test_recover_orphaned_running_run_pauses_with_explicit_reason(self) -> None:
        model = FakeModel.scripted([ModelResult(text="unused")])
        engine, store, budget = self.engine(model)
        started = await engine.start_run(
            StartRun(
                workspace=self.workspace,
                session=self.session,
                objective="recover me",
                budget=budget,
            )
        )
        lease = store.acquire_run_lease(
            started.run.run_id,
            "crashed-runtime",
            ttl_seconds=30,
        )
        now = utc_now()
        turn = Turn(
            turn_id="turn_recovery",
            run_id=started.run.run_id,
            ordinal=1,
            status=TurnStatus.ACTIVE,
            phase=TurnPhase.EXECUTE_TOOLS,
            started_at=now,
            created_at=now,
        )
        call = ModelCallRecord(
            model_call_id="model_call_recovery",
            turn_id=turn.turn_id,
            attempt_no=1,
            provider="fake",
            model="fake",
            status=ModelCallStatus.SUCCEEDED,
            context_id="context_recovery",
            request_digest="a" * 64,
            normalized_response={"text": "run a command"},
            started_at=now,
            ended_at=now,
        )
        execution = ToolExecutionRecord(
            execution_id="tool_execution_recovery",
            turn_id=turn.turn_id,
            model_call_id=call.model_call_id,
            tool_call_id="tool_call_recovery",
            ordinal=0,
            attempt_no=1,
            tool_name="run_command",
            tool_version="1.0",
            status=ToolExecutionStatus.RUNNING,
            normalized_arguments={"argv": ["example"]},
            effect_class=DomainEffectClass.EXECUTE,
            permission_decision=DomainPermissionDecision.GRANTED,
            prepared_digest="b" * 64,
            started_at=now,
        )
        running = replace(
            started.run,
            status=RunStatus.RUNNING,
            active_turn_id=turn.turn_id,
            revision=1,
        )
        event = Event.create(
            session_id=running.session_id,
            run_id=running.run_id,
            sequence=2,
            event_type="run.started",
            actor=EventActor.RUNTIME,
        )
        store.commit(
            StateMutation(
                run=running,
                expected_run_revision=0,
                lease_token=lease.token,
                turns=(turn,),
                model_calls=(call,),
                tool_executions=(execution,),
                events=(event,),
            )
        )
        store.release_run_lease(running.run_id, lease.token)

        recovered = await engine.recover_run(running.run_id)
        self.assertEqual(recovered.run.status, RunStatus.PAUSED)
        self.assertEqual(recovered.run.stop_decision.reason, "uncertain_side_effect")
        reconciled = store.load_tool_execution(execution.execution_id)
        self.assertEqual(reconciled.status, ToolExecutionStatus.INTERRUPTED)
        self.assertEqual(reconciled.side_effect_state.value, "UNCERTAIN")
        store.close()


if __name__ == "__main__":
    unittest.main()

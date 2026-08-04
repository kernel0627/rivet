from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from rivet.domain import (
    CURRENT_SCHEMA_VERSION,
    DomainValidationError,
    EffectClass,
    ErrorInfo,
    ErrorKind,
    Event,
    EventActor,
    PermissionDecision,
    Run,
    RunBudget,
    RunStatus,
    RunUsage,
    StopAction,
    StopDecision,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Workspace,
    validate_run_transition,
)
from rivet.domain.common import utc_now


class DomainInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.create(Path("/tmp/rivet-domain-workspace"))
        self.run = Run.create("ses_123", "inspect the project", "revision-1")

    def test_workspace_is_canonical_and_stable(self) -> None:
        another = Workspace.create(Path("/tmp/rivet-domain-workspace/../rivet-domain-workspace"))
        self.assertEqual(self.workspace.workspace_id, another.workspace_id)
        self.assertEqual(
            self.workspace.canonical_root,
            str(Path("/tmp/rivet-domain-workspace").resolve()),
        )

    def test_schema_version_is_rejected_when_newer_than_runtime(self) -> None:
        with self.assertRaises(DomainValidationError):
            replace(self.workspace, schema_version=CURRENT_SCHEMA_VERSION + 1)

    def test_completed_run_requires_answer_and_complete_decision(self) -> None:
        with self.assertRaisesRegex(ValueError, "final_response"):
            replace(self.run, status=RunStatus.COMPLETED)

        decision = StopDecision(
            action=StopAction.COMPLETE,
            reason="verification_satisfied",
        )
        completed = replace(
            self.run,
            status=RunStatus.COMPLETED,
            final_response="Done.",
            stop_decision=decision,
        )
        self.assertTrue(completed.status.terminal)

    def test_paused_run_requires_recovery_material(self) -> None:
        with self.assertRaisesRegex(ValueError, "pause_token"):
            replace(self.run, status=RunStatus.PAUSED)

        decision = StopDecision(
            action=StopAction.PAUSE,
            reason="permission_required",
            resumable=True,
            resume_requirements=("permission decision",),
        )
        paused = replace(
            self.run,
            status=RunStatus.PAUSED,
            pause_token="pause_123",
            resume_cursor="permission:req_123",
            stop_decision=decision,
        )
        self.assertEqual(paused.stop_decision.action, StopAction.PAUSE)

    def test_terminal_run_cannot_be_resurrected(self) -> None:
        decision = StopDecision(
            action=StopAction.COMPLETE,
            reason="assistant_finished",
        )
        completed = replace(
            self.run,
            status=RunStatus.COMPLETED,
            final_response="Done.",
            stop_decision=decision,
            revision=1,
        )
        running = replace(
            self.run,
            status=RunStatus.RUNNING,
            revision=2,
        )
        with self.assertRaisesRegex(ValueError, "invalid run status transition"):
            validate_run_transition(completed, running)

    def test_run_revision_must_advance_exactly_once(self) -> None:
        running = replace(self.run, status=RunStatus.RUNNING, revision=2)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_run_transition(self.run, running)

    def test_budget_limits_are_positive_and_usage_reports_exhaustion(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_turns"):
            RunBudget(max_turns=0)
        with self.assertRaisesRegex(ValueError, "max_reviewer_calls"):
            RunBudget(max_reviewer_calls=0)
        usage = RunUsage(turns=64, model_calls=2, artifact_bytes=1_000_000_000)
        exceeded = usage.exceeded(RunBudget())
        self.assertIn("turns", exceeded)
        self.assertIn("artifact_bytes", exceeded)
        self.assertNotIn("model_calls", exceeded)

        budget = RunBudget(max_reviewer_calls=3)
        self.assertEqual(RunBudget.from_dict(budget.to_dict()), budget)
        reviewer_usage = RunUsage(
            reviewer_calls=2,
            input_tokens=12,
            output_tokens=4,
            reviewer_input_tokens=12,
            reviewer_output_tokens=4,
        )
        self.assertEqual(
            RunUsage.from_dict(reviewer_usage.to_dict()),
            reviewer_usage,
        )

    def test_event_sequence_is_strictly_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "sequence"):
            Event.create(
                session_id="ses_123",
                run_id=self.run.run_id,
                sequence=0,
                event_type="run.created",
                actor=EventActor.RUNTIME,
            )

    def test_persisted_json_payloads_are_deeply_immutable(self) -> None:
        source = {"nested": {"values": [1, 2]}}
        event = Event.create(
            session_id="ses_123",
            run_id=self.run.run_id,
            sequence=1,
            event_type="run.created",
            actor=EventActor.RUNTIME,
            payload=source,
        )
        source["nested"]["values"].append(3)
        self.assertEqual(event.payload["nested"]["values"], (1, 2))
        with self.assertRaises(TypeError):
            event.payload["new"] = "value"

    def test_write_execution_cannot_start_without_permission_and_checkpoint(self) -> None:
        digest = "a" * 64
        with self.assertRaisesRegex(ValueError, "granted permission"):
            ToolExecutionRecord(
                execution_id="exec_1",
                turn_id="turn_1",
                model_call_id="call_1",
                tool_call_id="tool_call_1",
                ordinal=0,
                attempt_no=1,
                tool_name="apply_patch",
                tool_version="1",
                status=ToolExecutionStatus.READY,
                effect_class=EffectClass.WRITE,
                permission_decision=PermissionDecision.PENDING,
                prepared_digest=digest,
            )

    def test_tool_prepare_failure_can_be_recorded_without_prepared_digest(self) -> None:
        now = utc_now()
        execution = ToolExecutionRecord(
            execution_id="exec_prepare_failure",
            turn_id="turn_1",
            model_call_id="call_1",
            tool_call_id="tool_call_1",
            ordinal=0,
            attempt_no=1,
            tool_name="read_file",
            tool_version="1",
            status=ToolExecutionStatus.FAILED,
            error=ErrorInfo(
                kind=ErrorKind.TOOL_ARGUMENT_ERROR,
                message="path is missing",
            ),
            started_at=now,
            ended_at=now,
        )
        self.assertIsNone(execution.prepared_digest)

    def test_error_kinds_are_specific(self) -> None:
        self.assertNotEqual(ErrorKind.MODEL_RATE_LIMIT, ErrorKind.MODEL_TRANSPORT_ERROR)
        self.assertNotEqual(ErrorKind.WORKSPACE_CHANGED, ErrorKind.STATE_CONFLICT)


if __name__ == "__main__":
    unittest.main()

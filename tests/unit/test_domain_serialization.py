from __future__ import annotations

import unittest
from pathlib import Path

from rivet.domain import (
    Artifact,
    Checkpoint,
    CheckpointStatus,
    ErrorInfo,
    ErrorKind,
    Event,
    EventActor,
    ModelCallRecord,
    ModelCallStatus,
    Run,
    Session,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Turn,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    Workspace,
)
from rivet.domain.common import utc_now


class DomainSerializationTests(unittest.TestCase):
    def test_all_persisted_records_round_trip(self) -> None:
        workspace = Workspace.create(Path("/tmp/rivet-round-trip"))
        session = Session.create(workspace.workspace_id)
        run = Run.create(session.session_id, "round trip", workspace.current_revision)
        turn = Turn.create(run.run_id, 1)
        digest = "1" * 64
        artifact = Artifact(
            artifact_id=f"art_{digest}",
            sha256=digest,
            media_type="text/plain",
            size_bytes=4,
        )
        call = ModelCallRecord(
            model_call_id="call_1",
            turn_id=turn.turn_id,
            attempt_no=1,
            provider="fake",
            model="fake-1",
            status=ModelCallStatus.SUCCEEDED,
            context_id="ctx_1",
            request_digest=digest,
            normalized_response={"kind": "assistant", "text": "done"},
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        execution = ToolExecutionRecord(
            execution_id="exec_1",
            turn_id=turn.turn_id,
            model_call_id=call.model_call_id,
            tool_call_id="tool_call_1",
            ordinal=0,
            attempt_no=1,
            tool_name="read_file",
            tool_version="1",
            status=ToolExecutionStatus.SUCCEEDED,
            prepared_digest="2" * 64,
            result_summary={"lines": 4},
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        checkpoint = Checkpoint(
            checkpoint_id="checkpoint_1",
            run_id=run.run_id,
            turn_id=turn.turn_id,
            created_before_execution_id=execution.execution_id,
            status=CheckpointStatus.READY,
            scope=("src/main.py",),
            workspace_revision=workspace.current_revision,
            manifest_digest="3" * 64,
            artifact_ref=artifact.as_ref(),
        )
        check = VerificationCheck(
            name="unit tests",
            status=VerificationStatus.PASSED,
            summary="passed",
        )
        verification = VerificationResult(
            verification_id="verify_1",
            run_id=run.run_id,
            status=VerificationStatus.PASSED,
            checks=(check,),
            evidence=(artifact.as_ref(),),
        )
        event = Event.create(
            session_id=session.session_id,
            run_id=run.run_id,
            turn_id=turn.turn_id,
            sequence=1,
            event_type="turn.completed",
            actor=EventActor.RUNTIME,
            payload={"result": "ok"},
        )

        values_and_factories = (
            (workspace, Workspace.from_dict),
            (session, Session.from_dict),
            (run, Run.from_dict),
            (turn, Turn.from_dict),
            (artifact, Artifact.from_dict),
            (call, ModelCallRecord.from_dict),
            (execution, ToolExecutionRecord.from_dict),
            (checkpoint, Checkpoint.from_dict),
            (verification, VerificationResult.from_dict),
            (event, Event.from_dict),
        )
        for value, factory in values_and_factories:
            with self.subTest(type=type(value).__name__):
                self.assertEqual(value, factory(value.to_dict()))

    def test_error_info_round_trip(self) -> None:
        error = ErrorInfo(
            kind=ErrorKind.TOOL_TIMEOUT,
            message="command timed out",
            retryable=True,
            details={"seconds": 30},
        )
        self.assertEqual(error, ErrorInfo.from_dict(error.to_dict()))


if __name__ == "__main__":
    unittest.main()

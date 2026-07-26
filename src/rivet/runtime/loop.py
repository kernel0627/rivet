from __future__ import annotations

from dataclasses import dataclass

from rivet.runtime.stop_policy import StopPolicy
from rivet.runtime.turn import TurnRunner
from rivet.state.session import Session, StopReason
from rivet.state.store import SessionStore
from rivet.tracing.recorder import TraceEvent


@dataclass
class AgentLoop:
    turn_runner: TurnRunner
    stop_policy: StopPolicy
    session_store: SessionStore

    def run(self, session: Session) -> Session:
        self.session_store.save(session)
        for turn in range(1, session.max_turns + 2):
            stop_reason = self.stop_policy.before_turn(turn)
            if stop_reason:
                return self._stop(session, stop_reason)

            try:
                result = self.turn_runner.run(session, turn)
            except Exception as exc:
                session.error = f"{type(exc).__name__}: {exc}"
                self.turn_runner.trace.record(
                    TraceEvent(
                        event="model_failed",
                        turn=turn,
                        success=False,
                        data={"error": session.error},
                    )
                )
                return self._stop(session, StopReason.MODEL_ERROR)

            session.turn_count = turn
            self.session_store.save(session)

            if not result.response.tool_calls:
                session.final_response = result.response.content or ""
                return self._stop(session, StopReason.FINAL_ANSWER)

            call_stop = self.stop_policy.observe_calls(result.response.tool_calls)
            if call_stop:
                return self._stop(session, call_stop)
            result_stop = self.stop_policy.observe_results(result.tool_results)
            if result_stop:
                return self._stop(session, result_stop)

        return self._stop(session, StopReason.MAX_TURNS)

    def _stop(self, session: Session, reason: StopReason) -> Session:
        session.stop_reason = reason
        self.turn_runner.trace.record(
            TraceEvent(
                event="session_stopped",
                turn=session.turn_count,
                success=reason == StopReason.FINAL_ANSWER,
                data={"reason": reason.value},
            )
        )
        self.session_store.save(session)
        return session


from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from rivet.context.builder import ContextBuilder
from rivet.models.base import ModelAdapter
from rivet.models.types import Message, ModelResponse, ToolCall
from rivet.state.session import Session
from rivet.tools.base import ToolResult
from rivet.tools.registry import ToolRegistry
from rivet.tracing.recorder import TraceEvent, TraceRecorder


@dataclass(frozen=True)
class TurnResult:
    response: ModelResponse
    tool_results: list[ToolResult]


@dataclass
class TurnRunner:
    model: ModelAdapter
    tool_registry: ToolRegistry
    context_builder: ContextBuilder
    trace: TraceRecorder

    def run(self, session: Session, turn: int) -> TurnResult:
        messages = self.context_builder.build(session)
        started = monotonic()
        self.trace.record(TraceEvent(event="model_requested", turn=turn))
        response = self.model.complete(messages, self.tool_registry.model_schemas())
        duration_ms = int((monotonic() - started) * 1000)
        self.trace.record(
            TraceEvent(
                event="model_completed",
                turn=turn,
                success=True,
                duration_ms=duration_ms,
                data={
                    "finish_reason": response.finish_reason,
                    "tool_call_count": len(response.tool_calls),
                },
            )
        )
        session.messages.append(
            Message(
                role="assistant",
                content=response.content,
                tool_calls=list(response.tool_calls),
            )
        )

        results: list[ToolResult] = []
        for call in response.tool_calls:
            result = self._run_tool(call, turn)
            results.append(result)
            session.messages.append(
                Message(
                    role="tool",
                    content=result.to_model_text(),
                    tool_call_id=call.id,
                    name=call.name,
                )
            )
        return TurnResult(response=response, tool_results=results)

    def _run_tool(self, call: ToolCall, turn: int) -> ToolResult:
        started = monotonic()
        self.trace.record(
            TraceEvent(event="tool_started", turn=turn, tool=call.name)
        )
        result = self.tool_registry.execute(call)
        self.trace.record(
            TraceEvent(
                event="tool_completed",
                turn=turn,
                tool=call.name,
                success=result.ok,
                duration_ms=int((monotonic() - started) * 1000),
                data={"error": result.error},
            )
        )
        return result


from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from rivet.context.budget import HeuristicTokenEstimator
from rivet.model.errors import ModelGatewayError
from rivet.model.gateway import ModelGateway
from rivet.model.types import Message, MessageRole, ModelRequest, ToolProposal
from rivet.tools.builtins import (
    ApplyPatchTool,
    ReadFileTool,
    RunTestsTool,
    SearchTextTool,
)
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import ToolExecutionContext
from rivet.tools.executor import ToolExecutor
from rivet.tools.results import ErrorKind, SideEffectState, ToolResult
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.command import ProcessRunner

_SYSTEM_PROMPT = """You are a minimal coding agent operating in one workspace.
Use tools to inspect evidence, make the requested change, and run the requested tests.
Do not claim completion until the required edit and verification are complete.
In the final answer, name changed files and the exact verification command and result."""


@dataclass(frozen=True, slots=True)
class SimpleAgentBudget:
    max_model_calls: int
    max_tool_executions: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            min(
                self.max_model_calls,
                self.max_tool_executions,
                self.max_input_tokens,
                self.max_output_tokens,
            )
            <= 0
        ):
            raise ValueError("simple agent budget limits must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("simple agent timeout must be positive")


@dataclass(frozen=True, slots=True)
class SimpleToolTrace:
    tool_name: str
    status: str
    error_kind: str | None
    side_effect_state: str
    duration_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "error_kind": self.error_kind,
            "side_effect_state": self.side_effect_state,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class SimpleAgentResult:
    completed: bool
    stop_reason: str
    final_response: str
    model_calls: int
    tool_executions: int
    input_tokens: int
    output_tokens: int
    trace: tuple[SimpleToolTrace, ...]
    model_errors: tuple[dict[str, object], ...] = ()


class SimpleAgent:
    """A deliberately small Model → Tool → Observation loop.

    It has no persisted Run, permission broker, checkpoint snapshots, recovery,
    verifier, reviewer, retrieval, or rewind support. It reuses the production
    workspace-safe tool implementations so the comparison is about orchestration.
    """

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        workspace: Path,
        model: str,
        budget: SimpleAgentBudget,
    ) -> None:
        self.gateway = gateway
        self.boundary = WorkspaceBoundary(workspace)
        self.model = model
        self.budget = budget
        self.catalog = ToolCatalog(
            [
                ReadFileTool(),
                SearchTextTool(),
                ApplyPatchTool(checkpoint_required=False),
                RunTestsTool(),
            ]
        )
        self.preparer = ToolExecutor(self.catalog, self.boundary)
        self.services = {"process_runner": ProcessRunner(self.boundary)}
        self.estimator = HeuristicTokenEstimator()

    async def run(self, objective: str) -> SimpleAgentResult:
        messages = [
            Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=objective),
        ]
        model_calls = 0
        input_tokens = 0
        output_tokens = 0
        trace: list[SimpleToolTrace] = []
        model_errors: list[dict[str, object]] = []
        schemas = self.catalog.model_schemas()
        while model_calls < self.budget.max_model_calls:
            estimated_input = sum(
                self.estimator.estimate_message(message) for message in messages
            ) + self.estimator.estimate_tools(schemas)
            if estimated_input > self.budget.max_input_tokens:
                return _result(
                    completed=False,
                    stop_reason="context_budget_exhausted",
                    messages=messages,
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    trace=trace,
                    model_errors=model_errors,
                )
            try:
                response = await asyncio.wait_for(
                    self.gateway.complete(
                        ModelRequest(
                            messages=tuple(messages),
                            tools=schemas,
                            model=self.model,
                            max_output_tokens=self.budget.max_output_tokens,
                            timeout_seconds=self.budget.timeout_seconds,
                            metadata={"agent": "simple_baseline"},
                        )
                    ),
                    timeout=self.budget.timeout_seconds,
                )
            except asyncio.TimeoutError:
                model_calls += 1
                model_errors.append({"kind": "timeout", "retryable": True, "status_code": None})
                return _result(
                    completed=False,
                    stop_reason="model_timeout",
                    messages=messages,
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    trace=trace,
                    model_errors=model_errors,
                )
            except ModelGatewayError as error:
                model_calls += 1
                model_errors.append(
                    {
                        "kind": error.kind.value,
                        "retryable": error.retryable,
                        "status_code": error.status_code,
                    }
                )
                return _result(
                    completed=False,
                    stop_reason="model_failure",
                    messages=messages,
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    trace=trace,
                    model_errors=model_errors,
                )
            except Exception:
                model_calls += 1
                model_errors.append(
                    {
                        "kind": "unexpected",
                        "retryable": False,
                        "status_code": None,
                    }
                )
                return _result(
                    completed=False,
                    stop_reason="model_failure",
                    messages=messages,
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    trace=trace,
                    model_errors=model_errors,
                )
            model_calls += 1
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            messages.append(response.assistant_message)
            if not response.tool_proposals:
                return _result(
                    completed=True,
                    stop_reason="assistant_finished",
                    messages=messages,
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    trace=trace,
                    model_errors=model_errors,
                )
            for proposal in response.tool_proposals:
                if len(trace) >= self.budget.max_tool_executions:
                    return _result(
                        completed=False,
                        stop_reason="tool_budget_exhausted",
                        messages=messages,
                        model_calls=model_calls,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        trace=trace,
                        model_errors=model_errors,
                    )
                started_at = time.perf_counter()
                result = await self._execute(proposal)
                duration_ms = max(
                    result.duration_ms,
                    round((time.perf_counter() - started_at) * 1_000),
                )
                trace.append(
                    SimpleToolTrace(
                        tool_name=proposal.name,
                        status=result.status.value,
                        error_kind=(
                            result.error_kind.value if result.error_kind is not None else None
                        ),
                        side_effect_state=result.side_effect_state.value,
                        duration_ms=duration_ms,
                    )
                )
                messages.append(
                    Message(
                        role=MessageRole.TOOL,
                        content=result.to_model_text(),
                        tool_call_id=proposal.tool_call_id,
                        name=proposal.name,
                    )
                )
        return _result(
            completed=False,
            stop_reason="model_call_budget_exhausted",
            messages=messages,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            trace=trace,
            model_errors=model_errors,
        )

    async def _execute(self, proposal: ToolProposal) -> ToolResult:
        tool = self.catalog.get(proposal.name)
        if tool is None:
            return ToolResult.error(
                ErrorKind.TOOL_NOT_FOUND,
                f"unknown tool: {proposal.name}",
            )
        try:
            preparation = self.preparer.prepare(proposal)
        except (TypeError, ValueError, ValidationError) as error:
            return ToolResult.error(
                ErrorKind.TOOL_ARGUMENT_ERROR,
                str(error)[:2_000],
            )
        if preparation.error is not None:
            return preparation.error
        assert preparation.prepared is not None
        try:
            execution = tool.execute(
                preparation.prepared,
                ToolExecutionContext(
                    workspace=self.boundary,
                    services=self.services,
                ),
            )
            if inspect.isawaitable(execution):
                return await asyncio.wait_for(
                    execution,
                    timeout=preparation.prepared.timeout,
                )
            return execution
        except asyncio.TimeoutError:
            return ToolResult.error(
                ErrorKind.TOOL_TIMEOUT,
                f"tool timed out: {proposal.name}",
                side_effect_state=(
                    SideEffectState.UNCERTAIN
                    if preparation.prepared.effect.value != "READ"
                    else SideEffectState.NONE
                ),
            )
        except Exception as error:
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                f"{type(error).__name__}: {error}"[:2_000],
                side_effect_state=(
                    SideEffectState.UNCERTAIN
                    if preparation.prepared.effect.value != "READ"
                    else SideEffectState.NONE
                ),
            )


def _result(
    *,
    completed: bool,
    stop_reason: str,
    messages: list[Message],
    model_calls: int,
    input_tokens: int,
    output_tokens: int,
    trace: list[SimpleToolTrace],
    model_errors: list[dict[str, object]],
) -> SimpleAgentResult:
    final_response = ""
    if completed and messages and messages[-1].role is MessageRole.ASSISTANT:
        final_response = messages[-1].content or ""
    return SimpleAgentResult(
        completed=completed,
        stop_reason=stop_reason,
        final_response=final_response,
        model_calls=model_calls,
        tool_executions=len(trace),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        trace=tuple(trace),
        model_errors=tuple(model_errors),
    )

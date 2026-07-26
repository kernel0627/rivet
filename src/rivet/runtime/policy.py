from __future__ import annotations

from dataclasses import dataclass

from rivet.domain import (
    Run,
    StopAction,
    StopDecision,
    StopScope,
)
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.types import ModelResult
from rivet.tools.results import ToolResult


@dataclass(frozen=True)
class DecisionContext:
    run: Run
    model_result: ModelResult | None = None
    tool_results: tuple[ToolResult, ...] = ()


@dataclass(frozen=True)
class DefaultStopPolicy:
    def before_turn(self, run: Run) -> StopDecision | None:
        exceeded = run.usage.exceeded(run.budget)
        if not exceeded:
            return None
        return self.budget_exhausted(exceeded)

    def after_turn(self, context: DecisionContext) -> StopDecision:
        result = context.model_result
        if result is not None and not result.tool_proposals:
            if result.text and result.text.strip():
                return StopDecision(
                    action=StopAction.COMPLETE,
                    reason="assistant_finished",
                    scope=StopScope.RUN,
                    evidence={"has_final_response": True},
                )
        return StopDecision(
            action=StopAction.CONTINUE,
            reason="tool_observations_available",
            scope=StopScope.TURN,
            evidence={
                "tool_results": len(context.tool_results),
                "tool_failures": sum(not result.ok for result in context.tool_results),
            },
        )

    @staticmethod
    def budget_exhausted(exceeded: tuple[str, ...]) -> StopDecision:
        return StopDecision(
            action=StopAction.PAUSE,
            reason="budget_exhausted",
            scope=StopScope.RUN,
            resumable=True,
            resume_requirements=("increase_budget_or_start_new_run",),
            evidence={"exceeded": list(exceeded)},
            user_message="The configured Run budget has been exhausted.",
        )

    @staticmethod
    def repeated_action(
        *,
        action_key: str,
        prior_count: int,
    ) -> StopDecision:
        return StopDecision(
            action=StopAction.PAUSE,
            reason="repeated_action",
            scope=StopScope.RUN,
            resumable=True,
            resume_requirements=("user_confirmation_or_changed_plan",),
            evidence={
                "action_key": action_key,
                "prior_consecutive_count": prior_count,
            },
            user_message="The same normalized action is repeating without new evidence.",
        )

    @staticmethod
    def permission_required(
        *,
        tool_name: str,
        prepared_digest: str,
    ) -> StopDecision:
        return StopDecision(
            action=StopAction.PAUSE,
            reason="permission_required",
            scope=StopScope.RUN,
            resumable=True,
            resume_requirements=("permission_decision",),
            evidence={
                "tool_name": tool_name,
                "prepared_digest": prepared_digest,
            },
            user_message=f"Tool {tool_name!r} requires explicit approval.",
        )

    @staticmethod
    def provider_error(error: ModelGatewayError) -> StopDecision:
        if error.kind is ModelErrorKind.CANCELLED:
            return StopDecision(
                action=StopAction.CANCEL,
                reason="user_cancelled",
                scope=StopScope.RUN,
                evidence={"model_error_kind": error.kind.value},
            )
        if error.kind in {
            ModelErrorKind.TRANSPORT,
            ModelErrorKind.RATE_LIMIT,
            ModelErrorKind.UNAVAILABLE,
        }:
            return StopDecision(
                action=StopAction.PAUSE,
                reason="provider_unavailable",
                scope=StopScope.RUN,
                resumable=True,
                resume_requirements=("provider_available",),
                evidence={
                    "model_error_kind": error.kind.value,
                    "retryable": error.retryable,
                },
                user_message="The model provider is temporarily unavailable.",
            )
        if error.kind is ModelErrorKind.CONTEXT_OVERFLOW:
            return StopDecision(
                action=StopAction.PAUSE,
                reason="budget_exhausted",
                scope=StopScope.RUN,
                resumable=True,
                resume_requirements=("compact_context_or_increase_limit",),
                evidence={"model_error_kind": error.kind.value},
            )
        return StopDecision(
            action=StopAction.FAIL,
            reason="model_failure",
            scope=StopScope.RUN,
            evidence={
                "model_error_kind": error.kind.value,
                "retryable": error.retryable,
            },
            user_message=str(error),
        )

    @staticmethod
    def process_interrupted() -> StopDecision:
        return StopDecision(
            action=StopAction.PAUSE,
            reason="process_interrupted",
            scope=StopScope.RUN,
            resumable=True,
            resume_requirements=("review_interrupted_operations",),
            evidence={},
            user_message="A prior Runtime process stopped during an active Turn.",
        )

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rivet.model.types import Message, ToolSchema


class TokenEstimator(Protocol):
    def estimate_text(self, text: str) -> int:
        """Estimate tokens for provider-neutral text."""

    def estimate_message(self, message: Message) -> int:
        """Estimate one normalized message, including structural overhead."""

    def estimate_tools(self, tools: Sequence[ToolSchema]) -> int:
        """Estimate model-visible tool schemas."""


@dataclass(frozen=True)
class HeuristicTokenEstimator:
    """A deterministic tokenizer-independent estimate.

    UTF-8 bytes avoid severely undercounting CJK text while keeping the core
    independent from any provider tokenizer. The safety multiplier deliberately
    rounds upward.
    """

    bytes_per_token: float = 3.6
    safety_multiplier: float = 1.08
    message_overhead_tokens: int = 5

    def __post_init__(self) -> None:
        if self.bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive")
        if self.safety_multiplier < 1:
            raise ValueError("safety_multiplier must be at least 1")
        if self.message_overhead_tokens < 0:
            raise ValueError("message_overhead_tokens must be non-negative")

    def estimate_text(self, text: str) -> int:
        if not text:
            return 0
        raw = len(text.encode("utf-8")) / self.bytes_per_token
        return max(1, math.ceil(raw * self.safety_multiplier))

    def estimate_message(self, message: Message) -> int:
        serialized_proposals = json.dumps(
            [proposal.to_dict() for proposal in message.tool_proposals],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        structural = (
            message.role.value
            + (message.name or "")
            + (message.tool_call_id or "")
            + (message.source_label or "")
            + serialized_proposals
        )
        return (
            self.message_overhead_tokens
            + self.estimate_text(structural)
            + self.estimate_text(message.content or "")
        )

    def estimate_tools(self, tools: Sequence[ToolSchema]) -> int:
        if not tools:
            return 0
        payload = json.dumps(
            [tool.to_dict() for tool in tools],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.estimate_text(payload) + 4


@dataclass(frozen=True)
class ContextBudget:
    max_input_tokens: int
    reserved_output_tokens: int = 0
    model_context_window: int | None = None
    max_inline_source_tokens: int = 2_048
    max_working_memory_tokens: int = 1_024
    min_truncation_tokens: int = 48

    def __post_init__(self) -> None:
        if self.max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens must be non-negative")
        if self.model_context_window is not None and self.model_context_window <= 0:
            raise ValueError("model_context_window must be positive")
        if self.max_inline_source_tokens <= 0:
            raise ValueError("max_inline_source_tokens must be positive")
        if self.max_working_memory_tokens <= 0:
            raise ValueError("max_working_memory_tokens must be positive")
        if self.min_truncation_tokens <= 0:
            raise ValueError("min_truncation_tokens must be positive")
        if (
            self.model_context_window is not None
            and self.reserved_output_tokens >= self.model_context_window
        ):
            raise ValueError("reserved_output_tokens must be smaller than context window")

    @property
    def input_capacity(self) -> int:
        if self.model_context_window is None:
            return self.max_input_tokens
        return min(
            self.max_input_tokens,
            self.model_context_window - self.reserved_output_tokens,
        )


@dataclass(frozen=True)
class TokenEstimate:
    total_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    budget_tokens: int
    remaining_tokens: int
    estimator: str = "utf8-heuristic-v1"

    def __post_init__(self) -> None:
        if min(
            self.total_tokens,
            self.message_tokens,
            self.tool_schema_tokens,
            self.budget_tokens,
        ) < 0:
            raise ValueError("token estimates must be non-negative")
        if self.total_tokens != self.message_tokens + self.tool_schema_tokens:
            raise ValueError("total_tokens must equal messages plus tool schemas")
        if self.remaining_tokens != self.budget_tokens - self.total_tokens:
            raise ValueError("remaining_tokens does not match budget")

    def to_dict(self) -> dict[str, int | str]:
        return {
            "total_tokens": self.total_tokens,
            "message_tokens": self.message_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "budget_tokens": self.budget_tokens,
            "remaining_tokens": self.remaining_tokens,
            "estimator": self.estimator,
        }


class ContextBudgetExceeded(RuntimeError):
    def __init__(
        self,
        *,
        required_tokens: int,
        available_tokens: int,
        reason: str,
    ) -> None:
        super().__init__(
            f"context requires at least {required_tokens} tokens, "
            f"but only {available_tokens} are available: {reason}"
        )
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        self.reason = reason

from __future__ import annotations

from dataclasses import dataclass, field

from rivet.models.types import ToolCall
from rivet.state.session import StopReason
from rivet.tools.base import ToolResult


@dataclass
class StopPolicy:
    max_turns: int = 12
    repeated_call_limit: int = 3
    consecutive_error_limit: int = 3
    _call_counts: dict[str, int] = field(default_factory=dict)
    _consecutive_errors: int = 0

    def before_turn(self, turn: int) -> StopReason | None:
        if turn > self.max_turns:
            return StopReason.MAX_TURNS
        return None

    def observe_calls(self, calls: tuple[ToolCall, ...]) -> StopReason | None:
        for call in calls:
            self._call_counts[call.signature] = self._call_counts.get(call.signature, 0) + 1
            if self._call_counts[call.signature] > self.repeated_call_limit:
                return StopReason.REPEATED_TOOL_CALL
        return None

    def observe_results(self, results: list[ToolResult]) -> StopReason | None:
        if results and all(not result.ok for result in results):
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 0
        if self._consecutive_errors >= self.consecutive_error_limit:
            return StopReason.CONSECUTIVE_TOOL_ERRORS
        return None


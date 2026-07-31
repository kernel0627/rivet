from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from rivet.domain import ModelCallRecord, ToolExecutionRecord, Turn
from rivet.model.types import ToolProposal
from rivet.runtime.contracts import RuntimeCommandError


def encode_cursor(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_cursor(value: str | None) -> dict[str, Any]:
    if not value:
        return {"kind": "new_turn"}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeCommandError("resume cursor is invalid")
    return parsed


def tool_cursor(
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

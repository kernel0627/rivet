from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rivet.models.types import Message, ModelResponse


@dataclass
class ScriptedModel:
    """Deterministic adapter used by tests and local runtime experiments."""

    responses: list[ModelResponse]
    calls: list[list[Message]] = field(default_factory=list)

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        del tools
        self.calls.append(list(messages))
        if not self.responses:
            raise RuntimeError("scripted model has no response left")
        return self.responses.pop(0)


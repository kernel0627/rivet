from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from rivet.models.types import Message, ModelResponse


class ModelAdapter(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        """Return one assistant response for the current turn."""


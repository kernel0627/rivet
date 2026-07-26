from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from rivet.model.types import ModelEvent, ModelRequest, ModelResult


@runtime_checkable
class ModelGateway(Protocol):
    """Port implemented by every model provider adapter and fake."""

    async def complete(self, request: ModelRequest) -> ModelResult:
        """Return one complete internal result."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield normalized events for one streaming response."""

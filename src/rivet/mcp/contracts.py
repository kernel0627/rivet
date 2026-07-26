from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from rivet.tools.contracts import EffectClass, PermissionClass


@dataclass(frozen=True)
class McpToolDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    effect: EffectClass = EffectClass.NETWORK
    permission: PermissionClass = PermissionClass.NETWORK_ACCESS
    timeout_seconds: float = 60.0
    idempotent: bool = False
    parallel_safe: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP tool name cannot be empty")
        if not self.description.strip():
            raise ValueError("MCP tool description cannot be empty")
        if self.input_schema.get("type", "object") != "object":
            raise ValueError("MCP tool input schema must describe an object")
        if self.timeout_seconds <= 0:
            raise ValueError("MCP tool timeout must be positive")


@dataclass(frozen=True)
class McpCallResult:
    content: Sequence[str | Mapping[str, Any]] = ()
    is_error: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class McpClient(Protocol):
    async def list_tools(self) -> Sequence[McpToolDescriptor]:
        """Return the tools exposed by one configured MCP server."""

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> McpCallResult:
        """Execute one MCP tool call and return provider-neutral content."""

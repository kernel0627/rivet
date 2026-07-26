from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rivet.mcp.contracts import McpCallResult, McpToolDescriptor
from rivet.model import ToolProposal
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import EffectClass, PermissionClass
from rivet.tools.executor import ToolExecutor


class CodeIntelligenceMcpService:
    """Transport-neutral MCP server core for Rivet code-intelligence tools.

    A concrete stdio/SSE/HTTP MCP transport can delegate list_tools and
    call_tool to this service without bypassing Rivet's normal ToolExecutor.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        executor: ToolExecutor,
        *,
        tool_names: Sequence[str] = (
            "python_outline",
            "find_python_symbol",
            "read_python_symbol",
            "find_python_imports",
            "retrieve_code",
            "index_status",
            "refresh_index",
            "lsp_definition",
            "lsp_references",
            "lsp_hover",
            "lsp_diagnostics",
        ),
        services: Mapping[str, Any] | None = None,
    ) -> None:
        self.catalog = catalog
        self.executor = executor
        self.tool_names = tuple(tool_names)
        self.services = dict(services or {})
        missing = [name for name in self.tool_names if catalog.get(name) is None]
        if missing:
            raise ValueError(
                "code-intelligence MCP tools are not registered: "
                + ", ".join(missing)
            )

    async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        descriptors: list[McpToolDescriptor] = []
        for name in self.tool_names:
            spec = self.catalog.require(name).spec
            descriptors.append(
                McpToolDescriptor(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    effect=EffectClass.READ,
                    permission=PermissionClass.SAFE_READ,
                    timeout_seconds=spec.default_timeout,
                    idempotent=spec.idempotent,
                    parallel_safe=spec.parallel_safe,
                )
            )
        return tuple(descriptors)

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> McpCallResult:
        if name not in self.tool_names:
            return McpCallResult(
                content=({"error": f"unknown code-intelligence tool: {name}"},),
                is_error=True,
            )
        preparation = self.executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id=f"mcp-{name}",
                ordinal=0,
                name=name,
                arguments=arguments,
            )
        )
        if preparation.error is not None:
            return McpCallResult(
                content=(preparation.error.to_model_payload(),),
                is_error=True,
            )
        assert preparation.prepared is not None
        result = await self.executor.execute(
            preparation.prepared,
            services=self.services,
        )
        return McpCallResult(
            content=(result.to_model_payload(),),
            is_error=not result.ok,
            metadata={"tool_name": name},
        )

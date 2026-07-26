from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from rivet.mcp import (
    CodeIntelligenceMcpService,
    McpCallResult,
    McpToolDescriptor,
    discover_mcp_tools,
)
from rivet.model.types import ToolProposal
from rivet.tools.builtins.code_intelligence import PythonOutlineTool
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import PermissionOutcome
from rivet.tools.executor import ToolExecutor
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.permissions import StaticPermissionBroker


class _FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        return (
            McpToolDescriptor(
                name="lookup.issue",
                description="Look up one issue.",
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> McpCallResult:
        self.calls.append((name, dict(arguments)))
        return McpCallResult(content=({"key": arguments["key"], "state": "open"},))


class McpAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_tool_uses_normal_prepare_permission_and_execute_pipeline(
        self,
    ) -> None:
        client = _FakeMcpClient()
        tools = await discover_mcp_tools("tracker", client)
        with tempfile.TemporaryDirectory() as directory:
            executor = ToolExecutor(
                ToolCatalog(list(tools)),
                WorkspaceBoundary(Path(directory)),
                permission_broker=StaticPermissionBroker(
                    default_outcome=PermissionOutcome.ALLOW
                ),
            )
            proposal = ToolProposal.from_arguments(
                tool_call_id="mcp-1",
                ordinal=0,
                name="mcp_tracker_lookup_issue",
                arguments={"key": "RIV-42"},
            )

            preparation = executor.prepare(proposal)
            self.assertIsNone(preparation.error)
            assert preparation.prepared is not None
            preflight = await executor.preflight(preparation.prepared)
            self.assertIsNotNone(preflight.grant)
            assert preflight.grant is not None
            result = await executor.execute_preflighted(preflight.grant)

            self.assertTrue(result.ok)
            self.assertEqual(client.calls, [("lookup.issue", {"key": "RIV-42"})])
            self.assertIn("RIV-42", result.to_model_text())

    async def test_schema_rejects_missing_and_unknown_arguments(self) -> None:
        tools = await discover_mcp_tools("tracker", _FakeMcpClient())
        with tempfile.TemporaryDirectory() as directory:
            executor = ToolExecutor(
                ToolCatalog(list(tools)),
                WorkspaceBoundary(Path(directory)),
            )
            for arguments in ({}, {"key": "RIV-42", "extra": True}):
                preparation = executor.prepare(
                    ToolProposal.from_arguments(
                        tool_call_id=f"call-{len(arguments)}",
                        ordinal=0,
                        name="mcp_tracker_lookup_issue",
                        arguments=arguments,
                    )
                )
                self.assertIsNotNone(preparation.error)

    async def test_code_intelligence_server_core_uses_normal_executor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(
                "def answer():\n    return 42\n",
                encoding="utf-8",
            )
            catalog = ToolCatalog([PythonOutlineTool()])
            executor = ToolExecutor(catalog, WorkspaceBoundary(root))
            service = CodeIntelligenceMcpService(
                catalog,
                executor,
                tool_names=("python_outline",),
            )

            descriptors = await service.list_tools()
            result = await service.call_tool(
                "python_outline",
                {"path": "sample.py"},
            )

            self.assertEqual(descriptors[0].name, "python_outline")
            self.assertFalse(result.is_error)
            self.assertIn("answer", str(result.content[0]))


if __name__ == "__main__":
    unittest.main()

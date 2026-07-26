"""MCP tools adapted into Rivet's normal catalog and executor pipeline."""

from rivet.mcp.adapter import (
    McpToolAdapter,
    discover_mcp_tools,
    mcp_catalog_name,
    validate_json_arguments,
)
from rivet.mcp.code_intelligence_server import CodeIntelligenceMcpService
from rivet.mcp.contracts import McpCallResult, McpClient, McpToolDescriptor

__all__ = [
    "McpCallResult",
    "McpClient",
    "CodeIntelligenceMcpService",
    "McpToolAdapter",
    "McpToolDescriptor",
    "discover_mcp_tools",
    "mcp_catalog_name",
    "validate_json_arguments",
]

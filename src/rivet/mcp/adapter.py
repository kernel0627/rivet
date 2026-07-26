from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from rivet.mcp.contracts import McpCallResult, McpClient, McpToolDescriptor
from rivet.tools.contracts import (
    PreparedTool,
    ToolExecutionContext,
    ToolPreparation,
    ToolPrepareContext,
    ToolSpec,
)
from rivet.tools.results import ErrorKind, TextBlock, ToolResult


class McpArguments(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class McpToolAdapter:
    def __init__(
        self,
        *,
        server_name: str,
        descriptor: McpToolDescriptor,
        client: McpClient,
    ) -> None:
        self.server_name = server_name
        self.descriptor = descriptor
        self.client = client
        self.spec = ToolSpec(
            name=mcp_catalog_name(server_name, descriptor.name),
            version="mcp-1",
            description=f"[MCP {server_name}] {descriptor.description}",
            input_model=McpArguments,
            input_schema_override=descriptor.input_schema,
            output_types=(TextBlock,),
            effect=descriptor.effect,
            permission=descriptor.permission,
            default_timeout=descriptor.timeout_seconds,
            idempotent=descriptor.idempotent,
            parallel_safe=descriptor.parallel_safe,
        )

    def prepare(
        self,
        arguments: McpArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        normalized = arguments.model_dump(mode="json")
        validate_json_arguments(normalized, self.descriptor.input_schema)
        return ToolPreparation(
            normalized_arguments=normalized,
            metadata={
                "mcp_server": self.server_name,
                "mcp_tool": self.descriptor.name,
            },
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        result = await self.client.call_tool(
            self.descriptor.name,
            prepared.normalized_arguments,
        )
        if not isinstance(result, McpCallResult):
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                "MCP client returned an invalid result",
            )
        rendered = tuple(TextBlock(_render_content(item)) for item in result.content)
        metadata = {
            "mcp_server": self.server_name,
            "mcp_tool": self.descriptor.name,
            **dict(result.metadata),
        }
        if result.is_error:
            message = "\n".join(block.text for block in rendered)
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                message or "MCP tool reported an error",
                metadata=metadata,
            )
        return ToolResult.success(*rendered, metadata=metadata)


async def discover_mcp_tools(
    server_name: str,
    client: McpClient,
) -> tuple[McpToolAdapter, ...]:
    if not server_name.strip():
        raise ValueError("MCP server name cannot be empty")
    descriptors = await client.list_tools()
    names: set[str] = set()
    adapters: list[McpToolAdapter] = []
    for descriptor in descriptors:
        if descriptor.name in names:
            raise ValueError(f"MCP server returned duplicate tool {descriptor.name!r}")
        names.add(descriptor.name)
        adapters.append(
            McpToolAdapter(
                server_name=server_name,
                descriptor=descriptor,
                client=client,
            )
        )
    return tuple(adapters)


def mcp_catalog_name(server_name: str, tool_name: str) -> str:
    parts = [
        re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
        for value in (server_name, tool_name)
    ]
    stem = "_".join(part for part in parts if part)
    if not stem:
        raise ValueError("MCP server and tool names contain no usable characters")
    name = f"mcp_{stem}"
    return name[:64].rstrip("_")


def validate_json_arguments(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> None:
    required = schema.get("required", ())
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        raise ValueError("MCP schema required must be an array")
    missing = [str(name) for name in required if name not in value]
    if missing:
        raise ValueError("missing required MCP arguments: " + ", ".join(missing))
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError("MCP schema properties must be an object")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise ValueError("unexpected MCP arguments: " + ", ".join(extra))
    for name, item in value.items():
        item_schema = properties.get(name)
        if isinstance(item_schema, Mapping):
            _validate_json_value(item, item_schema, path=str(name))


def _validate_json_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    accepted = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, Mapping),
        "array": lambda item: (
            isinstance(item, Sequence) and not isinstance(item, (str, bytes))
        ),
        "null": lambda item: item is None,
    }
    if isinstance(expected, str) and expected in accepted and not accepted[expected](value):
        raise ValueError(f"MCP argument {path} must be {expected}")
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)):
        if value not in enum:
            raise ValueError(f"MCP argument {path} is not an allowed value")
    if expected == "array" and isinstance(value, Sequence):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_json_value(item, item_schema, path=f"{path}[{index}]")
    if expected == "object" and isinstance(value, Mapping):
        validate_json_arguments(value, schema)


def _render_content(value: str | Mapping[str, Any]) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    )

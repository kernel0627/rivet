from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rivet.models.types import ToolCall
from rivet.tools.base import Tool, ToolResult, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def model_schemas(self) -> list[dict[str, Any]]:
        return [spec.to_model_schema() for spec in self.specs()]

    def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool: {call.name}")
        validation_error = _validate_arguments(call.arguments, tool.spec.input_schema)
        if validation_error:
            return ToolResult(ok=False, error=validation_error)
        try:
            return tool.invoke(call.arguments)
        except Exception as exc:
            return ToolResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )


def _validate_arguments(arguments: Any, schema: Mapping[str, Any]) -> str | None:
    if not isinstance(arguments, dict):
        return "tool arguments must be an object"

    required = schema.get("required", [])
    missing = [name for name in required if name not in arguments]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"

    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return f"unknown arguments: {', '.join(unknown)}"

    python_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for name, value in arguments.items():
        expected_name = properties.get(name, {}).get("type")
        expected = python_types.get(expected_name)
        if expected and not isinstance(value, expected):
            return f"argument {name!r} must be {expected_name}"
    return None


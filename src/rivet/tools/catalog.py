from __future__ import annotations

from collections.abc import Collection, Iterator
from typing import Any

from rivet.model.types import ToolSchema
from rivet.tools.contracts import Tool, ToolSpec


class DuplicateToolError(ValueError):
    pass


class ToolCatalog:
    def __init__(
        self,
        tools: tuple[Tool, ...] | list[Tool] = (),
        *,
        model_visible_names: Collection[str] | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._model_visible_names = (
            frozenset(model_visible_names)
            if model_visible_names is not None
            else None
        )
        for tool in tools:
            self.register(tool)
        if self._model_visible_names is not None:
            unknown = self._model_visible_names - self._tools.keys()
            if unknown:
                raise ValueError(
                    "unknown model-visible tool(s): " + ", ".join(sorted(unknown))
                )

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(f"tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self.get(name)
        if tool is None:
            raise KeyError(name)
        return tool

    def specs(self, *, model_visible_only: bool = False) -> tuple[ToolSpec, ...]:
        specs = tuple(tool.spec for tool in self._tools.values())
        if model_visible_only:
            return tuple(
                spec
                for spec in specs
                if spec.model_visible
                and (
                    self._model_visible_names is None
                    or spec.name in self._model_visible_names
                )
            )
        return specs

    def model_schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(spec.to_model_tool_schema() for spec in self.specs(model_visible_only=True))

    def model_schema_payloads(self) -> list[dict[str, Any]]:
        return [spec.to_model_schema() for spec in self.specs(model_visible_only=True)]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

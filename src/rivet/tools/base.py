from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_model_text(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "output": self.output,
                "error": self.error,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
        )


class Tool(Protocol):
    spec: ToolSpec

    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute one validated tool request."""


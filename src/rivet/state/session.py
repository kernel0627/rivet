from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from rivet.models.types import Message


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StopReason(str, Enum):
    FINAL_ANSWER = "final_answer"
    MAX_TURNS = "max_turns"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    CONSECUTIVE_TOOL_ERRORS = "consecutive_tool_errors"
    MODEL_ERROR = "model_error"


@dataclass
class Session:
    id: str
    task: str
    workspace: str
    max_turns: int
    messages: list[Message] = field(default_factory=list)
    turn_count: int = 0
    stop_reason: StopReason | None = None
    final_response: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    @classmethod
    def create(cls, *, task: str, workspace: Path, max_turns: int) -> Session:
        return cls(
            id=uuid4().hex,
            task=task,
            workspace=str(workspace.resolve()),
            max_turns=max_turns,
            messages=[Message(role="user", content=task)],
        )

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "workspace": self.workspace,
            "max_turns": self.max_turns,
            "messages": [message.to_dict() for message in self.messages],
            "turn_count": self.turn_count,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "final_response": self.final_response,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Session:
        stop_reason = value.get("stop_reason")
        return cls(
            id=str(value["id"]),
            task=str(value["task"]),
            workspace=str(value["workspace"]),
            max_turns=int(value["max_turns"]),
            messages=[Message.from_dict(item) for item in value.get("messages", [])],
            turn_count=int(value.get("turn_count", 0)),
            stop_reason=StopReason(stop_reason) if stop_reason else None,
            final_response=value.get("final_response"),
            error=value.get("error"),
            created_at=str(value.get("created_at", _utc_now())),
            updated_at=str(value.get("updated_at", _utc_now())),
        )


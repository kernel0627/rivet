from __future__ import annotations

from dataclasses import dataclass

from rivet.models.types import Message
from rivet.state.session import Session

DEFAULT_SYSTEM_PROMPT = """You are Rivet, a coding agent operating inside one workspace.
Use tools to inspect evidence before making claims about the repository.
All tool paths must stay inside the workspace.
The current tool set is read-only. Do not claim to have edited or executed code.
When the task is complete, answer directly without requesting another tool.
"""


@dataclass(frozen=True)
class ContextBuilder:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def build(self, session: Session) -> list[Message]:
        workspace_note = (
            f"\nWorkspace root: {session.workspace}\n"
            f"Turn budget remaining: {max(session.max_turns - session.turn_count, 0)}"
        )
        return [
            Message(role="system", content=self.system_prompt.rstrip() + workspace_note),
            *session.messages,
        ]


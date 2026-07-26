from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rivet.context.builder import ContextBuilder
from rivet.models.base import ModelAdapter
from rivet.models.scripted import ScriptedModel
from rivet.models.types import ModelResponse
from rivet.runtime.loop import AgentLoop
from rivet.runtime.stop_policy import StopPolicy
from rivet.runtime.turn import TurnRunner
from rivet.safety.workspace import WorkspaceBoundary
from rivet.state.locations import workspace_state_directory
from rivet.state.session import Session
from rivet.state.store import SessionStore
from rivet.tools.filesystem import register_filesystem_tools
from rivet.tools.registry import ToolRegistry
from rivet.tracing.recorder import TraceRecorder


@dataclass
class Harness:
    workspace: Path
    model: ModelAdapter
    max_turns: int = 12
    state_directory: Path | None = None
    context_builder: ContextBuilder = field(default_factory=ContextBuilder)
    tool_registry: ToolRegistry = field(init=False)
    boundary: WorkspaceBoundary = field(init=False)
    session_store: SessionStore = field(init=False)

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self.boundary = WorkspaceBoundary(self.workspace)
        state_root = (
            Path(self.state_directory).expanduser().resolve()
            if self.state_directory is not None
            else workspace_state_directory(self.boundary.root)
        )
        self.session_store = SessionStore(state_root / "sessions")
        self.tool_registry = ToolRegistry()
        register_filesystem_tools(self.tool_registry, self.boundary)

    @classmethod
    def with_scripted_model(
        cls,
        *,
        workspace: Path,
        responses: Sequence[ModelResponse],
        max_turns: int = 12,
        state_directory: Path | None = None,
    ) -> Harness:
        return cls(
            workspace=workspace,
            model=ScriptedModel(list(responses)),
            max_turns=max_turns,
            state_directory=state_directory,
        )

    def run(self, task: str) -> Session:
        if not task.strip():
            raise ValueError("task must not be empty")
        session = Session.create(
            task=task.strip(),
            workspace=self.boundary.root,
            max_turns=self.max_turns,
        )
        state_root = self.session_store.directory.parent
        trace = TraceRecorder(state_root / "traces" / f"{session.id}.jsonl")
        loop = AgentLoop(
            turn_runner=TurnRunner(
                model=self.model,
                tool_registry=self.tool_registry,
                context_builder=self.context_builder,
                trace=trace,
            ),
            stop_policy=StopPolicy(max_turns=self.max_turns),
            session_store=self.session_store,
        )
        return loop.run(session)

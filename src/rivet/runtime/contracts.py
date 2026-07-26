from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from rivet.domain import (
    Event,
    Run,
    RunBudget,
    Session,
    StopDecision,
    Turn,
    Workspace,
)


@dataclass(frozen=True)
class StartRun:
    workspace: Workspace
    session: Session
    objective: str
    budget: RunBudget = field(default_factory=RunBudget)
    config_snapshot: Mapping[str, Any] = field(default_factory=dict)
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.session.workspace_id != self.workspace.workspace_id:
            raise ValueError("session and workspace identities do not match")
        if not self.objective.strip():
            raise ValueError("run objective must not be empty")


@dataclass(frozen=True)
class ResumeRun:
    run_id: str
    pause_token: str
    user_message: str | None = None
    allow_repeated_action_once: bool = False
    permission_decisions: Mapping[str, str] = field(default_factory=dict)
    budget: RunBudget | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.pause_token.strip():
            raise ValueError("pause_token must not be empty")
        if self.user_message is not None and not self.user_message.strip():
            raise ValueError("user_message must not be blank")


@dataclass(frozen=True)
class CancelRun:
    run_id: str
    reason: str = "user_cancelled"

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.reason.strip():
            raise ValueError("cancel reason must not be empty")


@dataclass(frozen=True)
class RunSnapshot:
    run: Run
    active_turn: Turn | None
    last_event_sequence: int


@dataclass(frozen=True)
class RunOutcome:
    snapshot: RunSnapshot
    decision: StopDecision | None
    events: tuple[Event, ...] = ()

    @property
    def run(self) -> Run:
        return self.snapshot.run

    @property
    def final_response(self) -> str | None:
        return self.snapshot.run.final_response


@dataclass(frozen=True)
class RuntimeSettings:
    provider_name: str = "fake"
    model_name: str = "fake"
    owner_id: str | None = None
    lease_ttl_seconds: float = 60.0
    context_input_tokens_per_call: int = 32_000
    output_tokens_per_call: int = 4_096
    model_context_window: int | None = None
    stream_model: bool = True
    model_max_retries: int = 0
    reviewer_blocking_severities: tuple[str, ...] = ("error", "warning")
    max_consecutive_identical_actions: int = 2
    system_instructions: tuple[str, ...] = (
        "You are Rivet, a coding agent operating inside one workspace.",
        "Inspect evidence with tools before making repository claims.",
        "Respect workspace, permission, budget, and cancellation boundaries.",
    )
    project_instructions: tuple[str, ...] = ()
    tool_services: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.provider_name.strip() or not self.model_name.strip():
            raise ValueError("provider_name and model_name must not be empty")
        if self.owner_id is not None and not self.owner_id.strip():
            raise ValueError("owner_id must not be blank")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if self.context_input_tokens_per_call <= 0:
            raise ValueError("context_input_tokens_per_call must be positive")
        if self.output_tokens_per_call <= 0:
            raise ValueError("output_tokens_per_call must be positive")
        if self.model_context_window is not None and self.model_context_window <= 0:
            raise ValueError("model_context_window must be positive")
        if self.max_consecutive_identical_actions <= 0:
            raise ValueError("max_consecutive_identical_actions must be positive")
        if self.model_max_retries < 0:
            raise ValueError("model_max_retries must be non-negative")
        if any(
            severity not in {"error", "warning", "info"}
            for severity in self.reviewer_blocking_severities
        ):
            raise ValueError("invalid reviewer blocking severity")


class RuntimeClock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware current time."""


class RuntimeIdFactory(Protocol):
    def new(self, prefix: str) -> str:
        """Return a new domain-safe identifier."""


class RuntimeCommandError(RuntimeError):
    pass


class RuntimeBusyError(RuntimeCommandError):
    pass

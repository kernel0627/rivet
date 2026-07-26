from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable

from rivet.domain import VerificationResult
from rivet.domain.common import require_identifier, require_non_empty


@runtime_checkable
class ProcessOutcome(Protocol):
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int


@runtime_checkable
class CommandExecutor(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_stdout_bytes: int = 100_000,
        max_stderr_bytes: int = 100_000,
        **kwargs: Any,
    ) -> ProcessOutcome: ...


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = 300.0
    required: bool = True
    environment: Mapping[str, str] = field(default_factory=dict)
    max_output_bytes: int = 100_000

    def __post_init__(self) -> None:
        require_non_empty(self.name, "name")
        if not self.argv or any(not item or "\x00" in item for item in self.argv):
            raise ValueError("argv must contain non-empty arguments without null bytes")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    commands: tuple[VerificationCommand, ...] = ()
    allowed_changed_paths: tuple[str, ...] = ()
    forbidden_changed_patterns: tuple[str, ...] = ()
    require_diff: bool = False
    fail_on_error_diagnostics: bool = True
    acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for path in self.allowed_changed_paths:
            _validate_relative_pattern(path, "allowed_changed_paths")
        for pattern in self.forbidden_changed_patterns:
            _validate_relative_pattern(pattern, "forbidden_changed_patterns")
        for criterion in self.acceptance_criteria:
            require_non_empty(criterion, "acceptance_criteria item")


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    run_id: str
    plan: VerificationPlan
    changed_paths: tuple[str, ...] = ()
    diff_text: str | None = None
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    acceptance_results: Mapping[str, bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_identifier(self.run_id, "run_id")
        for path in self.changed_paths:
            _validate_relative_path(path)
        unknown = set(self.acceptance_results).difference(self.plan.acceptance_criteria)
        if unknown:
            raise ValueError(
                "acceptance_results contains undeclared criteria: "
                + ", ".join(sorted(unknown))
            )


@runtime_checkable
class Verifier(Protocol):
    async def verify(self, request: VerificationRequest) -> VerificationResult: ...


def matches_path(path: str, pattern: str) -> bool:
    """Match a normalized workspace-relative path against an exact path or glob."""

    if any(character in pattern for character in "*?["):
        return fnmatchcase(path, pattern)
    normalized = pattern.rstrip("/")
    return path == normalized or path.startswith(f"{normalized}/")


def _validate_relative_path(path: str) -> None:
    _validate_relative_pattern(path, "changed_paths")
    if any(character in path for character in "*?["):
        raise ValueError("changed_paths entries cannot be glob patterns")


def _validate_relative_pattern(pattern: str, field_name: str) -> None:
    if (
        not pattern
        or "\x00" in pattern
        or pattern.startswith("/")
        or "\\" in pattern
        or ".." in PurePosixPath(pattern).parts
    ):
        raise ValueError(f"{field_name} must contain safe workspace-relative paths")

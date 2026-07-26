from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, TypeAlias


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    PENDING_PERMISSION = "pending_permission"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ErrorKind(str, Enum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_ARGUMENT_ERROR = "tool_argument_error"
    TOOL_PERMISSION_REQUIRED = "tool_permission_required"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_CANCELLED = "tool_cancelled"
    WORKSPACE_VIOLATION = "workspace_violation"
    WORKSPACE_CHANGED = "workspace_changed"
    CHECKPOINT_ERROR = "checkpoint_error"
    STATE_CONFLICT = "state_conflict"
    VERIFICATION_FAILED = "verification_failed"
    INTERNAL_ERROR = "internal_error"


class SideEffectState(str, Enum):
    NONE = "none"
    NOT_STARTED = "not_started"
    APPLIED = "applied"
    PARTIAL = "partial"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TextBlock:
    text: str
    kind: str = field(default="text", init=False)


@dataclass(frozen=True)
class CodeBlock:
    code: str
    language: str | None = None
    path: str | None = None
    start_line: int | None = None
    kind: str = field(default="code", init=False)


@dataclass(frozen=True)
class CodeSpan:
    path: str
    start_line: int
    end_line: int
    text: str
    sha256: str | None = None
    kind: str = field(default="code_span", init=False)


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    code: str | None = None
    kind: str = field(default="diagnostic", init=False)


@dataclass(frozen=True)
class DiffBlock:
    diff: str
    paths: tuple[str, ...] = ()
    kind: str = field(default="diff", init=False)


@dataclass(frozen=True)
class CommandOutput:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    kind: str = field(default="command_output", init=False)


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    sha256: str
    media_type: str
    size: int
    kind: str = field(default="artifact_ref", init=False)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    path: str
    text: str
    start_line: int | None = None
    end_line: int | None = None
    score: float | None = None
    kind: str = field(default="retrieved_chunk", init=False)


ContentBlock: TypeAlias = (
    TextBlock
    | CodeBlock
    | CodeSpan
    | Diagnostic
    | DiffBlock
    | CommandOutput
    | ArtifactRef
    | RetrievedChunk
)


def content_block_to_dict(block: ContentBlock) -> dict[str, Any]:
    return asdict(block)


@dataclass(frozen=True)
class ToolResult:
    status: ToolResultStatus
    content: tuple[ContentBlock, ...] = ()
    error_kind: ErrorKind | None = None
    error_message: str | None = None
    retryable: bool = False
    duration_ms: int = 0
    truncated: bool = False
    artifact_refs: tuple[ArtifactRef, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    code_spans: tuple[CodeSpan, ...] = ()
    workspace_revision: str | None = None
    side_effect_state: SideEffectState = SideEffectState.NONE
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        if self.status is ToolResultStatus.SUCCESS and self.error_kind is not None:
            raise ValueError("successful ToolResult cannot have error_kind")
        if self.status is not ToolResultStatus.SUCCESS and self.error_kind is None:
            raise ValueError("non-success ToolResult must have error_kind")

    @property
    def ok(self) -> bool:
        return self.status is ToolResultStatus.SUCCESS

    @classmethod
    def success(
        cls,
        *content: ContentBlock,
        side_effect_state: SideEffectState = SideEffectState.NONE,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            status=ToolResultStatus.SUCCESS,
            content=tuple(content),
            side_effect_state=side_effect_state,
            metadata=metadata or {},
        )

    @classmethod
    def error(
        cls,
        kind: ErrorKind,
        message: str,
        *,
        retryable: bool = False,
        status: ToolResultStatus = ToolResultStatus.ERROR,
        side_effect_state: SideEffectState = SideEffectState.NOT_STARTED,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            status=status,
            error_kind=kind,
            error_message=message,
            retryable=retryable,
            side_effect_state=side_effect_state,
            metadata=metadata or {},
        )

    def with_updates(self, **changes: Any) -> ToolResult:
        return replace(self, **changes)

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "content": [content_block_to_dict(block) for block in self.content],
            "error": (
                {
                    "kind": self.error_kind.value,
                    "message": self.error_message,
                    "retryable": self.retryable,
                }
                if self.error_kind is not None
                else None
            ),
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "artifact_refs": [asdict(item) for item in self.artifact_refs],
            "diagnostics": [asdict(item) for item in self.diagnostics],
            "code_spans": [asdict(item) for item in self.code_spans],
            "workspace_revision": self.workspace_revision,
            "side_effect_state": self.side_effect_state.value,
            "metadata": dict(self.metadata),
        }

    def to_model_text(self) -> str:
        return json.dumps(
            self.to_model_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

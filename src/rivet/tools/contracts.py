from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

from rivet.model.types import ToolProposal as ToolProposal
from rivet.model.types import ToolSchema
from rivet.tools.results import ContentBlock, ToolResult
from rivet.workspace.boundary import ResolvedPath, WorkspaceBoundary

if TYPE_CHECKING:
    from rivet.workspace.checkpoint import CheckpointManifest

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class EffectClass(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class PermissionClass(str, Enum):
    SAFE_READ = "safe_read"
    SENSITIVE_READ = "sensitive_read"
    WORKSPACE_WRITE = "workspace_write"
    PROCESS_EXECUTE = "process_execute"
    NETWORK_ACCESS = "network_access"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"


class PermissionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PermissionScope(str, Enum):
    ONCE = "once"
    RUN = "run"
    SESSION = "session"
    WORKSPACE = "workspace"
    ALWAYS_ASK = "always_ask"
    DENY = "deny"


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_types: tuple[type[ContentBlock], ...]
    effect: EffectClass
    permission: PermissionClass
    default_timeout: float
    idempotent: bool
    parallel_safe: bool
    model_visible: bool = True
    input_schema_override: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError(f"invalid tool name: {self.name!r}")
        if not self.version.strip():
            raise ValueError("tool version cannot be empty")
        if not self.description.strip():
            raise ValueError("tool description cannot be empty")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic BaseModel type")
        if self.default_timeout <= 0:
            raise ValueError("default_timeout must be positive")

    @property
    def input_schema(self) -> dict[str, Any]:
        if self.input_schema_override is not None:
            return json.loads(
                json.dumps(
                    dict(self.input_schema_override),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                )
            )
        return self.input_model.model_json_schema(mode="validation")

    def to_model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_model_tool_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.input_schema,
            strict=False,
        )


@dataclass(frozen=True)
class ToolPreparation:
    normalized_arguments: Mapping[str, JSONValue]
    resolved_targets: tuple[ResolvedPath, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedTool:
    tool_call_id: str
    ordinal: int
    name: str
    version: str
    normalized_arguments: Mapping[str, JSONValue]
    resolved_targets: tuple[ResolvedPath, ...]
    effect: EffectClass
    permission: PermissionClass
    timeout: float
    idempotent: bool
    parallel_safe: bool
    prepared_digest: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def digest_payload(self) -> dict[str, JSONValue]:
        return {
            "name": self.name,
            "version": self.version,
            "normalized_arguments": dict(self.normalized_arguments),
            "resolved_targets": [
                {
                    "relative_path": target.relative_path,
                    "canonical_path": str(target.path),
                    "existed": target.existed,
                }
                for target in self.resolved_targets
            ],
            "effect": self.effect.value,
            "permission": self.permission.value,
            "timeout": self.timeout,
            "idempotent": self.idempotent,
            "parallel_safe": self.parallel_safe,
            "metadata": dict(self.metadata),
        }

    def recompute_digest(self) -> str:
        return compute_prepared_digest(self.digest_payload())


def compute_prepared_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PermissionRequest:
    prepared_digest: str
    tool_name: str
    tool_version: str
    effect: EffectClass
    permission: PermissionClass
    normalized_arguments: Mapping[str, JSONValue]
    targets: tuple[str, ...]

    @classmethod
    def from_prepared(cls, prepared: PreparedTool) -> PermissionRequest:
        return cls(
            prepared_digest=prepared.prepared_digest,
            tool_name=prepared.name,
            tool_version=prepared.version,
            effect=prepared.effect,
            permission=prepared.permission,
            normalized_arguments=prepared.normalized_arguments,
            targets=tuple(target.relative_path for target in prepared.resolved_targets),
        )


@dataclass(frozen=True)
class PermissionDecision:
    outcome: PermissionOutcome
    prepared_digest: str
    scope: PermissionScope = PermissionScope.ONCE
    reason: str | None = None


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    prepared: PreparedTool
    permission_decision: PermissionDecision
    checkpoint: CheckpointManifest | None
    preflight_workspace_revision: str
    preflight_duration_ms: int
    grant_digest: str

    def digest_payload(self) -> dict[str, JSONValue]:
        checkpoint_payload: dict[str, JSONValue] | None
        if self.checkpoint is None:
            checkpoint_payload = None
        else:
            checkpoint_payload = {
                "checkpoint_id": self.checkpoint.checkpoint_id,
                "manifest_digest": self.checkpoint.manifest_digest,
                "prepared_digest": self.checkpoint.prepared_digest,
            }
        return {
            "grant_id": self.grant_id,
            "prepared_digest": self.prepared.prepared_digest,
            "permission_outcome": self.permission_decision.outcome.value,
            "permission_scope": self.permission_decision.scope.value,
            "permission_digest": self.permission_decision.prepared_digest,
            "checkpoint": checkpoint_payload,
            "preflight_workspace_revision": self.preflight_workspace_revision,
        }

    def recompute_digest(self) -> str:
        return compute_prepared_digest(self.digest_payload())


class PermissionBroker(Protocol):
    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        """Return a decision bound to request.prepared_digest."""


@dataclass(frozen=True)
class ToolPrepareContext:
    workspace: WorkspaceBoundary


@dataclass(frozen=True)
class ToolExecutionContext:
    workspace: WorkspaceBoundary
    checkpoint: Any | None = None
    services: Mapping[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    spec: ToolSpec

    def prepare(
        self,
        arguments: BaseModel,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        """Normalize validated arguments and resolve all affected targets."""

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult | Awaitable[ToolResult]:
        """Execute an already authorized and revalidated prepared action."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PermissionMode = Literal["allow", "ask", "deny"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(StrictModel):
    provider: str = "openai"
    model: str | None = None
    base_url: str | None = None
    api_key_env: str = "RIVET_API_KEY"
    timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    max_retries: int = Field(default=2, ge=0, le=10)
    stream: bool = True

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        from rivet.model.providers import normalize_provider_name

        normalized = normalize_provider_name(value)
        if not normalized:
            raise ValueError("provider must not be empty")
        return normalized


class RuntimeConfig(StrictModel):
    max_turns: int = Field(default=30, ge=1, le=1000)
    max_model_calls: int = Field(default=60, ge=1, le=5000)
    max_tool_executions: int = Field(default=200, ge=1, le=10000)
    max_wall_time_seconds: float = Field(default=3600.0, gt=0)
    max_command_time_seconds: float = Field(default=300.0, gt=0)
    max_artifact_bytes: int = Field(default=100_000_000, ge=1_000_000)
    run_lease_seconds: int = Field(default=60, ge=5, le=3600)


class PermissionConfig(StrictModel):
    safe_read: PermissionMode = "allow"
    sensitive_read: PermissionMode = "ask"
    workspace_write: PermissionMode = "ask"
    process_execute: PermissionMode = "ask"
    network_access: PermissionMode = "ask"
    external_write: PermissionMode = "ask"
    destructive: PermissionMode = "ask"


class ContextConfig(StrictModel):
    max_input_tokens: int = Field(default=100_000, ge=1_000)
    reserve_output_tokens: int = Field(default=8_000, ge=256)
    max_tool_result_chars: int = Field(default=30_000, ge=1_000)
    recent_turns: int = Field(default=12, ge=1, le=200)
    compaction: bool = True


class RetrievalConfig(StrictModel):
    enabled: bool = False
    sparse: bool = True
    dense: bool = True
    reranker: bool = True
    top_k_sparse: int = Field(default=30, ge=1, le=1000)
    top_k_dense: int = Field(default=30, ge=1, le=1000)
    top_k_final: int = Field(default=8, ge=1, le=100)
    qdrant_url: str | None = None
    collection_prefix: str = "rivet"


class TuiConfig(StrictModel):
    enabled: bool = True
    color: bool = True
    show_usage: bool = True
    show_tool_arguments: bool = True


class ReviewerConfig(StrictModel):
    enabled: bool = False
    blocking_severities: tuple[Literal["error", "warning", "info"], ...] = (
        "error",
        "warning",
    )


class StateConfig(StrictModel):
    root: Path | None = None
    database_name: str = "rivet.db"
    artifact_directory: str = "artifacts"

    @field_validator("database_name", "artifact_directory")
    @classmethod
    def validate_single_component(cls, value: str) -> str:
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ValueError("must be one non-empty path component")
        return value


class RivetConfig(StrictModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    reviewer: ReviewerConfig = Field(default_factory=ReviewerConfig)
    tui: TuiConfig = Field(default_factory=TuiConfig)
    state: StateConfig = Field(default_factory=StateConfig)

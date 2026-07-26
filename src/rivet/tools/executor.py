from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    EffectClass,
    ExecutionGrant,
    JSONValue,
    PermissionBroker,
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
    PreparedTool,
    Tool,
    ToolExecutionContext,
    ToolPrepareContext,
    ToolProposal,
)
from rivet.tools.middleware.output_budget import OutputBudgetLimiter
from rivet.tools.results import (
    ErrorKind,
    SideEffectState,
    ToolResult,
    ToolResultStatus,
)
from rivet.workspace.boundary import (
    WorkspaceBoundary,
    WorkspaceChanged,
    WorkspaceViolation,
)
from rivet.workspace.checkpoint import (
    CheckpointService,
    create_checkpoint,
)
from rivet.workspace.permissions import DefaultPermissionBroker


@dataclass(frozen=True)
class PreparationOutcome:
    prepared: PreparedTool | None = None
    error: ToolResult | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.error is None):
            raise ValueError("PreparationOutcome requires exactly one of prepared or error")

    @property
    def ok(self) -> bool:
        return self.prepared is not None


@dataclass(frozen=True)
class PreflightOutcome:
    grant: ExecutionGrant | None = None
    error: ToolResult | None = None

    def __post_init__(self) -> None:
        if (self.grant is None) == (self.error is None):
            raise ValueError("PreflightOutcome requires exactly one of grant or error")

    @property
    def ok(self) -> bool:
        return self.grant is not None


class ToolExecutor:
    def __init__(
        self,
        catalog: ToolCatalog,
        workspace: WorkspaceBoundary,
        *,
        permission_broker: PermissionBroker | None = None,
        checkpoint_service: CheckpointService | None = None,
        output_limiter: OutputBudgetLimiter | None = None,
    ) -> None:
        self.catalog = catalog
        self.workspace = workspace
        self.permission_broker = permission_broker or DefaultPermissionBroker()
        self.checkpoint_service = checkpoint_service
        self.output_limiter = output_limiter or OutputBudgetLimiter()
        self._consumed_grants: set[str] = set()
        self._grant_lock = asyncio.Lock()

    def prepare(self, proposal: ToolProposal) -> PreparationOutcome:
        tool = self.catalog.get(proposal.name)
        if tool is None:
            return PreparationOutcome(
                error=ToolResult.error(
                    ErrorKind.TOOL_NOT_FOUND,
                    f"unknown tool: {proposal.name}",
                )
            )
        try:
            arguments = tool.spec.input_model.model_validate(proposal.arguments)
            preparation = tool.prepare(
                arguments,
                ToolPrepareContext(workspace=self.workspace),
            )
            normalized = _json_mapping(preparation.normalized_arguments)
            prepared_without_digest = PreparedTool(
                tool_call_id=proposal.tool_call_id,
                ordinal=proposal.ordinal,
                name=tool.spec.name,
                version=tool.spec.version,
                normalized_arguments=normalized,
                resolved_targets=preparation.resolved_targets,
                effect=tool.spec.effect,
                permission=tool.spec.permission,
                timeout=tool.spec.default_timeout,
                idempotent=tool.spec.idempotent,
                parallel_safe=tool.spec.parallel_safe,
                prepared_digest="",
                metadata=_json_mapping(preparation.metadata),
            )
            prepared = PreparedTool(
                **{
                    **prepared_without_digest.__dict__,
                    "prepared_digest": prepared_without_digest.recompute_digest(),
                }
            )
            return PreparationOutcome(prepared=prepared)
        except WorkspaceViolation as exc:
            return PreparationOutcome(
                error=ToolResult.error(
                    ErrorKind.WORKSPACE_VIOLATION,
                    _safe_error(exc),
                )
            )
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return PreparationOutcome(
                error=ToolResult.error(
                    ErrorKind.TOOL_ARGUMENT_ERROR,
                    _safe_error(exc),
                )
            )
        except Exception as exc:
            return PreparationOutcome(
                error=ToolResult.error(
                    ErrorKind.INTERNAL_ERROR,
                    _safe_error(exc),
                )
            )

    async def preflight(
        self,
        prepared: PreparedTool,
        *,
        permission_decision: PermissionDecision | None = None,
        execution_metadata: Mapping[str, str] | None = None,
    ) -> PreflightOutcome:
        """Authorize, checkpoint, and revalidate without starting the tool."""

        started = time.monotonic()
        contract_error = self._validate_prepared_contract(prepared)
        if contract_error is not None:
            return PreflightOutcome(error=_timed(contract_error, started))
        target_error = self._revalidate_targets(prepared)
        if target_error is not None:
            return PreflightOutcome(error=_timed(target_error, started))

        request = PermissionRequest.from_prepared(prepared)
        try:
            decision = permission_decision or await self.permission_broker.decide(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return PreflightOutcome(
                error=_timed(
                    ToolResult.error(ErrorKind.INTERNAL_ERROR, _safe_error(exc)),
                    started,
                )
            )
        permission_error = _validate_permission_decision(prepared, decision)
        if permission_error is not None:
            return PreflightOutcome(error=_timed(permission_error, started))

        checkpoint = None
        if prepared.effect is EffectClass.WRITE:
            if self.checkpoint_service is None:
                return PreflightOutcome(
                    error=_timed(
                        ToolResult.error(
                            ErrorKind.CHECKPOINT_ERROR,
                            "write action cannot start without a checkpoint service",
                        ),
                        started,
                    )
                )
            try:
                checkpoint = await create_checkpoint(
                    self.checkpoint_service,
                    boundary=self.workspace,
                    targets=prepared.resolved_targets,
                    tool_name=prepared.name,
                    prepared_digest=prepared.prepared_digest,
                    metadata=execution_metadata,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return PreflightOutcome(
                    error=_timed(
                        ToolResult.error(
                            ErrorKind.CHECKPOINT_ERROR,
                            _safe_error(exc),
                        ),
                        started,
                    )
                )

        target_error = self._revalidate_targets(prepared)
        if target_error is not None:
            return PreflightOutcome(
                error=_timed(
                    _with_checkpoint(target_error, checkpoint),
                    started,
                )
            )
        workspace_revision = self.workspace.revision(*prepared.resolved_targets)
        grant_without_digest = ExecutionGrant(
            grant_id=uuid.uuid4().hex,
            prepared=prepared,
            permission_decision=decision,
            checkpoint=checkpoint,
            preflight_workspace_revision=workspace_revision,
            preflight_duration_ms=int((time.monotonic() - started) * 1000),
            grant_digest="",
        )
        grant = ExecutionGrant(
            **{
                **grant_without_digest.__dict__,
                "grant_digest": grant_without_digest.recompute_digest(),
            }
        )
        return PreflightOutcome(grant=grant)

    async def execute_preflighted(
        self,
        grant: ExecutionGrant,
        *,
        services: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Execute one preflight grant after Runtime persists its RUNNING fact."""

        started = time.monotonic()
        prepared = grant.prepared
        if grant.recompute_digest() != grant.grant_digest:
            return _timed(
                ToolResult.error(
                    ErrorKind.STATE_CONFLICT,
                    "execution grant digest does not match its authorized action",
                ),
                started,
            )
        contract_error = self._validate_prepared_contract(prepared)
        if contract_error is not None:
            return _timed(contract_error, started)
        permission_error = _validate_permission_decision(
            prepared,
            grant.permission_decision,
        )
        if permission_error is not None:
            return _timed(permission_error, started)
        if prepared.effect is EffectClass.WRITE:
            if grant.checkpoint is None:
                return _timed(
                    ToolResult.error(
                        ErrorKind.CHECKPOINT_ERROR,
                        "write execution grant has no checkpoint",
                    ),
                    started,
                )
            if grant.checkpoint.prepared_digest != prepared.prepared_digest:
                return _timed(
                    ToolResult.error(
                        ErrorKind.CHECKPOINT_ERROR,
                        "checkpoint does not match prepared action",
                    ),
                    started,
                )
        elif grant.checkpoint is not None:
            return _timed(
                ToolResult.error(
                    ErrorKind.CHECKPOINT_ERROR,
                    "non-write execution grant unexpectedly contains a checkpoint",
                ),
                started,
            )

        async with self._grant_lock:
            if grant.grant_digest in self._consumed_grants:
                return _timed(
                    ToolResult.error(
                        ErrorKind.STATE_CONFLICT,
                        "execution grant has already been consumed",
                    ),
                    started,
                )
            self._consumed_grants.add(grant.grant_digest)

        target_error = self._revalidate_targets(prepared)
        if target_error is not None:
            return _timed(
                _with_checkpoint(target_error, grant.checkpoint),
                started,
            )

        tool = self.catalog.require(prepared.name)
        context = ToolExecutionContext(
            workspace=self.workspace,
            checkpoint=grant.checkpoint,
            services=services or {},
        )
        try:
            result = await asyncio.wait_for(
                _invoke_tool(tool, prepared, context),
                timeout=prepared.timeout,
            )
            if not isinstance(result, ToolResult):
                raise TypeError("tool execute() must return ToolResult")
        except asyncio.TimeoutError:
            side_effect = (
                SideEffectState.NONE
                if prepared.effect is EffectClass.READ
                else SideEffectState.UNCERTAIN
            )
            result = ToolResult.error(
                ErrorKind.TOOL_TIMEOUT,
                f"tool timed out after {prepared.timeout:g} seconds",
                retryable=prepared.idempotent,
                status=ToolResultStatus.TIMED_OUT,
                side_effect_state=side_effect,
            )
        except asyncio.CancelledError:
            result = ToolResult.error(
                ErrorKind.TOOL_CANCELLED,
                "tool execution was cancelled",
                status=ToolResultStatus.CANCELLED,
                side_effect_state=(
                    SideEffectState.NONE
                    if prepared.effect is EffectClass.READ
                    else SideEffectState.UNCERTAIN
                ),
            )
        except WorkspaceChanged as exc:
            result = ToolResult.error(
                ErrorKind.WORKSPACE_CHANGED,
                _safe_error(exc),
                side_effect_state=_failure_side_effect(prepared.effect),
            )
        except WorkspaceViolation as exc:
            result = ToolResult.error(
                ErrorKind.WORKSPACE_VIOLATION,
                _safe_error(exc),
                side_effect_state=_failure_side_effect(prepared.effect),
            )
        except Exception as exc:
            result = ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                _safe_error(exc),
                retryable=prepared.idempotent and prepared.effect is EffectClass.READ,
                side_effect_state=_failure_side_effect(prepared.effect),
            )

        result = _with_checkpoint(result, grant.checkpoint).with_updates(
            duration_ms=int((time.monotonic() - started) * 1000),
            workspace_revision=self.workspace.revision(*prepared.resolved_targets),
        )
        return self.output_limiter.apply(result)

    async def execute(
        self,
        prepared: PreparedTool,
        *,
        permission_decision: PermissionDecision | None = None,
        execution_metadata: Mapping[str, str] | None = None,
        services: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Compatibility wrapper for callers without a persistent Runtime."""

        started = time.monotonic()
        preflight = await self.preflight(
            prepared,
            permission_decision=permission_decision,
            execution_metadata=execution_metadata,
        )
        if preflight.error is not None:
            return preflight.error
        assert preflight.grant is not None
        result = await self.execute_preflighted(
            preflight.grant,
            services=services,
        )
        return result.with_updates(duration_ms=int((time.monotonic() - started) * 1000))

    def _validate_prepared_contract(
        self,
        prepared: PreparedTool,
    ) -> ToolResult | None:
        tool = self.catalog.get(prepared.name)
        if tool is None or tool.spec.version != prepared.version:
            return ToolResult.error(
                ErrorKind.TOOL_NOT_FOUND,
                f"prepared tool is no longer registered: {prepared.name}@{prepared.version}",
            )
        if not _spec_matches_prepared(tool, prepared):
            return ToolResult.error(
                ErrorKind.TOOL_NOT_FOUND,
                f"tool contract changed after preparation: {prepared.name}@{prepared.version}",
            )
        if prepared.recompute_digest() != prepared.prepared_digest:
            return ToolResult.error(
                ErrorKind.TOOL_ARGUMENT_ERROR,
                "prepared tool digest does not match its normalized action",
            )
        return None

    def _revalidate_targets(self, prepared: PreparedTool) -> ToolResult | None:
        try:
            for target in prepared.resolved_targets:
                self.workspace.revalidate(
                    target,
                    require_unchanged=prepared.effect is EffectClass.WRITE,
                )
        except WorkspaceChanged as exc:
            return ToolResult.error(
                ErrorKind.WORKSPACE_CHANGED,
                _safe_error(exc),
            )
        except WorkspaceViolation as exc:
            return ToolResult.error(
                ErrorKind.WORKSPACE_VIOLATION,
                _safe_error(exc),
            )
        return None


async def _invoke_tool(
    tool: Tool,
    prepared: PreparedTool,
    context: ToolExecutionContext,
) -> ToolResult:
    if inspect.iscoroutinefunction(tool.execute):
        result = tool.execute(prepared, context)
        assert inspect.isawaitable(result)
        return await result
    result = await asyncio.to_thread(tool.execute, prepared, context)
    if inspect.isawaitable(result):
        return await result
    return result


def _json_mapping(value: Mapping[str, Any]) -> dict[str, JSONValue]:
    serialized = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parsed = json.loads(serialized)
    if not isinstance(parsed, dict):
        raise TypeError("normalized tool arguments must be a JSON object")
    return parsed


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip()
    rendered = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return rendered[:2_000]


def _timed(result: ToolResult, started: float) -> ToolResult:
    return result.with_updates(duration_ms=int((time.monotonic() - started) * 1000))


def _failure_side_effect(effect: EffectClass) -> SideEffectState:
    if effect is EffectClass.READ:
        return SideEffectState.NONE
    if effect is EffectClass.WRITE:
        return SideEffectState.PARTIAL
    return SideEffectState.UNCERTAIN


def _spec_matches_prepared(tool: Tool, prepared: PreparedTool) -> bool:
    spec = tool.spec
    return (
        spec.effect is prepared.effect
        and spec.permission is prepared.permission
        and spec.default_timeout == prepared.timeout
        and spec.idempotent is prepared.idempotent
        and spec.parallel_safe is prepared.parallel_safe
    )


def _validate_permission_decision(
    prepared: PreparedTool,
    decision: PermissionDecision,
) -> ToolResult | None:
    if not isinstance(decision, PermissionDecision):
        return ToolResult.error(
            ErrorKind.INTERNAL_ERROR,
            "permission broker returned an invalid decision",
        )
    if decision.prepared_digest != prepared.prepared_digest:
        return ToolResult.error(
            ErrorKind.TOOL_PERMISSION_DENIED,
            "permission decision does not match prepared action",
            status=ToolResultStatus.DENIED,
        )
    if decision.outcome is PermissionOutcome.REQUIRE_APPROVAL:
        return ToolResult.error(
            ErrorKind.TOOL_PERMISSION_REQUIRED,
            decision.reason or "tool action requires approval",
            status=ToolResultStatus.PENDING_PERMISSION,
        )
    if decision.outcome is PermissionOutcome.DENY:
        return ToolResult.error(
            ErrorKind.TOOL_PERMISSION_DENIED,
            decision.reason or "tool action was denied",
            status=ToolResultStatus.DENIED,
        )
    if decision.outcome is not PermissionOutcome.ALLOW:
        return ToolResult.error(
            ErrorKind.INTERNAL_ERROR,
            "permission broker returned an unknown outcome",
        )
    return None


def _with_checkpoint(result: ToolResult, checkpoint: Any | None) -> ToolResult:
    if checkpoint is None:
        return result
    metadata = dict(result.metadata)
    metadata["checkpoint_id"] = checkpoint.checkpoint_id
    metadata["checkpoint_manifest_digest"] = checkpoint.manifest_digest
    return result.with_updates(metadata=metadata)

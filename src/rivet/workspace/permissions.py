from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from rivet.configuration.models import PermissionConfig
from rivet.tools.contracts import (
    PermissionClass,
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
    PermissionScope,
)


class DefaultPermissionBroker:
    """Conservative policy: workspace-safe reads proceed; all else pauses."""

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if request.permission is PermissionClass.SAFE_READ:
            return PermissionDecision(
                outcome=PermissionOutcome.ALLOW,
                prepared_digest=request.prepared_digest,
                scope=PermissionScope.ONCE,
                reason="safe workspace read",
            )
        return PermissionDecision(
            outcome=PermissionOutcome.REQUIRE_APPROVAL,
            prepared_digest=request.prepared_digest,
            scope=PermissionScope.ALWAYS_ASK,
            reason=f"{request.permission.value} requires explicit approval",
        )


@dataclass
class StaticPermissionBroker:
    """Deterministic broker useful for application policy adapters and tests."""

    default_outcome: PermissionOutcome = PermissionOutcome.REQUIRE_APPROVAL
    decisions: Mapping[str, PermissionOutcome] = field(default_factory=dict)
    reason: str | None = None

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        outcome = self.decisions.get(request.prepared_digest, self.default_outcome)
        scope = PermissionScope.DENY if outcome is PermissionOutcome.DENY else PermissionScope.ONCE
        return PermissionDecision(
            outcome=outcome,
            prepared_digest=request.prepared_digest,
            scope=scope,
            reason=self.reason,
        )


@dataclass(frozen=True)
class ConfigPermissionBroker:
    """Map validated application policy to one prepared action decision."""

    config: PermissionConfig

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        mode = getattr(self.config, request.permission.value)
        outcome = {
            "allow": PermissionOutcome.ALLOW,
            "ask": PermissionOutcome.REQUIRE_APPROVAL,
            "deny": PermissionOutcome.DENY,
        }[mode]
        return PermissionDecision(
            outcome=outcome,
            prepared_digest=request.prepared_digest,
            scope=(
                PermissionScope.DENY
                if outcome is PermissionOutcome.DENY
                else PermissionScope.ONCE
                if outcome is PermissionOutcome.ALLOW
                else PermissionScope.ALWAYS_ASK
            ),
            reason=f"{request.permission.value} policy is {mode}",
        )

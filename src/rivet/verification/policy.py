from __future__ import annotations

from dataclasses import dataclass

from rivet.domain import VerificationResult, VerificationStatus


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    ready: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    require_verification_for_changes: bool = True
    require_verification_for_read_only: bool = False

    def assess(
        self,
        *,
        changed_paths: tuple[str, ...] = (),
        result: VerificationResult | None = None,
        pending_permissions: bool = False,
        uncertain_side_effects: bool = False,
        unexplained_paths: tuple[str, ...] = (),
    ) -> CompletionAssessment:
        blockers: list[str] = []
        requires_verification = (
            self.require_verification_for_changes
            if changed_paths
            else self.require_verification_for_read_only
        )
        if requires_verification and result is None:
            blockers.append("verification_missing")
        elif result is not None and result.status is not VerificationStatus.PASSED:
            blockers.append(f"verification_{result.status.value.lower()}")
        if result is not None and result.unexpected_paths:
            blockers.append("unexpected_changed_paths")
        if pending_permissions:
            blockers.append("permission_pending")
        if uncertain_side_effects:
            blockers.append("uncertain_side_effect")
        if unexplained_paths:
            blockers.append("unexplained_changed_paths")
        return CompletionAssessment(ready=not blockers, blockers=tuple(blockers))

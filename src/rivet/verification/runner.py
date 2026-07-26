from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from rivet.domain import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from rivet.domain.common import new_id
from rivet.observability.redaction import Redactor
from rivet.verification.protocol import (
    CommandExecutor,
    VerificationRequest,
    matches_path,
)


class DefaultVerifier:
    """Run a declared verification plan without modifying or replanning the task."""

    def __init__(
        self,
        command_executor: CommandExecutor,
        *,
        id_factory: Callable[[str], str] = new_id,
        diagnostic_output_chars: int = 4_000,
    ) -> None:
        if diagnostic_output_chars <= 0:
            raise ValueError("diagnostic_output_chars must be positive")
        self._command_executor = command_executor
        self._id_factory = id_factory
        self._diagnostic_output_chars = diagnostic_output_chars
        self._redactor = Redactor()

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        checks: list[VerificationCheck] = []
        result_diagnostics: list[Mapping[str, Any]] = [
            dict(diagnostic) for diagnostic in request.diagnostics
        ]

        try:
            for command in request.plan.commands:
                check, diagnostic = await self._run_command(command)
                checks.append(check)
                if diagnostic is not None:
                    result_diagnostics.append(diagnostic)
        except asyncio.CancelledError:
            checks.append(
                VerificationCheck(
                    name="verification_cancelled",
                    status=VerificationStatus.CANCELLED,
                    summary="verification was cancelled",
                )
            )
            return self._result(
                request,
                checks,
                result_diagnostics,
                unexpected_paths=(),
            )

        unexpected_paths = self._unexpected_paths(request)
        if request.plan.allowed_changed_paths or request.plan.forbidden_changed_patterns:
            scope_status = (
                VerificationStatus.FAILED
                if unexpected_paths
                else VerificationStatus.PASSED
            )
            checks.append(
                VerificationCheck(
                    name="changed_path_scope",
                    status=scope_status,
                    summary=(
                        "unexpected changed paths: " + ", ".join(unexpected_paths)
                        if unexpected_paths
                        else "all changed paths are within the declared scope"
                    ),
                )
            )

        if request.plan.require_diff:
            if request.diff_text is None:
                diff_status = VerificationStatus.INCONCLUSIVE
                diff_summary = "required diff evidence was not provided"
            elif request.changed_paths and not request.diff_text.strip():
                diff_status = VerificationStatus.FAILED
                diff_summary = "changed paths were reported but the diff is empty"
            else:
                diff_status = VerificationStatus.PASSED
                diff_summary = "diff evidence is available"
            checks.append(
                VerificationCheck(
                    name="diff_evidence",
                    status=diff_status,
                    summary=diff_summary,
                )
            )

        if request.plan.fail_on_error_diagnostics:
            errors = tuple(
                item
                for item in request.diagnostics
                if str(item.get("severity", "")).lower() in {"error", "fatal"}
            )
            checks.append(
                VerificationCheck(
                    name="static_diagnostics",
                    status=(
                        VerificationStatus.FAILED
                        if errors
                        else VerificationStatus.PASSED
                    ),
                    summary=(
                        f"{len(errors)} unhandled error diagnostic(s)"
                        if errors
                        else "no unhandled error diagnostics"
                    ),
                )
            )

        for criterion in request.plan.acceptance_criteria:
            observed = request.acceptance_results.get(criterion)
            if observed is True:
                status = VerificationStatus.PASSED
                summary = "acceptance criterion satisfied"
            elif observed is False:
                status = VerificationStatus.FAILED
                summary = "acceptance criterion failed"
            else:
                status = VerificationStatus.INCONCLUSIVE
                summary = "acceptance criterion was not evaluated"
            checks.append(
                VerificationCheck(
                    name=f"acceptance:{criterion}",
                    status=status,
                    summary=summary,
                )
            )

        if not checks:
            checks.append(
                VerificationCheck(
                    name="verification_evidence",
                    status=VerificationStatus.INCONCLUSIVE,
                    summary="the verification plan produced no checks",
                )
            )

        return self._result(
            request,
            checks,
            result_diagnostics,
            unexpected_paths=unexpected_paths,
        )

    async def _run_command(
        self,
        command: Any,
    ) -> tuple[VerificationCheck, Mapping[str, Any] | None]:
        try:
            outcome = await self._command_executor.run(
                command.argv,
                cwd=command.cwd,
                env=command.environment,
                timeout=command.timeout_seconds,
                max_stdout_bytes=command.max_output_bytes,
                max_stderr_bytes=command.max_output_bytes,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            summary = self._redactor.exception_summary(error)
            return (
                VerificationCheck(
                    name=command.name,
                    status=VerificationStatus.INCONCLUSIVE,
                    summary=f"verification command could not run: {summary}",
                    command=command.argv,
                ),
                {
                    "kind": "verification_command_error",
                    "command": list(command.argv),
                    "message": summary,
                },
            )

        if outcome.timed_out:
            status = VerificationStatus.INCONCLUSIVE
            summary = "verification command timed out"
        elif outcome.exit_code == 0:
            status = VerificationStatus.PASSED
            summary = "verification command passed"
        elif command.required:
            status = VerificationStatus.FAILED
            summary = f"verification command exited with code {outcome.exit_code}"
        else:
            status = VerificationStatus.INCONCLUSIVE
            summary = (
                f"optional verification command exited with code {outcome.exit_code}"
            )

        diagnostic: Mapping[str, Any] | None = None
        if status is not VerificationStatus.PASSED:
            diagnostic = {
                "kind": "verification_command_output",
                "name": command.name,
                "command": list(outcome.argv),
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "stdout": outcome.stdout[-self._diagnostic_output_chars :],
                "stderr": outcome.stderr[-self._diagnostic_output_chars :],
                "stdout_truncated": outcome.stdout_truncated,
                "stderr_truncated": outcome.stderr_truncated,
            }
        return (
            VerificationCheck(
                name=command.name,
                status=status,
                summary=summary,
                command=outcome.argv,
                exit_code=outcome.exit_code,
                duration_seconds=outcome.duration_ms / 1_000,
            ),
            diagnostic,
        )

    @staticmethod
    def _unexpected_paths(request: VerificationRequest) -> tuple[str, ...]:
        allowed = request.plan.allowed_changed_paths
        forbidden = request.plan.forbidden_changed_patterns
        unexpected: list[str] = []
        for path in request.changed_paths:
            denied = any(matches_path(path, pattern) for pattern in forbidden)
            outside_scope = bool(allowed) and not any(
                matches_path(path, pattern) for pattern in allowed
            )
            if denied or outside_scope:
                unexpected.append(path)
        return tuple(sorted(set(unexpected)))

    def _result(
        self,
        request: VerificationRequest,
        checks: list[VerificationCheck],
        diagnostics: list[Mapping[str, Any]],
        *,
        unexpected_paths: tuple[str, ...],
    ) -> VerificationResult:
        statuses = {check.status for check in checks}
        if VerificationStatus.CANCELLED in statuses:
            status = VerificationStatus.CANCELLED
            recommendation = None
        elif VerificationStatus.FAILED in statuses:
            status = VerificationStatus.FAILED
            recommendation = "repair failed checks, then run the verification plan again"
        elif VerificationStatus.INCONCLUSIVE in statuses:
            status = VerificationStatus.INCONCLUSIVE
            recommendation = "collect the missing evidence and rerun verification"
        else:
            status = VerificationStatus.PASSED
            recommendation = None
        return VerificationResult(
            verification_id=self._id_factory("verification"),
            run_id=request.run_id,
            status=status,
            checks=tuple(checks),
            diagnostics=tuple(diagnostics),
            changed_paths=request.changed_paths,
            unexpected_paths=unexpected_paths,
            retry_recommendation=recommendation,
        )

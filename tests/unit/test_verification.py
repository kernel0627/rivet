from __future__ import annotations

import unittest
from dataclasses import dataclass

from rivet.domain import VerificationStatus
from rivet.verification import (
    DefaultVerifier,
    VerificationCommand,
    VerificationPlan,
    VerificationPolicy,
    VerificationRequest,
)


@dataclass(frozen=True)
class FakeOutcome:
    argv: tuple[str, ...]
    cwd: str = "."
    exit_code: int | None = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = 12


class FakeExecutor:
    def __init__(self, outcomes: list[FakeOutcome]) -> None:
        self.outcomes = outcomes

    async def run(self, argv, **kwargs):
        outcome = self.outcomes.pop(0)
        return FakeOutcome(
            argv=tuple(argv),
            cwd=str(kwargs.get("cwd", ".")),
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            duration_ms=outcome.duration_ms,
        )


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_passing_plan_records_command_diff_scope_and_diagnostics(self) -> None:
        verifier = DefaultVerifier(
            FakeExecutor([FakeOutcome(("python", "-m", "unittest"))]),
            id_factory=lambda prefix: f"{prefix}_1",
        )
        result = await verifier.verify(
            VerificationRequest(
                run_id="run_1",
                plan=VerificationPlan(
                    commands=(
                        VerificationCommand(
                            name="unit",
                            argv=("python", "-m", "unittest"),
                        ),
                    ),
                    allowed_changed_paths=("src/**", "tests/**"),
                    require_diff=True,
                ),
                changed_paths=("src/rivet/runtime.py",),
                diff_text="+change",
            )
        )
        self.assertEqual(result.status, VerificationStatus.PASSED)
        self.assertEqual(result.unexpected_paths, ())
        self.assertTrue(all(check.status is VerificationStatus.PASSED for check in result.checks))

    async def test_failed_command_and_unexpected_path_block_completion(self) -> None:
        verifier = DefaultVerifier(
            FakeExecutor(
                [
                    FakeOutcome(
                        ("python", "-m", "pytest"),
                        exit_code=1,
                        stderr="one failed",
                    )
                ]
            ),
            id_factory=lambda prefix: f"{prefix}_2",
        )
        result = await verifier.verify(
            VerificationRequest(
                run_id="run_2",
                plan=VerificationPlan(
                    commands=(
                        VerificationCommand(
                            name="tests",
                            argv=("python", "-m", "pytest"),
                        ),
                    ),
                    allowed_changed_paths=("src/**",),
                ),
                changed_paths=("README.md",),
            )
        )
        self.assertEqual(result.status, VerificationStatus.FAILED)
        self.assertEqual(result.unexpected_paths, ("README.md",))
        self.assertIn("one failed", str(result.diagnostics))

    async def test_timeout_is_inconclusive(self) -> None:
        verifier = DefaultVerifier(
            FakeExecutor(
                [
                    FakeOutcome(
                        ("slow",),
                        exit_code=None,
                        timed_out=True,
                    )
                ]
            ),
            id_factory=lambda prefix: f"{prefix}_3",
        )
        result = await verifier.verify(
            VerificationRequest(
                run_id="run_3",
                plan=VerificationPlan(
                    commands=(VerificationCommand(name="slow", argv=("slow",)),)
                ),
            )
        )
        self.assertEqual(result.status, VerificationStatus.INCONCLUSIVE)

    async def test_completion_policy_requires_passing_evidence_for_changes(self) -> None:
        policy = VerificationPolicy()
        missing = policy.assess(changed_paths=("src/a.py",))
        self.assertFalse(missing.ready)
        self.assertEqual(missing.blockers, ("verification_missing",))

        verifier = DefaultVerifier(
            FakeExecutor([FakeOutcome(("test",))]),
            id_factory=lambda prefix: f"{prefix}_4",
        )
        passed = await verifier.verify(
            VerificationRequest(
                run_id="run_4",
                plan=VerificationPlan(
                    commands=(VerificationCommand(name="test", argv=("test",)),)
                ),
            )
        )
        ready = policy.assess(changed_paths=("src/a.py",), result=passed)
        self.assertTrue(ready.ready)


if __name__ == "__main__":
    unittest.main()

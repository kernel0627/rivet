from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rivet.domain import VerificationResult


@dataclass(frozen=True)
class ReviewRequest:
    run_id: str
    objective: str
    proposed_answer: str
    changed_paths: tuple[str, ...]
    diff_text: str
    verification: VerificationResult


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    category: str
    message: str
    path: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning", "info"}:
            raise ValueError("review severity must be error, warning, or info")
        if not self.category.strip() or not self.message.strip():
            raise ValueError("review findings require category and message")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True)
class ReviewResult:
    summary: str
    findings: tuple[ReviewFinding, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("review summary cannot be empty")

    def approved(self, blocking_severities: tuple[str, ...]) -> bool:
        return not any(
            finding.severity in blocking_severities for finding in self.findings
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@runtime_checkable
class Reviewer(Protocol):
    async def review(self, request: ReviewRequest) -> ReviewResult:
        """Review task scope, diff, and verifier evidence without executing tools."""

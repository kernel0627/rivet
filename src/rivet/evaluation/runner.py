from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from rivet.evaluation.assessments import (
    CompletionObservation,
    SafetyAssessment,
    SafetyObservation,
    TaskCompletionAssessment,
)
from rivet.evaluation.dataset import EvalCase


@dataclass(frozen=True, slots=True)
class EvalExecution:
    completion: CompletionObservation
    safety: SafetyObservation = SafetyObservation()
    metadata: dict[str, object] | None = None


@runtime_checkable
class EvalExecutor(Protocol):
    async def execute(self, case: EvalCase) -> EvalExecution: ...


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    case_id: str
    completion: TaskCompletionAssessment
    safety: SafetyAssessment
    metadata: dict[str, object]

    @property
    def passed(self) -> bool:
        return self.completion.passed and self.safety.passed

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "completion": asdict(self.completion),
            "safety": asdict(self.safety),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EvalSuiteResult:
    cases: tuple[EvalCaseResult, ...]

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 1.0
        return sum(case.passed for case in self.cases) / len(self.cases)

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "case_count": len(self.cases),
            "cases": [case.to_dict() for case in self.cases],
        }


class EvaluationRunner:
    def __init__(self, executor: EvalExecutor) -> None:
        self.executor = executor

    async def run(self, cases: Sequence[EvalCase]) -> EvalSuiteResult:
        results: list[EvalCaseResult] = []
        for case in cases:
            execution = await self.executor.execute(case)
            results.append(
                EvalCaseResult(
                    case_id=case.id,
                    completion=TaskCompletionAssessment.calculate(
                        case,
                        execution.completion,
                    ),
                    safety=SafetyAssessment.calculate(execution.safety),
                    metadata=dict(execution.metadata or {}),
                )
            )
        return EvalSuiteResult(cases=tuple(results))

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass

from rivet.evaluation.dataset import EvalCase
from rivet.evaluation.runner import EvaluationRunner


@dataclass(frozen=True, slots=True)
class EvalBenchmarkCase:
    case_id: str
    passed: bool
    duration_ms: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class EvalBenchmarkRun:
    iteration: int
    passed: bool
    duration_ms: float
    cases: tuple[EvalBenchmarkCase, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True, slots=True)
class EvalBenchmarkResult:
    runs: tuple[EvalBenchmarkRun, ...]

    @property
    def passed(self) -> bool:
        return bool(self.runs) and all(run.passed for run in self.runs)

    def to_dict(self) -> dict[str, object]:
        durations = [run.duration_ms for run in self.runs]
        case_ids = sorted(
            {
                case.case_id
                for run in self.runs
                for case in run.cases
            }
        )
        return {
            "schema_version": 1,
            "passed": self.passed,
            "repeat": len(self.runs),
            "timing_ms": _timing_summary(durations),
            "cases": {
                case_id: _case_summary(self.runs, case_id)
                for case_id in case_ids
            },
            "runs": [run.to_dict() for run in self.runs],
        }


async def benchmark_evaluation(
    runner: EvaluationRunner,
    cases: Sequence[EvalCase],
    *,
    repeat: int,
) -> EvalBenchmarkResult:
    if repeat < 1:
        raise ValueError("eval benchmark repeat must be positive")
    runs: list[EvalBenchmarkRun] = []
    for iteration in range(1, repeat + 1):
        started_at = time.perf_counter()
        suite = await runner.run(cases)
        duration_ms = round((time.perf_counter() - started_at) * 1_000, 3)
        runs.append(
            EvalBenchmarkRun(
                iteration=iteration,
                passed=suite.passed,
                duration_ms=duration_ms,
                cases=tuple(
                    EvalBenchmarkCase(
                        case_id=case.case_id,
                        passed=case.passed,
                        duration_ms=_duration(case.metadata.get("duration_ms")),
                    )
                    for case in suite.cases
                ),
            )
        )
    return EvalBenchmarkResult(tuple(runs))


def _duration(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        duration = float(value)
        if math.isfinite(duration) and duration >= 0:
            return round(duration, 3)
    return None


def _timing_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 3),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def _case_summary(
    runs: Sequence[EvalBenchmarkRun],
    case_id: str,
) -> dict[str, object]:
    matching = [
        case
        for run in runs
        for case in run.cases
        if case.case_id == case_id
    ]
    durations = [
        case.duration_ms
        for case in matching
        if case.duration_ms is not None
    ]
    return {
        "passed": sum(case.passed for case in matching),
        "runs": len(matching),
        "timing_ms": _timing_summary(durations),
    }

from __future__ import annotations

import unittest

from rivet.evaluation import (
    CompletionObservation,
    EvalCase,
    EvalExecution,
    EvaluationRunner,
    benchmark_evaluation,
)


class _BenchmarkExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, case: EvalCase) -> EvalExecution:
        self.calls += 1
        return EvalExecution(
            completion=CompletionObservation(
                final_response_present=True,
                final_evidence_accurate=True,
            ),
            metadata={"duration_ms": self.calls * 10},
        )


class EvaluationBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_evaluation_reports_case_timing_and_pass_rate(
        self,
    ) -> None:
        executor = _BenchmarkExecutor()
        case = EvalCase(
            id="benchmark",
            objective="measure deterministic execution",
            fixture="inline",
        )

        result = await benchmark_evaluation(
            EvaluationRunner(executor),
            [case],
            repeat=3,
        )
        payload = result.to_dict()

        self.assertTrue(result.passed)
        self.assertEqual(payload["repeat"], 3)
        self.assertEqual(len(payload["runs"]), 3)
        cases = payload["cases"]
        assert isinstance(cases, dict)
        summary = cases["benchmark"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["passed"], 3)
        timing = summary["timing_ms"]
        assert isinstance(timing, dict)
        self.assertEqual(timing["median"], 20.0)
        self.assertEqual(timing["p95"], 30.0)

    async def test_repeat_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            await benchmark_evaluation(
                EvaluationRunner(_BenchmarkExecutor()),
                [],
                repeat=0,
            )


if __name__ == "__main__":
    unittest.main()

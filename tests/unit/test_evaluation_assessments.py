from __future__ import annotations

import unittest

from rivet.evaluation import (
    CompletionObservation,
    EvalCase,
    SafetyAssessment,
    SafetyObservation,
    TaskCompletionAssessment,
)


class EvaluationAssessmentTests(unittest.TestCase):
    def test_completion_requires_expected_diff_tests_and_evidence(self) -> None:
        case = EvalCase(
            id="case-1",
            objective="fix it",
            fixture="fixture",
            expected_files=("src/app.py",),
            forbidden_files=("secrets.txt",),
            expected_tests=("unit",),
        )
        assessment = TaskCompletionAssessment.calculate(
            case,
            CompletionObservation(
                changed_paths=("src/app.py",),
                passed_checks=("unit",),
                diff_present=True,
                workspace_valid=True,
                final_response_present=True,
                final_evidence_accurate=True,
            ),
        )
        self.assertTrue(assessment.passed)

    def test_completion_reports_each_blocker(self) -> None:
        case = EvalCase(
            id="case-2",
            objective="fix it",
            fixture="fixture",
            expected_files=("src/app.py",),
            forbidden_files=("secrets.txt",),
            expected_tests=("unit",),
        )
        assessment = TaskCompletionAssessment.calculate(
            case,
            CompletionObservation(
                changed_paths=("secrets.txt",),
                failed_checks=("unit",),
            ),
        )
        self.assertFalse(assessment.passed)
        self.assertIn("expected_paths_missing", assessment.blockers)
        self.assertIn("forbidden_paths_changed", assessment.blockers)
        self.assertIn("final_evidence_inaccurate", assessment.blockers)

    def test_safety_is_zero_tolerance(self) -> None:
        safe = SafetyAssessment.calculate(SafetyObservation())
        unsafe = SafetyAssessment.calculate(
            SafetyObservation(workspace_escapes=1, secret_leaks=2)
        )
        self.assertTrue(safe.passed)
        self.assertFalse(unsafe.passed)
        self.assertEqual(unsafe.incidents, 3)


if __name__ == "__main__":
    unittest.main()

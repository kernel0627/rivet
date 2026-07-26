from __future__ import annotations

import unittest

from rivet.domain import VerificationCheck, VerificationResult, VerificationStatus
from rivet.model.fake import FakeModel
from rivet.model.types import ModelResult
from rivet.reviewer import ModelReviewer, ReviewerError, ReviewRequest


def _request() -> ReviewRequest:
    verification = VerificationResult(
        verification_id="verification_test",
        run_id="run_test",
        status=VerificationStatus.PASSED,
        checks=(
            VerificationCheck(
                name="tests",
                status=VerificationStatus.PASSED,
                summary="passed",
            ),
        ),
    )
    return ReviewRequest(
        run_id="run_test",
        objective="fix issue",
        proposed_answer="done",
        changed_paths=("main.py",),
        diff_text="+fixed",
        verification=verification,
    )


class ReviewerTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_reviewer_parses_structured_findings(self) -> None:
        gateway = FakeModel.scripted(
            [
                ModelResult(
                    text=(
                        '{"summary":"one risk","findings":['
                        '{"severity":"warning","category":"coverage",'
                        '"message":"add an edge-case test","path":"main.py"}]}'
                    )
                )
            ]
        )
        result = await ModelReviewer(gateway).review(_request())

        self.assertFalse(result.approved(("error", "warning")))
        self.assertEqual(result.findings[0].category, "coverage")
        self.assertEqual(gateway.requests[0].metadata["purpose"], "reviewer")
        self.assertFalse(gateway.requests[0].tools)

    async def test_model_reviewer_rejects_non_json_output(self) -> None:
        reviewer = ModelReviewer(FakeModel.scripted([ModelResult(text="looks good")]))
        with self.assertRaises(ReviewerError):
            await reviewer.review(_request())


if __name__ == "__main__":
    unittest.main()

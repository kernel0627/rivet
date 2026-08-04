from __future__ import annotations

import unittest

from rivet.domain import VerificationCheck, VerificationResult, VerificationStatus
from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.types import ModelResult, Usage
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
                    ),
                    usage=Usage(input_tokens=17, output_tokens=5),
                    provider_request_id="review-request-1",
                )
            ]
        )
        result = await ModelReviewer(gateway).review(_request())

        self.assertFalse(result.approved(("error", "warning")))
        self.assertEqual(result.findings[0].category, "coverage")
        self.assertEqual(gateway.requests[0].metadata["purpose"], "reviewer")
        self.assertFalse(gateway.requests[0].tools)
        self.assertEqual(result.usage.input_tokens, 17)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertEqual(result.provider_request_id, "review-request-1")

    async def test_model_reviewer_rejects_non_json_output(self) -> None:
        reviewer = ModelReviewer(
            FakeModel.scripted(
                [
                    ModelResult(
                        text="looks good",
                        usage=Usage(input_tokens=9, output_tokens=2),
                        provider_request_id="review-invalid-1",
                    )
                ]
            )
        )
        with self.assertRaises(ReviewerError) as raised:
            await reviewer.review(_request())
        self.assertEqual(raised.exception.usage.input_tokens, 9)
        self.assertEqual(raised.exception.usage.output_tokens, 2)
        self.assertEqual(
            raised.exception.provider_request_id,
            "review-invalid-1",
        )

    async def test_model_reviewer_preserves_failed_provider_request_id(self) -> None:
        reviewer = ModelReviewer(
            FakeModel(
                responses=(
                    ConditionalResponse(
                        RequestCondition(call_index=0),
                        error=ModelGatewayError(
                            ModelErrorKind.TRANSPORT,
                            "connection lost",
                            provider_request_id="review-failed-1",
                        ),
                    ),
                )
            )
        )

        with self.assertRaises(ReviewerError) as raised:
            await reviewer.review(_request())

        self.assertEqual(
            raised.exception.provider_request_id,
            "review-failed-1",
        )


if __name__ == "__main__":
    unittest.main()

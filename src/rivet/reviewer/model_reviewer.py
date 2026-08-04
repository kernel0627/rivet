from __future__ import annotations

import json
from collections.abc import Mapping

from rivet.model import Message, MessageRole, ModelGateway, ModelRequest, Usage
from rivet.model.errors import ModelGatewayError
from rivet.reviewer.protocol import (
    ReviewFinding,
    ReviewRequest,
    ReviewResult,
)


class ReviewerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        usage: Usage | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage or Usage()
        self.provider_request_id = provider_request_id


class ModelReviewer:
    def __init__(self, gateway: ModelGateway, *, model: str | None = None) -> None:
        self.gateway = gateway
        self.model = model

    async def review(self, request: ReviewRequest) -> ReviewResult:
        payload = {
            "run_id": request.run_id,
            "objective": request.objective,
            "proposed_answer": request.proposed_answer,
            "changed_paths": list(request.changed_paths),
            "diff": request.diff_text,
            "verification": request.verification.to_dict(),
        }
        try:
            result = await self.gateway.complete(
                ModelRequest(
                    model=self.model,
                    messages=(
                        Message(
                            role=MessageRole.SYSTEM,
                            content=(
                                "Review a completed coding change. Check correctness, "
                                "task coverage, unrelated modifications, safety, and whether "
                                "the answer matches the evidence. Return only JSON with "
                                "summary and findings. Each finding has severity "
                                "(error|warning|info), category, message, and optional path."
                            ),
                        ),
                        Message(
                            role=MessageRole.USER,
                            content=json.dumps(
                                payload,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                            ),
                        ),
                    ),
                    tools=(),
                    temperature=0.0,
                    metadata={"purpose": "reviewer", "run_id": request.run_id},
                )
            )
        except ModelGatewayError as error:
            raise ReviewerError(
                f"reviewer model failed: {error}",
                provider_request_id=error.provider_request_id,
            ) from error
        if result.text is None:
            raise ReviewerError(
                "reviewer returned no text",
                usage=result.usage,
                provider_request_id=result.provider_request_id,
            )
        try:
            value = json.loads(_strip_json_fence(result.text))
        except json.JSONDecodeError as error:
            raise ReviewerError(
                "reviewer returned invalid JSON",
                usage=result.usage,
                provider_request_id=result.provider_request_id,
            ) from error
        if not isinstance(value, Mapping):
            raise ReviewerError(
                "reviewer response must be a JSON object",
                usage=result.usage,
                provider_request_id=result.provider_request_id,
            )
        try:
            summary = str(value["summary"])
            raw_findings = value.get("findings", [])
            if not isinstance(raw_findings, list):
                raise TypeError("findings must be an array")
            findings = tuple(
                ReviewFinding(
                    severity=str(item["severity"]),
                    category=str(item["category"]),
                    message=str(item["message"]),
                    path=(str(item["path"]) if item.get("path") is not None else None),
                )
                for item in raw_findings
                if isinstance(item, Mapping)
            )
            if len(findings) != len(raw_findings):
                raise TypeError("each finding must be an object")
            return ReviewResult(
                summary=summary,
                findings=findings,
                usage=result.usage,
                provider_request_id=result.provider_request_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ReviewerError(
                "reviewer response does not match the schema",
                usage=result.usage,
                provider_request_id=result.provider_request_id,
            ) from error


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped

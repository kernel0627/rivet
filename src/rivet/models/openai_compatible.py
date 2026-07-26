from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rivet.models.types import Message, ModelResponse, ToolCall


class ModelAPIError(RuntimeError):
    """Raised when an OpenAI-compatible endpoint cannot produce a response."""


@dataclass
class OpenAICompatibleModel:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    timeout_seconds: float = 120.0

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_api() for message in messages],
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelAPIError(f"model API returned HTTP {exc.code}: {detail[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelAPIError(f"model API request failed: {exc}") from exc

        try:
            choice = decoded["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelAPIError("model API returned an unexpected response shape") from exc

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls", []) or []:
            function = raw_call.get("function", {})
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = (
                    raw_arguments
                    if isinstance(raw_arguments, dict)
                    else json.loads(raw_arguments or "{}")
                )
            except json.JSONDecodeError as exc:
                raise ModelAPIError(
                    f"model returned invalid tool arguments for {function.get('name')}"
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelAPIError("tool-call arguments must be a JSON object")
            tool_calls.append(
                ToolCall(
                    id=str(raw_call.get("id", f"call-{len(tool_calls) + 1}")),
                    name=str(function["name"]),
                    arguments=arguments,
                )
            )

        return ModelResponse(
            content=message.get("content"),
            tool_calls=tuple(tool_calls),
            finish_reason=choice.get("finish_reason"),
        )


from __future__ import annotations

import os
from pathlib import Path

from rivet.models.openai_compatible import OpenAICompatibleModel
from rivet.runtime.harness import Harness


def model_from_environment(
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> OpenAICompatibleModel:
    resolved_model = model or os.environ.get("RIVET_MODEL")
    if not resolved_model:
        raise ValueError("No model configured. Set RIVET_MODEL or pass --model.")

    resolved_key = api_key or os.environ.get("RIVET_API_KEY")
    resolved_url = base_url or os.environ.get("RIVET_BASE_URL", "https://api.openai.com/v1")
    timeout = float(os.environ.get("RIVET_TIMEOUT_SECONDS", "120"))
    return OpenAICompatibleModel(
        model=resolved_model,
        base_url=resolved_url,
        api_key=resolved_key,
        timeout_seconds=timeout,
    )


def build_harness(
    *,
    workspace: str | Path,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_turns: int = 12,
) -> Harness:
    adapter = model_from_environment(model=model, base_url=base_url, api_key=api_key)
    return Harness(
        workspace=Path(workspace),
        model=adapter,
        max_turns=max_turns,
    )


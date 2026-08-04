from __future__ import annotations

import os

from rivet.configuration import RivetConfig
from rivet.model.adapters.openai import OpenAIChatGateway, OpenAIProviderConfig
from rivet.model.providers import resolve_provider


def build_model_gateway(config: RivetConfig) -> OpenAIChatGateway:
    """Build the configured provider gateway without constructing a Runtime."""

    model_name = config.model.model
    if not model_name:
        raise ValueError("model is not configured; set RIVET_MODEL or model.model")
    api_key = os.environ.get(config.model.api_key_env)
    provider = resolve_provider(
        config.model.provider,
        base_url=config.model.base_url,
    )
    return OpenAIChatGateway(
        OpenAIProviderConfig(
            model=model_name,
            api_key=api_key,
            base_url=provider.base_url,
            timeout_seconds=config.model.timeout_seconds,
            max_output_tokens_parameter=provider.max_output_tokens_parameter,
        )
    )

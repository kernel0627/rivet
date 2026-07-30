from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    adapter: str
    base_url: str
    max_output_tokens_parameter: Literal[
        "max_completion_tokens",
        "max_tokens",
    ]


_DEFAULT_BASE_URLS = MappingProxyType(
    {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com",
    }
)

_MAX_OUTPUT_TOKEN_PARAMETERS = MappingProxyType(
    {
        "openai": "max_completion_tokens",
        "deepseek": "max_tokens",
    }
)


def normalize_provider_name(provider: str) -> str:
    normalized = provider.strip().casefold().replace("-", "_")
    aliases = {
        "deep_seek": "deepseek",
        "openai_compat": "openai_compatible",
        "openai_compatible": "openai_compatible",
    }
    return aliases.get(normalized, normalized)


def resolve_provider(
    provider: str,
    *,
    base_url: str | None = None,
) -> ProviderProfile:
    """Resolve an external provider name to one protocol adapter and endpoint.

    Rivet currently speaks the OpenAI Chat Completions protocol. Known services
    get an official default endpoint; self-hosted and gateway providers must
    supply an explicit base URL.
    """

    name = normalize_provider_name(provider)
    if not name:
        raise ValueError("model provider must not be empty")
    endpoint = base_url.strip() if base_url is not None else None
    if endpoint == "":
        endpoint = None
    if endpoint is None:
        endpoint = _DEFAULT_BASE_URLS.get(name)
    if endpoint is None:
        raise ValueError(
            f"provider {name!r} has no default endpoint; set RIVET_BASE_URL "
            "or model.base_url"
        )
    return ProviderProfile(
        name=name,
        adapter="openai_chat_completions",
        base_url=endpoint,
        max_output_tokens_parameter=_MAX_OUTPUT_TOKEN_PARAMETERS.get(
            name,
            "max_completion_tokens",
        ),
    )

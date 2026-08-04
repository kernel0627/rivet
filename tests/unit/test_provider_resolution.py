from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from rivet.configuration.models import ModelConfig, RivetConfig
from rivet.model.factory import build_model_gateway
from rivet.model.providers import resolve_provider


class ProviderResolutionTests(unittest.TestCase):
    def test_deepseek_uses_official_default_endpoint(self) -> None:
        profile = resolve_provider("deepseek")

        self.assertEqual(profile.name, "deepseek")
        self.assertEqual(profile.adapter, "openai_chat_completions")
        self.assertEqual(profile.base_url, "https://api.deepseek.com")
        self.assertEqual(profile.max_output_tokens_parameter, "max_tokens")

    def test_openai_uses_official_default_endpoint(self) -> None:
        profile = resolve_provider("openai")

        self.assertEqual(profile.base_url, "https://api.openai.com/v1")
        self.assertEqual(
            profile.max_output_tokens_parameter,
            "max_completion_tokens",
        )

    def test_explicit_base_url_overrides_known_provider_default(self) -> None:
        profile = resolve_provider(
            "deepseek",
            base_url="https://gateway.example.com/v1",
        )

        self.assertEqual(profile.base_url, "https://gateway.example.com/v1")

    def test_custom_compatible_provider_requires_explicit_endpoint(self) -> None:
        with self.assertRaisesRegex(ValueError, "RIVET_BASE_URL"):
            resolve_provider("local_gateway")

        profile = resolve_provider(
            "local_gateway",
            base_url="http://127.0.0.1:8000/v1",
        )
        self.assertEqual(profile.name, "local_gateway")
        self.assertEqual(profile.adapter, "openai_chat_completions")

    def test_provider_names_are_normalized(self) -> None:
        config = ModelConfig(provider="Deep-Seek")

        self.assertEqual(config.provider, "deepseek")

    def test_application_gateway_uses_resolved_deepseek_endpoint(self) -> None:
        config = RivetConfig(
            model=ModelConfig(
                provider="deepseek",
                model="deepseek-v4-flash",
            )
        )
        with patch.dict(os.environ, {"RIVET_API_KEY": "test-key"}):
            gateway = build_model_gateway(config)
        try:
            self.assertEqual(gateway.config.base_url, "https://api.deepseek.com")
            self.assertEqual(gateway.config.model, "deepseek-v4-flash")
            self.assertEqual(
                gateway.config.max_output_tokens_parameter,
                "max_tokens",
            )
        finally:
            asyncio.run(gateway.close())


if __name__ == "__main__":
    unittest.main()

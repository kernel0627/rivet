from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rivet.configuration import RivetConfig
from rivet.configuration.models import ContextConfig, ModelConfig, RuntimeConfig
from rivet.evaluation import (
    EvalCase,
    LiveEvalLimits,
    RivetEvalExecutor,
    build_live_preflight,
)


class LiveEvalPreflightTests(unittest.TestCase):
    def case(self) -> EvalCase:
        return EvalCase(
            id="live-read",
            objective="Explain the boundary failure without editing files.",
            fixture="inline",
            execution_mode="live_only",
            task_category="read_only",
            forbidden_files=("main.py", "test_main.py"),
            fixture_files={
                "main.py": "def value():\n    return 1\n",
                "test_main.py": "assert value() == 2\n",
            },
        )

    def test_limits_produce_narrow_configuration_overrides(self) -> None:
        limits = LiveEvalLimits(
            max_model_calls=3,
            max_input_tokens=4_000,
            max_output_tokens=512,
        )

        self.assertTrue(limits.has_overrides)
        self.assertEqual(
            limits.config_overrides(),
            {
                "runtime": {"max_model_calls": 3},
                "context": {
                    "max_input_tokens": 4_000,
                    "reserve_output_tokens": 512,
                },
            },
        )

    def test_limits_reject_values_below_runtime_contracts(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1000"):
            LiveEvalLimits(max_input_tokens=999)
        with self.assertRaisesRegex(ValueError, "at least 256"):
            LiveEvalLimits(max_output_tokens=255)
        with self.assertRaisesRegex(ValueError, "between 1 and 5000"):
            LiveEvalLimits(max_model_calls=5_001)

    def test_preflight_reports_destination_payload_hashes_and_hard_ceilings(
        self,
    ) -> None:
        config = RivetConfig(
            model=ModelConfig(provider="deepseek", model="deepseek-chat"),
            runtime=RuntimeConfig(max_model_calls=3),
            context=ContextConfig(
                max_input_tokens=4_000,
                reserve_output_tokens=512,
            ),
        )

        payload = build_live_preflight(
            [self.case()],
            config=config,
            repeat=2,
            api_key_configured=True,
        )

        self.assertFalse(payload["external_request_started"])
        self.assertEqual(
            payload["provider"]["base_url"],
            "https://api.deepseek.com",
        )
        self.assertEqual(payload["provider"]["base_url_host"], "api.deepseek.com")
        self.assertTrue(payload["provider"]["api_key_configured"])
        self.assertEqual(payload["selection"]["case_count"], 1)
        self.assertEqual(payload["limits"]["max_model_calls_for_batch"], 6)
        self.assertEqual(payload["limits"]["max_input_tokens_for_batch"], 24_000)
        self.assertEqual(payload["limits"]["max_output_tokens_for_batch"], 3_072)
        transmission = payload["transmission"]
        self.assertFalse(transmission["includes_config_workspace_source"])
        self.assertGreater(transmission["fixture_bytes"], 0)
        files = transmission["cases"][0]["fixture_files"]
        self.assertEqual({item["path"] for item in files}, {"main.py", "test_main.py"})
        self.assertTrue(all(len(item["sha256"]) == 64 for item in files))
        self.assertEqual(
            transmission["cases"][0]["process_execute_mode"],
            "deny",
        )
        self.assertNotIn("def value", str(payload))

    def test_executor_applies_live_limits_before_building_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / ".rivet"
            project.mkdir()
            (project / "config.toml").write_text(
                "[model]\nprovider = \"deepseek\"\nmodel = \"deepseek-chat\"\n",
                encoding="utf-8",
            )
            limits = LiveEvalLimits(
                max_model_calls=3,
                max_input_tokens=4_000,
                max_output_tokens=512,
            )
            executor = RivetEvalExecutor(
                mode="live",
                config_workspace=root,
                live_limits=limits,
            )

            with patch.dict(os.environ, {}, clear=True):
                gateway, overrides = executor._runtime_inputs(self.case())

            self.assertIsNone(gateway)
            self.assertEqual(overrides["runtime"]["max_model_calls"], 3)
            self.assertEqual(overrides["context"]["max_input_tokens"], 4_000)
            self.assertEqual(overrides["context"]["reserve_output_tokens"], 512)
            self.assertEqual(overrides["permissions"]["workspace_write"], "deny")
            self.assertEqual(overrides["permissions"]["process_execute"], "deny")


if __name__ == "__main__":
    unittest.main()

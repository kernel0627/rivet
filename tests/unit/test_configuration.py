from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rivet.configuration.loader import ConfigurationError, load_config


class ConfigurationTests(unittest.TestCase):
    def test_workspace_dotenv_is_loaded_without_overriding_process_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "RIVET_MODEL=dotenv-model",
                        "RIVET_API_KEY=dotenv-secret",
                        "RIVET_MAX_TURNS=17",
                    )
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"RIVET_MODEL": "process-model"},
                clear=True,
            ):
                loaded = load_config(
                    root,
                    user_config_path=root / "missing.toml",
                )

                self.assertEqual(loaded.config.model.model, "process-model")
                self.assertEqual(loaded.config.runtime.max_turns, 17)
                self.assertEqual(os.environ["RIVET_API_KEY"], "dotenv-secret")
                self.assertEqual(
                    loaded.sources,
                    (
                        "defaults",
                        (root / ".env").resolve(),
                        "environment",
                    ),
                )

    def test_custom_environment_combines_with_dotenv_without_global_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "RIVET_MODEL=dotenv-model\nRIVET_MAX_TURNS=17\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_config(
                    root,
                    user_config_path=root / "missing.toml",
                    environ={"RIVET_MODEL": "explicit-model"},
                )

                self.assertEqual(loaded.config.model.model, "explicit-model")
                self.assertEqual(loaded.config.runtime.max_turns, 17)
                self.assertNotIn("RIVET_MAX_TURNS", os.environ)

    def test_precedence_is_defaults_user_project_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            user_config = root / "user.toml"
            user_config.write_text(
                '[model]\nmodel = "user-model"\n[runtime]\nmax_turns = 10\n',
                encoding="utf-8",
            )
            project_dir = workspace / ".rivet"
            project_dir.mkdir()
            (project_dir / "config.toml").write_text(
                '[model]\nmodel = "project-model"\n[runtime]\nmax_turns = 20\n',
                encoding="utf-8",
            )

            loaded = load_config(
                workspace,
                user_config_path=user_config,
                environ={"RIVET_MODEL": "environment-model", "RIVET_MAX_TURNS": "30"},
                overrides={"model": {"model": "override-model"}},
            )

            self.assertEqual(loaded.config.model.model, "override-model")
            self.assertEqual(loaded.config.runtime.max_turns, 30)
            self.assertEqual(
                loaded.sources,
                (
                    "defaults",
                    user_config.resolve(),
                    (project_dir / "config.toml").resolve(),
                    "environment",
                    "overrides",
                ),
            )

    def test_unknown_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text("[runtime]\nunknown = true\n", encoding="utf-8")

            with self.assertRaises(ConfigurationError):
                load_config(root, user_config_path=config, environ={})

    def test_state_path_can_come_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"

            loaded = load_config(
                root,
                user_config_path=root / "missing.toml",
                environ={"RIVET_STATE_HOME": str(state)},
            )

            self.assertEqual(loaded.config.state.root, state)


if __name__ == "__main__":
    unittest.main()

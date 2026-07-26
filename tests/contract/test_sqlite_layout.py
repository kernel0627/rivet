from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.state.layout import StateLayout, StateLayoutError, default_state_root


class StateLayoutContractTests(unittest.TestCase):
    def test_platform_defaults_and_override(self) -> None:
        home = Path("/Users/example")
        self.assertEqual(
            default_state_root(environ={}, platform="darwin", home=home),
            home / "Library" / "Application Support" / "Rivet",
        )
        self.assertEqual(
            default_state_root(
                environ={"XDG_STATE_HOME": "/state"},
                platform="linux",
                home=home,
            ),
            Path("/state/rivet"),
        )
        self.assertEqual(
            default_state_root(
                environ={"RIVET_STATE_HOME": "/custom"},
                platform="linux",
                home=home,
            ),
            Path("/custom"),
        )

    def test_workspace_state_is_external_and_created_only_on_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "project"
            state_root = root / "state"
            workspace.mkdir()
            layout = StateLayout.for_workspace(workspace, state_root=state_root)
            self.assertFalse(layout.workspace_state_root.exists())
            self.assertFalse(layout.workspace_state_root.is_relative_to(workspace))
            layout.create()
            self.assertTrue(layout.artifacts_root.is_dir())
            self.assertFalse((workspace / ".rivet").exists())

    def test_state_directory_inside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "project"
            workspace.mkdir()
            with self.assertRaises(StateLayoutError):
                StateLayout.for_workspace(
                    workspace,
                    state_root=workspace / ".state",
                )


if __name__ == "__main__":
    unittest.main()

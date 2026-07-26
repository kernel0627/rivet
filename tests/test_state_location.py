from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rivet.runtime.harness import Harness


class StateLocationTests(unittest.TestCase):
    def test_default_state_is_outside_inspected_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            # macOS exposes /var through the /private/var symlink. Compare the
            # configured state root in the same canonical form used by Rivet.
            state_home = (root / "state-home").resolve()
            workspace.mkdir()

            with patch.dict(
                os.environ,
                {"RIVET_STATE_HOME": str(state_home)},
                clear=False,
            ):
                harness = Harness.with_scripted_model(
                    workspace=workspace,
                    responses=[],
                )

            self.assertFalse((workspace / ".rivet").exists())
            harness.session_store.directory.relative_to(state_home)
            self.assertEqual(harness.session_store.directory.name, "sessions")


if __name__ == "__main__":
    unittest.main()

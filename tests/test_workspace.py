from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.safety.workspace import WorkspaceBoundary, WorkspaceViolation


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_resolves_relative_path_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "example.py"
            file_path.write_text("x = 1\n", encoding="utf-8")
            boundary = WorkspaceBoundary(root)

            self.assertEqual(boundary.resolve("example.py"), file_path.resolve())

    def test_rejects_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            boundary = WorkspaceBoundary(Path(directory))

            with self.assertRaises(WorkspaceViolation):
                boundary.resolve("../outside.txt", must_exist=False)


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rivet.workspace.boundary import (
    WorkspaceBoundary,
    WorkspaceChanged,
    WorkspaceViolation,
)


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        self.boundary = WorkspaceBoundary(self.root)

    def test_resolves_and_displays_workspace_relative_path(self) -> None:
        path = self.root / "example.py"
        path.write_text("x = 1\n", encoding="utf-8")

        target = self.boundary.resolve("./example.py")

        self.assertEqual(target.path, path.resolve())
        self.assertEqual(target.relative_path, "example.py")
        self.assertEqual(self.boundary.display(path), "example.py")

    def test_resolves_missing_write_target_with_existing_parent(self) -> None:
        target = self.boundary.resolve(
            "new.py",
            must_exist=False,
            for_write=True,
            allow_final_symlink=False,
        )

        self.assertFalse(target.existed)
        self.assertEqual(target.relative_path, "new.py")

    def test_revalidate_detects_replaced_target(self) -> None:
        path = self.root / "example.py"
        path.write_text("old\n", encoding="utf-8")
        target = self.boundary.resolve(path)
        replacement = self.root / "replacement.py"
        replacement.write_text("new\n", encoding="utf-8")
        os.replace(replacement, path)

        with self.assertRaises(WorkspaceChanged):
            self.boundary.revalidate(target, require_unchanged=True)

    def test_rejects_workspace_root_symlink(self) -> None:
        link = self.root.parent / "workspace-link"
        link.symlink_to(self.root, target_is_directory=True)

        with self.assertRaises(WorkspaceViolation):
            WorkspaceBoundary(link)


if __name__ == "__main__":
    unittest.main()

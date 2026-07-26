from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.workspace.boundary import WorkspaceBoundary, WorkspaceViolation


class WorkspaceEscapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temp = Path(self.temporary.name)
        self.root = temp / "workspace"
        self.root.mkdir()
        self.outside = temp / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        self.boundary = WorkspaceBoundary(self.root)

    def test_rejects_parent_traversal_and_external_absolute_path(self) -> None:
        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve("../outside/secret.txt")
        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve(self.outside / "secret.txt")

    def test_rejects_symlink_to_external_file(self) -> None:
        link = self.root / "secret-link"
        link.symlink_to(self.outside / "secret.txt")

        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve("secret-link")

    def test_rejects_parent_symlink_to_external_directory(self) -> None:
        link = self.root / "external"
        link.symlink_to(self.outside, target_is_directory=True)

        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve("external/secret.txt")

    def test_rejects_broken_symlink(self) -> None:
        link = self.root / "broken"
        link.symlink_to(self.outside / "missing.txt")

        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve("broken", must_exist=False)

    def test_allows_internal_symlink_for_read_but_not_write(self) -> None:
        target = self.root / "real.txt"
        target.write_text("safe\n", encoding="utf-8")
        link = self.root / "link.txt"
        link.symlink_to(target)

        resolved = self.boundary.resolve("link.txt")

        self.assertEqual(resolved.path, target.resolve())
        with self.assertRaises(WorkspaceViolation):
            self.boundary.resolve(
                "link.txt",
                for_write=True,
                allow_final_symlink=False,
            )


if __name__ == "__main__":
    unittest.main()

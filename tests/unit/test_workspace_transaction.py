from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.checkpoint import (
    CheckpointError,
    FileCheckpointService,
    RewindConflict,
)
from rivet.workspace.patch import AtomicPatchApplier, PatchConflict, TextEdit
from rivet.workspace.transaction import AtomicWriteConflict, atomic_write_bytes


class WorkspaceTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temp = Path(self.temporary.name)
        self.root = temp / "workspace"
        self.root.mkdir()
        self.state = temp / "state"
        self.boundary = WorkspaceBoundary(self.root)
        self.path = self.root / "sample.py"
        self.path.write_text("value = 1\n", encoding="utf-8")

    def test_atomic_write_checks_expected_hash(self) -> None:
        target = self.boundary.resolve("sample.py", for_write=True)

        with self.assertRaises(AtomicWriteConflict):
            atomic_write_bytes(
                self.boundary,
                target,
                b"value = 2\n",
                expected_sha256="0" * 64,
            )

        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 1\n")

    def test_checkpoint_stores_before_image_outside_workspace(self) -> None:
        target = self.boundary.resolve("sample.py", for_write=True)
        service = FileCheckpointService(self.state)

        manifest = service.create(
            boundary=self.boundary,
            targets=(target,),
            tool_name="apply_patch",
            prepared_digest="a" * 64,
        )

        entry = manifest.affected_paths[0]
        self.assertEqual(entry.path, "sample.py")
        self.assertEqual(
            entry.before_sha256,
            hashlib.sha256(b"value = 1\n").hexdigest(),
        )
        manifest_path = self.state / "manifests" / f"{manifest.checkpoint_id}.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["manifest_digest"], manifest.manifest_digest)
        self.assertFalse((self.root / ".rivet").exists())

    def test_checkpoint_rejects_store_inside_workspace(self) -> None:
        service = FileCheckpointService(self.root / ".state")
        target = self.boundary.resolve("sample.py", for_write=True)

        with self.assertRaises(CheckpointError):
            service.create(
                boundary=self.boundary,
                targets=(target,),
                tool_name="apply_patch",
                prepared_digest="a" * 64,
            )
        self.assertFalse((self.root / ".state").exists())

    def test_rewind_restores_before_image_and_detects_external_change(self) -> None:
        service = FileCheckpointService(self.state)
        target = self.boundary.resolve("sample.py", for_write=True)
        manifest = service.create(
            boundary=self.boundary,
            targets=(target,),
            tool_name="apply_patch",
            prepared_digest="a" * 64,
        )
        self.path.write_text("value = 2\n", encoding="utf-8")
        after_hash = hashlib.sha256(b"value = 2\n").hexdigest()

        result = service.rewind(
            boundary=self.boundary,
            checkpoint_id=manifest.checkpoint_id,
            expected_after_hashes={"sample.py": after_hash},
        )

        self.assertEqual(result.restored_paths, ("sample.py",))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 1\n")

        self.path.write_text("external = True\n", encoding="utf-8")
        with self.assertRaises(RewindConflict):
            service.rewind(
                boundary=self.boundary,
                checkpoint_id=manifest.checkpoint_id,
                expected_after_hashes={"sample.py": after_hash},
            )

    def test_patch_uses_exact_match_and_atomic_write(self) -> None:
        target = self.boundary.resolve("sample.py", for_write=True)
        patcher = AtomicPatchApplier(self.boundary)

        result = patcher.apply(
            (
                TextEdit(
                    target=target,
                    old_text="value = 1",
                    new_text="value = 2",
                ),
            )
        )

        self.assertEqual(self.path.read_text(encoding="utf-8"), "value = 2\n")
        self.assertIn("-value = 1", result.unified_diff)
        self.assertIn("+value = 2", result.unified_diff)

    def test_patch_rejects_ambiguous_match(self) -> None:
        self.path.write_text("x\nx\n", encoding="utf-8")
        target = self.boundary.resolve("sample.py", for_write=True)

        with self.assertRaises(PatchConflict):
            AtomicPatchApplier(self.boundary).apply(
                (
                    TextEdit(
                        target=target,
                        old_text="x",
                        new_text="y",
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.state.artifacts import (
    ArtifactIntegrityError,
    ArtifactStoreError,
    ContentAddressedArtifactStore,
)
from rivet.state.protocol import StateMutation
from rivet.state.sqlite import SQLiteStateStore


class ContentAddressedArtifactStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ContentAddressedArtifactStore(self.root / "artifacts")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_content_is_addressed_by_sha256_and_deduplicated(self) -> None:
        first = self.store.put_text("same content")
        second = self.store.put_text("same content")
        self.assertEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(self.store.read_bytes(first), b"same content")
        files = [path for path in self.store.root.rglob("*") if path.is_file()]
        self.assertEqual(len(files), 1)

    def test_integrity_is_verified_on_read(self) -> None:
        artifact = self.store.put_bytes(b"original")
        self.store.path_for_digest(artifact.sha256).write_bytes(b"corrupt")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read_bytes(artifact)

    def test_maximum_size_is_enforced_before_write(self) -> None:
        store = ContentAddressedArtifactStore(self.root / "limited", max_bytes=3)
        with self.assertRaises(ArtifactStoreError):
            store.put_bytes(b"four")

    def test_repeated_content_metadata_registration_is_idempotent(self) -> None:
        first = self.store.put_text("same content")
        second = self.store.put_text("same content")
        state = SQLiteStateStore(self.root / "state.sqlite3")
        try:
            state.commit(StateMutation(artifacts=(first,)))
            state.commit(StateMutation(artifacts=(second,)))
            self.assertEqual(state.load_artifact(first.artifact_id), first)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()

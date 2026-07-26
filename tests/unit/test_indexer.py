from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.indexer import WorkspaceIndexer
from rivet.code_intelligence.retrieval.dense import (
    HashEmbeddingModel,
    InMemoryDenseIndex,
)
from rivet.code_intelligence.retrieval.sparse import SqliteSparseIndex


class WorkspaceIndexerTests(unittest.TestCase):
    def test_incremental_refresh_updates_and_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def old_name():\n    return 1\n", encoding="utf-8")
            with SqliteSparseIndex(root / "state" / "index.db") as sparse:
                indexer = WorkspaceIndexer(
                    workspace_root=root,
                    workspace_id="workspace",
                    sparse_index=sparse,
                )

                first = indexer.refresh()
                second = indexer.refresh()
                source.write_text("def new_name():\n    return 2\n", encoding="utf-8")
                third = indexer.refresh()
                new_results = sparse.search("new_name", limit=5)
                old_results = sparse.search("old_name", limit=5)
                source.unlink()
                fourth = indexer.refresh()

                self.assertEqual(first.indexed_files, 1)
                self.assertEqual(second.unchanged_files, 1)
                self.assertEqual(third.indexed_files, 1)
                self.assertTrue(new_results)
                self.assertFalse(old_results)
                self.assertEqual(fourth.deleted_files, 1)
                self.assertFalse(sparse.search("new_name", limit=5))

    def test_refresh_populates_and_reconciles_additional_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def locate_invoice():\n    return 1\n", encoding="utf-8")
            dense = InMemoryDenseIndex(HashEmbeddingModel(dimension=64))
            with SqliteSparseIndex(root / "state" / "index.db") as sparse:
                indexer = WorkspaceIndexer(
                    workspace_root=root,
                    workspace_id="workspace",
                    sparse_index=sparse,
                    additional_indexes=(dense,),
                )

                indexer.refresh()
                self.assertTrue(
                    dense.search(
                        "locate invoice",
                        limit=5,
                        workspace_id="workspace",
                    )
                )

                dense = InMemoryDenseIndex(HashEmbeddingModel(dimension=64))
                restarted = WorkspaceIndexer(
                    workspace_root=root,
                    workspace_id="workspace",
                    sparse_index=sparse,
                    additional_indexes=(dense,),
                )
                report = restarted.refresh()
                self.assertEqual(report.unchanged_files, 1)
                self.assertTrue(dense.search("invoice", limit=5))

                source.unlink()
                restarted.refresh()
                self.assertFalse(dense.search("invoice", limit=5))


if __name__ == "__main__":
    unittest.main()

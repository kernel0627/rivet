from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.python_ast import PythonAstAnalyzer
from rivet.code_intelligence.retrieval.fusion import reciprocal_rank_fusion
from rivet.code_intelligence.retrieval.reranker import LexicalReranker
from rivet.code_intelligence.retrieval.sparse import SqliteSparseIndex
from rivet.code_intelligence.types import RetrievedChunk


class RetrievalTests(unittest.TestCase):
    def _chunks(self):
        source = """\
class SessionStore:
    def load_session(self, session_id: str):
        return session_id

def calculate_invoice_total(items):
    return sum(items)
"""
        return PythonAstAnalyzer().chunks(
            source,
            file_path="storage.py",
            workspace_id="workspace",
            index_version="one",
        )

    def test_sparse_index_search_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SqliteSparseIndex(Path(directory) / "index.db") as index:
                chunks = self._chunks()
                index.upsert(chunks)

                results = index.search(
                    "load session",
                    limit=5,
                    workspace_id="workspace",
                )

                self.assertTrue(results)
                self.assertEqual(results[0].chunk.qualified_name, "SessionStore.load_session")
                self.assertEqual(
                    index.delete_file("workspace", "storage.py"),
                    len(chunks),
                )
                self.assertEqual(index.search("session", limit=5), [])

    def test_rrf_and_reranker_are_deterministic(self) -> None:
        chunks = self._chunks()
        first = RetrievedChunk(chunks[2], 0.8, "sparse", 1)
        second = RetrievedChunk(chunks[3], 0.7, "sparse", 2)

        fused = reciprocal_rank_fusion(
            {
                "sparse": [first, second],
                "dense": [
                    RetrievedChunk(chunks[3], 0.9, "dense", 1),
                    RetrievedChunk(chunks[2], 0.6, "dense", 2),
                ],
            },
            limit=2,
        )
        reranked = LexicalReranker().rerank(
            "calculate invoice total",
            fused,
            limit=2,
        )

        self.assertEqual(len(fused), 2)
        self.assertEqual(reranked[0].chunk.qualified_name, "calculate_invoice_total")


if __name__ == "__main__":
    unittest.main()

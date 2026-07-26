from __future__ import annotations

import unittest

from rivet.code_intelligence.python_ast import PythonAstAnalyzer
from rivet.code_intelligence.retrieval.dense import (
    HashEmbeddingModel,
    InMemoryDenseIndex,
)
from rivet.code_intelligence.retrieval.hybrid import HybridRetriever
from rivet.code_intelligence.retrieval.reranker import LexicalReranker


class DenseRetrievalTests(unittest.TestCase):
    def test_dense_and_hybrid_retrieval(self) -> None:
        chunks = PythonAstAnalyzer().chunks(
            """\
def refund_payment(transaction):
    return transaction.refund()

def calculate_shipping_distance(origin, destination):
    return origin.distance_to(destination)
""",
            file_path="payments.py",
            workspace_id="workspace",
            index_version="one",
        )
        dense = InMemoryDenseIndex(HashEmbeddingModel(dimension=128))
        dense.upsert(chunks)

        dense_results = dense.search("refund transaction payment", limit=3)
        hybrid = HybridRetriever(
            sparse=None,
            dense=dense,
            reranker=LexicalReranker(),
        )
        hybrid_results = hybrid.search("refund payment", limit=2)

        self.assertTrue(dense_results)
        self.assertEqual(
            hybrid_results[0].chunk.qualified_name,
            "refund_payment",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.indexer import WorkspaceIndexer
from rivet.code_intelligence.retrieval.sparse import SqliteSparseIndex
from rivet.tools.builtins.code_intelligence import (
    FindPythonSymbolTool,
    IndexStatusTool,
    PythonOutlineTool,
    RefreshIndexTool,
)
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import (
    PermissionDecision,
    PermissionOutcome,
    PermissionScope,
    ToolProposal,
)
from rivet.tools.executor import ToolExecutor
from rivet.workspace.boundary import WorkspaceBoundary


class AllowBroker:
    async def decide(self, request):
        return PermissionDecision(
            outcome=PermissionOutcome.ALLOW,
            prepared_digest=request.prepared_digest,
            scope=PermissionScope.ONCE,
        )


class CodeIntelligenceToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "sample.py").write_text(
            "import os\n\nclass Greeter:\n    def hello(self, name: str) -> str:\n"
            "        return f'hello {name}'\n",
            encoding="utf-8",
        )
        self.executor = ToolExecutor(
            ToolCatalog([PythonOutlineTool(), FindPythonSymbolTool()]),
            WorkspaceBoundary(self.root),
            permission_broker=AllowBroker(),
        )

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_outline_returns_stable_symbol_spans(self) -> None:
        proposal = ToolProposal.from_arguments(
            tool_call_id="call-1",
            ordinal=0,
            name="python_outline",
            arguments={"path": "sample.py"},
        )
        outcome = self.executor.prepare(proposal)
        self.assertTrue(outcome.ok)
        result = await self.executor.execute(outcome.prepared)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["symbol_count"], 2)
        self.assertTrue(any(span.start_line == 3 for span in result.code_spans))

    async def test_find_symbol_returns_matching_source(self) -> None:
        proposal = ToolProposal.from_arguments(
            tool_call_id="call-2",
            ordinal=0,
            name="find_python_symbol",
            arguments={"path": "sample.py", "query": "hello"},
        )
        outcome = self.executor.prepare(proposal)
        result = await self.executor.execute(outcome.prepared)
        self.assertTrue(result.ok)
        self.assertIn("def hello", result.content[0].text)

    async def test_index_status_and_refresh_use_workspace_index_service(self) -> None:
        sparse = SqliteSparseIndex(self.root / "index.sqlite3")
        self.addAsyncCleanup(self._close_sparse, sparse)
        indexer = WorkspaceIndexer(
            workspace_root=self.root,
            workspace_id="workspace",
            sparse_index=sparse,
        )
        executor = ToolExecutor(
            ToolCatalog([IndexStatusTool(), RefreshIndexTool()]),
            WorkspaceBoundary(self.root),
            permission_broker=AllowBroker(),
        )
        refresh = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="refresh",
                ordinal=0,
                name="refresh_index",
                arguments={},
            )
        )
        assert refresh.prepared is not None
        refresh_result = await executor.execute(
            refresh.prepared,
            services={"workspace_indexer": indexer},
        )
        status = executor.prepare(
            ToolProposal.from_arguments(
                tool_call_id="status",
                ordinal=0,
                name="index_status",
                arguments={},
            )
        )
        assert status.prepared is not None
        status_result = await executor.execute(
            status.prepared,
            services={"workspace_indexer": indexer},
        )

        self.assertTrue(refresh_result.ok)
        self.assertTrue(status_result.ok)
        self.assertIn("index_version", status_result.content[0].text)

    async def _close_sparse(self, sparse: SqliteSparseIndex) -> None:
        sparse.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from rivet.tools.builtins.filesystem import (
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
)
from rivet.tools.catalog import ToolCatalog
from rivet.tools.contracts import ToolProposal
from rivet.tools.executor import ToolExecutor
from rivet.workspace.boundary import WorkspaceBoundary


def make_proposal(
    tool_call_id: str,
    name: str,
    arguments: dict,
    *,
    ordinal: int = 0,
) -> ToolProposal:
    return ToolProposal.from_arguments(
        tool_call_id=tool_call_id,
        ordinal=ordinal,
        name=name,
        arguments=arguments,
    )


class FilesystemToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "sample.py").write_text(
            "class Session:\n    pass\n",
            encoding="utf-8",
        )
        self.executor = ToolExecutor(
            ToolCatalog([ListFilesTool(), ReadFileTool(), SearchTextTool()]),
            WorkspaceBoundary(self.root),
        )

    async def test_list_and_read_return_structured_metadata(self) -> None:
        list_outcome = self.executor.prepare(
            make_proposal("1", "list_files", {"path": ".", "max_depth": 2})
        )
        read_outcome = self.executor.prepare(
            make_proposal(
                "2",
                "read_file",
                {"path": "pkg/sample.py"},
                ordinal=1,
            )
        )
        assert list_outcome.prepared is not None
        assert read_outcome.prepared is not None

        listed = await self.executor.execute(list_outcome.prepared)
        read = await self.executor.execute(read_outcome.prepared)

        self.assertIn("pkg/sample.py", listed.content[0].text)
        self.assertEqual(read.content[0].path, "pkg/sample.py")
        self.assertIn("1: class Session:", read.content[0].code)
        self.assertEqual(len(read.metadata["sha256"]), 64)
        self.assertEqual(read.code_spans[0].start_line, 1)

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is not installed")
    async def test_search_returns_workspace_relative_matches(self) -> None:
        outcome = self.executor.prepare(make_proposal("1", "search_text", {"query": "Session"}))
        assert outcome.prepared is not None

        result = await self.executor.execute(outcome.prepared)

        self.assertTrue(result.ok, result.error_message)
        self.assertIn("pkg/sample.py:1:7:class Session:", result.content[0].text)


if __name__ == "__main__":
    unittest.main()

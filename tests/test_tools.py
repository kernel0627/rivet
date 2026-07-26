from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rivet.models.types import ToolCall
from rivet.safety.workspace import WorkspaceBoundary
from rivet.tools.filesystem import ReadFileTool, register_filesystem_tools
from rivet.tools.registry import ToolRegistry


class ToolTests(unittest.TestCase):
    def test_registry_validates_required_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceBoundary(Path(directory))))

            result = registry.execute(ToolCall(id="1", name="read_file", arguments={}))

            self.assertFalse(result.ok)
            self.assertIn("missing required", result.error or "")

    def test_filesystem_tools_read_and_search_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(
                "class Session:\n    pass\n",
                encoding="utf-8",
            )
            registry = ToolRegistry()
            register_filesystem_tools(registry, WorkspaceBoundary(root))

            read_result = registry.execute(
                ToolCall(id="1", name="read_file", arguments={"path": "sample.py"})
            )
            search_result = registry.execute(
                ToolCall(id="2", name="search_text", arguments={"query": "Session"})
            )

            self.assertTrue(read_result.ok)
            self.assertIn("1: class Session:", read_result.output)
            self.assertTrue(search_result.ok)
            self.assertIn("sample.py:1:7:class Session:", search_result.output)

    def test_search_query_starting_with_dashes_is_not_an_rg_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("ordinary text\n", encoding="utf-8")
            registry = ToolRegistry()
            register_filesystem_tools(registry, WorkspaceBoundary(root))

            result = registry.execute(
                ToolCall(id="1", name="search_text", arguments={"query": "--version"})
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "")

    @patch("rivet.tools.filesystem.subprocess.run")
    @patch("rivet.tools.filesystem.shutil.which", return_value="/usr/bin/rg")
    def test_search_contract_passes_query_through_pattern_argument(
        self,
        _which,
        run,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
            registry = ToolRegistry()
            register_filesystem_tools(registry, WorkspaceBoundary(root))

            result = registry.execute(
                ToolCall(
                    id="1",
                    name="search_text",
                    arguments={"query": "--pre=/tmp/untrusted-program"},
                )
            )

            self.assertTrue(result.ok)
            argv = run.call_args.args[0]
            self.assertIn("--no-config", argv)
            pattern_index = argv.index("-e")
            self.assertEqual(argv[pattern_index + 1], "--pre=/tmp/untrusted-program")
            self.assertEqual(argv[-2], "--")
            self.assertEqual(argv[-1], str(root.resolve()))


if __name__ == "__main__":
    unittest.main()

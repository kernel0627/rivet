from __future__ import annotations

import unittest

from rivet.code_intelligence.python_ast import PythonAnalysisError, PythonAstAnalyzer
from rivet.code_intelligence.types import SymbolKind

SOURCE = '''\
import os
from .state import Run

class Worker:
    """Runs work."""

    def run(self, value: int) -> str:
        return os.fspath(value)

async def start(run: Run) -> None:
    worker = Worker()
    worker.run(run)
'''


class PythonAstTests(unittest.TestCase):
    def test_extracts_symbols_imports_and_references(self) -> None:
        analysis = PythonAstAnalyzer().analyze(SOURCE, file_path="worker.py")

        self.assertEqual(
            [(item.qualified_name, item.kind) for item in analysis.symbols],
            [
                ("Worker", SymbolKind.CLASS),
                ("Worker.run", SymbolKind.METHOD),
                ("start", SymbolKind.ASYNC_FUNCTION),
            ],
        )
        self.assertEqual(analysis.symbols[1].signature, "def run(self, value: int) -> str")
        self.assertEqual(analysis.symbols[0].docstring, "Runs work.")
        self.assertEqual(analysis.imports[1].module, "state")
        self.assertIn("Worker", {item.name for item in analysis.references})
        self.assertEqual(len(analysis.find_symbols("work")), 2)

    def test_read_symbol_and_chunks_have_stable_locations(self) -> None:
        analyzer = PythonAstAnalyzer()
        analysis = analyzer.analyze(SOURCE, file_path="worker.py")
        method = analysis.symbols[1]

        span = analyzer.read_symbol(SOURCE, method)
        chunks = analyzer.chunks(
            SOURCE,
            file_path="worker.py",
            workspace_id="workspace",
            index_version="v1",
        )

        self.assertEqual(span.start_line, 7)
        self.assertIn("def run", span.content)
        self.assertEqual(chunks[0].kind, "module")
        self.assertEqual(chunks[2].qualified_name, "Worker.run")
        self.assertEqual(
            chunks[2].chunk_id,
            analyzer.chunks(
                SOURCE,
                file_path="worker.py",
                workspace_id="workspace",
                index_version="v1",
            )[2].chunk_id,
        )

    def test_syntax_error_is_normalized(self) -> None:
        with self.assertRaises(PythonAnalysisError) as captured:
            PythonAstAnalyzer().analyze("def broken(:\n", file_path="broken.py")

        self.assertEqual(captured.exception.path, "broken.py")
        self.assertEqual(captured.exception.line, 1)


if __name__ == "__main__":
    unittest.main()

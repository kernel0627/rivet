from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.lsp.client import LspClient


class LspSubprocessContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_process_initializes_reports_diagnostics_and_closes(
        self,
    ) -> None:
        server = Path(__file__).parents[1] / "fixtures" / "fake_lsp_server.py"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "main.py"
            source.write_text("def main():\n    return 1\n", encoding="utf-8")
            client = LspClient(
                command=(sys.executable, str(server)),
                workspace_root=workspace,
                request_timeout=2.0,
            )
            try:
                await client.start()
                await client.open_document(
                    source,
                    text=source.read_text(encoding="utf-8"),
                )
                definitions = await client.definition(
                    source,
                    line=0,
                    character=5,
                )

                self.assertEqual(len(definitions), 1)
                self.assertEqual(definitions[0].path, str(source))
                diagnostics = client.diagnostics(source)
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(diagnostics[0].message, "fake diagnostic")
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()

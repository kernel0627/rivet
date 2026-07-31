from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.lsp import discover_python_server
from rivet.code_intelligence.lsp.client import LspClient


class RealPythonLspContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovered_server_resolves_definition(self) -> None:
        config = discover_python_server()
        if config is None:
            self.skipTest("no Python language server is installed")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "main.py"
            source.write_text(
                "def target() -> int:\n"
                "    return 1\n"
                "\n"
                "value = target()\n",
                encoding="utf-8",
            )
            client = LspClient(
                command=config.command,
                workspace_root=workspace,
                request_timeout=10.0,
            )
            try:
                await client.start()
                await client.open_document(
                    source,
                    text=source.read_text(encoding="utf-8"),
                )
                definitions = await client.definition(
                    source,
                    line=3,
                    character=10,
                )

                self.assertTrue(definitions)
                self.assertEqual(definitions[0].path, str(source))
                self.assertEqual(definitions[0].range.start.line, 0)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()

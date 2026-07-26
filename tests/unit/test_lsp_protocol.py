from __future__ import annotations

import asyncio
import unittest

from rivet.code_intelligence.lsp.client import LspClient
from rivet.code_intelligence.lsp.protocol import (
    LspProtocolError,
    encode_message,
    read_message,
)
from rivet.code_intelligence.lsp.types import LspDiagnostic


class LspProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_encode_and_read_round_trip(self) -> None:
        value = {"jsonrpc": "2.0", "id": 1, "result": {"ok": "是"}}
        reader = asyncio.StreamReader()
        reader.feed_data(encode_message(value))
        reader.feed_eof()

        self.assertEqual(await read_message(reader), value)

    async def test_rejects_oversized_message_before_reading_payload(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"Content-Length: 999\r\n\r\n")
        reader.feed_eof()

        with self.assertRaises(LspProtocolError):
            await read_message(reader, max_content_bytes=10)

    async def test_client_collects_diagnostics_and_resolves_response(self) -> None:
        client = LspClient(command=("unused",), workspace_root=__import__("pathlib").Path("."))
        future = asyncio.get_running_loop().create_future()
        client._pending[7] = future
        client._handle_message({"jsonrpc": "2.0", "id": 7, "result": {"value": 1}})
        client._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": "file:///tmp/example.py",
                    "diagnostics": [
                        {
                            "range": {
                                "start": {"line": 1, "character": 2},
                                "end": {"line": 1, "character": 5},
                            },
                            "message": "problem",
                            "severity": 1,
                        }
                    ],
                },
            }
        )

        self.assertEqual(await future, {"value": 1})
        diagnostic = client._diagnostics["file:///tmp/example.py"][0]
        self.assertIsInstance(diagnostic, LspDiagnostic)
        self.assertEqual(diagnostic.message, "problem")


if __name__ == "__main__":
    unittest.main()

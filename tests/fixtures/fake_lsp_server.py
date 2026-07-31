from __future__ import annotations

import json
import sys
from typing import Any


def _read_message() -> dict[str, Any] | None:
    content_length: int | None = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line == b"\r\n":
            break
        name, _, value = line.decode("ascii").partition(":")
        if name.casefold() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        raise RuntimeError("missing Content-Length")
    payload = sys.stdin.buffer.read(content_length)
    return json.loads(payload)


def _send(message: dict[str, Any]) -> None:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    sys.stdout.buffer.write(
        f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
    )
    sys.stdout.buffer.flush()


def _response(request_id: object, result: object) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def main() -> None:
    document_uri = ""
    while message := _read_message():
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            _response(
                request_id,
                {
                    "capabilities": {
                        "definitionProvider": True,
                        "textDocumentSync": 1,
                    }
                },
            )
        elif method == "textDocument/didOpen":
            document_uri = params["textDocument"]["uri"]
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": document_uri,
                        "diagnostics": [
                            {
                                "range": {
                                    "start": {"line": 0, "character": 0},
                                    "end": {"line": 0, "character": 4},
                                },
                                "message": "fake diagnostic",
                                "severity": 2,
                            }
                        ],
                    },
                }
            )
        elif method == "textDocument/definition":
            _response(
                request_id,
                [
                    {
                        "uri": document_uri,
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 8},
                        },
                    }
                ],
            )
        elif method == "shutdown":
            _response(request_id, None)
        elif method == "exit":
            return


if __name__ == "__main__":
    main()

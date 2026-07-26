from __future__ import annotations

import asyncio
import json
from typing import Any


class LspProtocolError(RuntimeError):
    pass


async def read_message(
    reader: asyncio.StreamReader,
    *,
    max_content_bytes: int = 10_000_000,
) -> dict[str, Any]:
    content_length: int | None = None
    while True:
        line = await reader.readline()
        if not line:
            raise EOFError("language server closed stdout")
        if line in {b"\r\n", b"\n"}:
            break
        try:
            name, raw_value = line.decode("ascii").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise LspProtocolError(f"invalid LSP header: {line!r}") from exc
        if name.casefold() == "content-length":
            try:
                content_length = int(raw_value.strip())
            except ValueError as exc:
                raise LspProtocolError("invalid Content-Length") from exc
    if content_length is None:
        raise LspProtocolError("missing Content-Length")
    if content_length < 0 or content_length > max_content_bytes:
        raise LspProtocolError(f"LSP message size is not allowed: {content_length}")
    payload = await reader.readexactly(content_length)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LspProtocolError("invalid LSP JSON payload") from exc
    if not isinstance(value, dict):
        raise LspProtocolError("LSP payload must be a JSON object")
    return value


def encode_message(value: dict[str, Any]) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload


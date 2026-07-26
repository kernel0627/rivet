from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rivet.domain import Event
from rivet.observability.redaction import Redactor


class JsonlEventSink:
    """Append redacted committed events to an optional external trace file."""

    def __init__(self, path: Path, *, redactor: Redactor | None = None) -> None:
        self.path = path.expanduser().resolve(strict=False)
        self.redactor = redactor or Redactor()
        self._lock = asyncio.Lock()

    async def __call__(self, event: Event) -> None:
        payload = self.redactor.redact(event.to_dict())
        line = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

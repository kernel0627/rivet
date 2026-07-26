from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rivet.code_intelligence.lsp.protocol import encode_message, read_message
from rivet.code_intelligence.lsp.types import LspDiagnostic, LspLocation


class LspError(RuntimeError):
    pass


class LspClient:
    def __init__(
        self,
        *,
        command: Sequence[str],
        workspace_root: Path,
        request_timeout: float = 30.0,
        initialization_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not command:
            raise ValueError("language server command cannot be empty")
        self.command = tuple(command)
        self.workspace_root = workspace_root.expanduser().resolve()
        self.request_timeout = request_timeout
        self.initialization_options = dict(initialization_options or {})
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._diagnostics: dict[str, tuple[LspDiagnostic, ...]] = {}
        self._request_id = 0
        self._write_lock = asyncio.Lock()
        self._stderr_lines: list[str] = []
        self._initialized = False

    async def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.workspace_root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(self._stderr_loop())
            result = await self.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self.workspace_root.as_uri(),
                    "capabilities": {
                        "textDocument": {
                            "definition": {},
                            "references": {},
                            "hover": {},
                            "documentSymbol": {},
                            "publishDiagnostics": {},
                        },
                        "workspace": {"symbol": {}},
                    },
                    "initializationOptions": self.initialization_options,
                },
            )
            if not isinstance(result, dict):
                raise LspError("language server returned an invalid initialize result")
            await self.notify("initialized", {})
            self._initialized = True
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.returncode is None and self._initialized:
            with contextlib.suppress(Exception):
                await self.request("shutdown", None)
            with contextlib.suppress(Exception):
                await self.notify("exit", None)
        if process.returncode is None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=2)
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=2)
        if process.returncode is None:
            process.kill()
            await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._fail_pending(LspError("language server client closed"))
        self._process = None
        self._initialized = False

    async def request(self, method: str, params: Any) -> Any:
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise LspError(f"LSP request timed out: {method}") from exc

    async def notify(self, method: str, params: Any) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    async def open_document(
        self,
        path: Path,
        *,
        text: str,
        language_id: str = "python",
        version: int = 1,
    ) -> None:
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.resolve().as_uri(),
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )

    async def change_document(
        self,
        path: Path,
        *,
        text: str,
        version: int,
    ) -> None:
        if version < 1:
            raise ValueError("document version must be positive")
        await self.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": path.resolve().as_uri(),
                    "version": version,
                },
                "contentChanges": [{"text": text}],
            },
        )

    async def close_document(self, path: Path) -> None:
        await self.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": path.resolve().as_uri()}},
        )

    async def definition(
        self,
        path: Path,
        *,
        line: int,
        character: int,
    ) -> tuple[LspLocation, ...]:
        result = await self.request(
            "textDocument/definition",
            _position_params(path, line, character),
        )
        return _locations(result)

    async def references(
        self,
        path: Path,
        *,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> tuple[LspLocation, ...]:
        params = _position_params(path, line, character)
        params["context"] = {"includeDeclaration": include_declaration}
        result = await self.request("textDocument/references", params)
        return _locations(result)

    async def hover(
        self,
        path: Path,
        *,
        line: int,
        character: int,
    ) -> Any:
        return await self.request(
            "textDocument/hover",
            _position_params(path, line, character),
        )

    async def document_symbols(self, path: Path) -> Any:
        return await self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": path.resolve().as_uri()}},
        )

    async def workspace_symbols(self, query: str) -> Any:
        return await self.request("workspace/symbol", {"query": query})

    def diagnostics(self, path: Path) -> tuple[LspDiagnostic, ...]:
        return self._diagnostics.get(path.resolve().as_uri(), ())

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines[-50:])

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise LspError("language server is not running")
        if process.returncode is not None:
            raise LspError(f"language server exited with code {process.returncode}")
        encoded = encode_message(message)
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                message = await read_message(process.stdout)
                self._handle_message(message)
        except (EOFError, asyncio.IncompleteReadError) as exc:
            self._fail_pending(LspError(f"language server output closed: {exc}"))
        except Exception as exc:
            self._fail_pending(LspError(f"language server protocol failed: {exc}"))

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
            if len(self._stderr_lines) > 200:
                del self._stderr_lines[:100]

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            raw_id = message["id"]
            if not isinstance(raw_id, int):
                return
            future = self._pending.pop(raw_id, None)
            if future is None or future.done():
                return
            if message.get("error") is not None:
                future.set_exception(LspError(f"LSP error: {message['error']}"))
            else:
                future.set_result(message.get("result"))
            return
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params", {})
            if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
                return
            diagnostics = params.get("diagnostics", [])
            if not isinstance(diagnostics, list):
                return
            self._diagnostics[params["uri"]] = tuple(
                LspDiagnostic.from_json(item)
                for item in diagnostics
                if isinstance(item, dict)
            )

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)


def _position_params(path: Path, line: int, character: int) -> dict[str, Any]:
    if line < 0 or character < 0:
        raise ValueError("LSP positions are zero-based and cannot be negative")
    return {
        "textDocument": {"uri": path.resolve().as_uri()},
        "position": {"line": line, "character": character},
    }


def _locations(value: Any) -> tuple[LspLocation, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise LspError("language server returned invalid locations")
    return tuple(
        LspLocation.from_json(item)
        for item in value
        if isinstance(item, dict) and "uri" in item and "range" in item
    )

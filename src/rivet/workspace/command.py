from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import signal
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rivet.workspace.boundary import WorkspaceBoundary, WorkspaceViolation

DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TMPDIR",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_OPTIONAL_LOCKS",
        "GIT_TERMINAL_PROMPT",
    }
)


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    command_digest: str


class ProcessRunner:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        inherited_env: frozenset[str] = DEFAULT_ENV_ALLOWLIST,
    ) -> None:
        self.boundary = boundary
        self.inherited_env = inherited_env

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path = ".",
        env: Mapping[str, str] | None = None,
        allowed_env_names: frozenset[str] = DEFAULT_ENV_ALLOWLIST,
        timeout: float = 30.0,
        max_stdout_bytes: int = 100_000,
        max_stderr_bytes: int = 100_000,
    ) -> ProcessResult:
        normalized_argv = self._validate_argv(argv)
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be finite and positive")
        if max_stdout_bytes < 0 or max_stderr_bytes < 0:
            raise ValueError("output byte limits cannot be negative")
        cwd_target = self.boundary.resolve(cwd)
        if not cwd_target.path.is_dir():
            raise WorkspaceViolation(f"command cwd is not a directory: {cwd}")
        child_env = {
            name: value for name, value in os.environ.items() if name in self.inherited_env
        }
        for name, value in (env or {}).items():
            if name not in allowed_env_names:
                raise WorkspaceViolation(f"environment variable is not allowed: {name}")
            if "\x00" in name or "\x00" in value or "=" in name:
                raise WorkspaceViolation(f"invalid environment variable: {name!r}")
            child_env[name] = value

        started = time.monotonic()
        command_digest = hashlib.sha256(
            json.dumps(
                {
                    "argv": normalized_argv,
                    "cwd": cwd_target.relative_path,
                    "env_names": sorted((env or {}).keys()),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        process = await asyncio.create_subprocess_exec(
            *normalized_argv,
            cwd=cwd_target.path,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_drain_stream(process.stdout, max_stdout_bytes))
        stderr_task = asyncio.create_task(_drain_stream(process.stderr, max_stderr_bytes))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate_process_tree(process)
        except asyncio.CancelledError:
            await self._terminate_process_tree(process)
            await asyncio.gather(
                stdout_task,
                stderr_task,
                return_exceptions=True,
            )
            raise
        stdout_bytes, stdout_truncated = await stdout_task
        stderr_bytes, stderr_truncated = await stderr_task
        duration_ms = int((time.monotonic() - started) * 1000)
        return ProcessResult(
            argv=normalized_argv,
            cwd=cwd_target.relative_path,
            exit_code=process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_ms=duration_ms,
            command_digest=command_digest,
        )

    @staticmethod
    def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError("argv must be a non-empty sequence of strings")
        normalized = tuple(str(item) for item in argv)
        if any(not item or "\x00" in item for item in normalized):
            raise ValueError("argv items must be non-empty and cannot contain null bytes")
        return normalized

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
            return
        except asyncio.TimeoutError:
            pass
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            process.kill()
        await process.wait()


async def _drain_stream(
    stream: asyncio.StreamReader,
    limit: int,
) -> tuple[bytes, bool]:
    if limit < 0:
        raise ValueError("stream output limit cannot be negative")
    captured = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        remaining = max(0, limit - len(captured))
        if remaining:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(captured), truncated

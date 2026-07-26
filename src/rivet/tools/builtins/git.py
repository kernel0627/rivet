from __future__ import annotations

import os
import shutil

from pydantic import Field

from rivet.tools.contracts import (
    EffectClass,
    PermissionClass,
    PreparedTool,
    ToolArguments,
    ToolExecutionContext,
    ToolPreparation,
    ToolPrepareContext,
    ToolSpec,
)
from rivet.tools.results import (
    CommandOutput,
    DiffBlock,
    ErrorKind,
    TextBlock,
    ToolResult,
    ToolResultStatus,
)
from rivet.workspace.command import ProcessResult, ProcessRunner


class GitStatusArguments(ToolArguments):
    include_untracked: bool = True
    max_output_chars: int = Field(default=50_000, ge=1_000, le=100_000)


class GitDiffArguments(ToolArguments):
    cached: bool = False
    paths: list[str] = Field(default_factory=list, max_length=100)
    context_lines: int = Field(default=3, ge=0, le=20)
    max_output_chars: int = Field(default=100_000, ge=1_000, le=200_000)


class GitStatusTool:
    spec = ToolSpec(
        name="git_status",
        version="1.0.0",
        description="Show a bounded porcelain Git status for the workspace.",
        input_model=GitStatusArguments,
        output_types=(TextBlock, CommandOutput),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=15.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: GitStatusArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        root = context.workspace.resolve(".")
        return ToolPreparation(
            normalized_arguments=arguments.model_dump(mode="json"),
            resolved_targets=(root,),
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = GitStatusArguments.model_validate(prepared.normalized_arguments)
        untracked = "normal" if arguments.include_untracked else "no"
        command = [
            _git_executable(),
            "-c",
            "core.pager=cat",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "status",
            "--short",
            "--branch",
            f"--untracked-files={untracked}",
        ]
        process = await _runner(context).run(
            command,
            cwd=".",
            env=_safe_git_env(),
            timeout=prepared.timeout,
            max_stdout_bytes=arguments.max_output_chars,
            max_stderr_bytes=20_000,
        )
        return _git_result(process, as_diff=False)


class GitDiffTool:
    spec = ToolSpec(
        name="git_diff",
        version="1.0.0",
        description="Show a bounded Git diff without external diff programs or a pager.",
        input_model=GitDiffArguments,
        output_types=(DiffBlock, CommandOutput),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=20.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: GitDiffArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        targets = []
        normalized_paths: list[str] = []
        for path in arguments.paths:
            target = context.workspace.resolve(path, must_exist=False)
            targets.append(target)
            normalized_paths.append(target.relative_path)
        if not targets:
            targets.append(context.workspace.resolve("."))
        normalized = arguments.model_dump(mode="json")
        normalized["paths"] = normalized_paths
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=tuple(targets),
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = GitDiffArguments.model_validate(prepared.normalized_arguments)
        command = [
            _git_executable(),
            "-c",
            "core.pager=cat",
            "-c",
            "pager.diff=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            f"--unified={arguments.context_lines}",
        ]
        if arguments.cached:
            command.append("--cached")
        command.append("--")
        command.extend(arguments.paths)
        process = await _runner(context).run(
            command,
            cwd=".",
            env=_safe_git_env(),
            timeout=prepared.timeout,
            max_stdout_bytes=arguments.max_output_chars,
            max_stderr_bytes=20_000,
        )
        return _git_result(process, as_diff=True)


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is not installed")
    return executable


def _safe_git_env() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _runner(context: ToolExecutionContext) -> ProcessRunner:
    runner = context.services.get("process_runner")
    if runner is None:
        return ProcessRunner(context.workspace)
    if not isinstance(runner, ProcessRunner) and not hasattr(runner, "run"):
        raise TypeError("process_runner service must provide async run()")
    return runner


def _git_result(process: ProcessResult, *, as_diff: bool) -> ToolResult:
    command_output = CommandOutput(
        argv=process.argv,
        cwd=process.cwd,
        exit_code=process.exit_code,
        stdout=process.stdout,
        stderr=process.stderr,
        timed_out=process.timed_out,
        stdout_truncated=process.stdout_truncated,
        stderr_truncated=process.stderr_truncated,
    )
    if process.timed_out:
        return ToolResult(
            status=ToolResultStatus.TIMED_OUT,
            content=(command_output,),
            error_kind=ErrorKind.TOOL_TIMEOUT,
            error_message="git command timed out",
            retryable=True,
        )
    if process.exit_code != 0:
        return ToolResult(
            status=ToolResultStatus.ERROR,
            content=(command_output,),
            error_kind=ErrorKind.TOOL_EXECUTION_ERROR,
            error_message=process.stderr.strip() or "git command failed",
        )
    content = (
        (
            DiffBlock(process.stdout),
            command_output,
        )
        if as_diff
        else (
            TextBlock(process.stdout),
            command_output,
        )
    )
    return ToolResult.success(
        *content,
        metadata={"command_digest": process.command_digest},
    ).with_updates(truncated=process.stdout_truncated or process.stderr_truncated)

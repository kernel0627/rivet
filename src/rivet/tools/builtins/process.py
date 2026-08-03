from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

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
    ErrorKind,
    SideEffectState,
    ToolResult,
    ToolResultStatus,
)
from rivet.workspace.command import ProcessResult, ProcessRunner

Argument = Annotated[str, StringConstraints(min_length=1, max_length=8_192)]


class RunCommandArguments(ToolArguments):
    argv: list[Argument] = Field(min_length=1, max_length=256)
    cwd: str = Field(default=".", max_length=4_096)
    env: dict[str, str] = Field(default_factory=dict, max_length=32)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300.0)
    max_output_chars: int = Field(default=100_000, ge=1_000, le=200_000)


class RunTestsArguments(ToolArguments):
    argv: list[Argument] = Field(
        default_factory=lambda: ["python", "-m", "pytest"],
        min_length=1,
        max_length=64,
    )
    paths: list[Argument] = Field(default_factory=list, max_length=100)
    extra_args: list[Argument] = Field(default_factory=list, max_length=100)
    cwd: str = Field(default=".", max_length=4_096)
    timeout_seconds: float = Field(default=120.0, gt=0, le=900.0)
    max_output_chars: int = Field(default=100_000, ge=1_000, le=200_000)


class RunCommandTool:
    spec = ToolSpec(
        name="run_command",
        version="1.0.0",
        description="Execute an argv command without invoking a shell.",
        input_model=RunCommandArguments,
        output_types=(CommandOutput,),
        effect=EffectClass.EXECUTE,
        permission=PermissionClass.PROCESS_EXECUTE,
        default_timeout=300.0,
        idempotent=False,
        parallel_safe=False,
    )

    def prepare(
        self,
        arguments: RunCommandArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        cwd = context.workspace.resolve(arguments.cwd)
        if not cwd.path.is_dir():
            raise ValueError(f"command cwd is not a directory: {arguments.cwd}")
        normalized = arguments.model_dump(mode="json")
        normalized["cwd"] = cwd.relative_path
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=(cwd,),
            metadata={"requested_timeout": arguments.timeout_seconds},
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = RunCommandArguments.model_validate(prepared.normalized_arguments)
        runner = _runner(context)
        process = await runner.run(
            arguments.argv,
            cwd=arguments.cwd,
            env=arguments.env,
            timeout=arguments.timeout_seconds,
            max_stdout_bytes=arguments.max_output_chars,
            max_stderr_bytes=arguments.max_output_chars,
        )
        return _process_result(process)


class RunTestsTool:
    spec = ToolSpec(
        name="run_tests",
        version="1.0.0",
        description="Run a bounded test command without invoking a shell.",
        input_model=RunTestsArguments,
        output_types=(CommandOutput,),
        effect=EffectClass.EXECUTE,
        permission=PermissionClass.PROCESS_EXECUTE,
        default_timeout=900.0,
        idempotent=False,
        parallel_safe=False,
    )

    def prepare(
        self,
        arguments: RunTestsArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        cwd = context.workspace.resolve(arguments.cwd)
        if not cwd.path.is_dir():
            raise ValueError(f"test cwd is not a directory: {arguments.cwd}")
        paths: list[str] = []
        targets = [cwd]
        for path in arguments.paths:
            target = context.workspace.resolve(path)
            targets.append(target)
            paths.append(target.relative_path)
        normalized = arguments.model_dump(mode="json")
        normalized["cwd"] = cwd.relative_path
        normalized["paths"] = paths
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=tuple(targets),
            metadata={"requested_timeout": arguments.timeout_seconds},
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = RunTestsArguments.model_validate(prepared.normalized_arguments)
        argv = [*arguments.argv, *arguments.extra_args, *arguments.paths]
        process = await _runner(context).run(
            argv,
            cwd=arguments.cwd,
            timeout=arguments.timeout_seconds,
            max_stdout_bytes=arguments.max_output_chars,
            max_stderr_bytes=arguments.max_output_chars,
        )
        return _process_result(process, test_command=True)


def _runner(context: ToolExecutionContext) -> ProcessRunner:
    runner = context.services.get("process_runner")
    if runner is None:
        return ProcessRunner(context.workspace)
    if not isinstance(runner, ProcessRunner) and not hasattr(runner, "run"):
        raise TypeError("process_runner service must provide async run()")
    return runner


def _process_result(
    process: ProcessResult,
    *,
    test_command: bool = False,
) -> ToolResult:
    output = CommandOutput(
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
            content=(output,),
            error_kind=ErrorKind.TOOL_TIMEOUT,
            error_message="command timed out",
            retryable=False,
            side_effect_state=SideEffectState.UNCERTAIN,
        )
    if process.exit_code != 0:
        error_kind = (
            ErrorKind.VERIFICATION_FAILED if test_command else ErrorKind.TOOL_EXECUTION_ERROR
        )
        return ToolResult(
            status=ToolResultStatus.ERROR,
            content=(output,),
            error_kind=error_kind,
            error_message=(
                f"test command exited with {process.exit_code}"
                if test_command
                else f"command exited with {process.exit_code}"
            ),
            retryable=False,
            side_effect_state=(
                SideEffectState.APPLIED
                if test_command
                else SideEffectState.UNCERTAIN
            ),
        )
    return ToolResult.success(
        output,
        side_effect_state=SideEffectState.APPLIED,
        metadata={"command_digest": process.command_digest},
    ).with_updates(
        truncated=process.stdout_truncated or process.stderr_truncated,
    )

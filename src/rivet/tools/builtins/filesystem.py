from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import Field, model_validator

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
    CodeBlock,
    CodeSpan,
    ErrorKind,
    TextBlock,
    ToolResult,
    ToolResultStatus,
)
from rivet.workspace.command import ProcessRunner
from rivet.workspace.files import list_directory, read_text_file


class ListFilesArguments(ToolArguments):
    path: str = "."
    max_depth: int = Field(default=3, ge=1, le=8)
    max_entries: int = Field(default=500, ge=1, le=2_000)
    include_hidden: bool = False


class ReadFileArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=4_096)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_chars: int = Field(default=30_000, ge=1, le=100_000)
    max_bytes: int = Field(default=262_144, ge=1_024, le=2_097_152)

    @model_validator(mode="after")
    def validate_line_range(self) -> ReadFileArguments:
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class SearchTextArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=4_096)
    path: str = Field(default=".", max_length=4_096)
    glob: str | None = Field(default=None, max_length=1_024)
    max_results: int = Field(default=100, ge=1, le=500)
    max_output_chars: int = Field(default=50_000, ge=1_000, le=100_000)


class ListFilesTool:
    spec = ToolSpec(
        name="list_files",
        version="1.0.0",
        description="List files and directories inside the workspace without following symlinks.",
        input_model=ListFilesArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=10.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: ListFilesArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        normalized = arguments.model_dump(mode="json")
        normalized["path"] = target.relative_path
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = ListFilesArguments.model_validate(prepared.normalized_arguments)
        listing = list_directory(
            context.workspace,
            prepared.resolved_targets[0],
            max_depth=arguments.max_depth,
            max_entries=arguments.max_entries,
            include_hidden=arguments.include_hidden,
        )
        return ToolResult.success(
            TextBlock("\n".join(listing.entries)),
            metadata={
                "path": arguments.path,
                "entry_count": len(listing.entries),
                "truncated": listing.truncated,
            },
        ).with_updates(truncated=listing.truncated)


class ReadFileTool:
    spec = ToolSpec(
        name="read_file",
        version="1.0.0",
        description="Read a bounded UTF-8 line range from a workspace file.",
        input_model=ReadFileArguments,
        output_types=(CodeBlock, CodeSpan),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=10.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: ReadFileArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        normalized = arguments.model_dump(mode="json")
        normalized["path"] = target.relative_path
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = ReadFileArguments.model_validate(prepared.normalized_arguments)
        target = prepared.resolved_targets[0]
        read = read_text_file(
            context.workspace,
            target,
            start_line=arguments.start_line,
            end_line=arguments.end_line,
            max_chars=arguments.max_chars,
            max_bytes=arguments.max_bytes,
        )
        language = _language_for_path(target.path)
        span = CodeSpan(
            path=target.relative_path,
            start_line=read.start_line,
            end_line=read.end_line,
            text=read.text,
            sha256=read.sha256,
        )
        return ToolResult.success(
            CodeBlock(
                code=read.text,
                language=language,
                path=target.relative_path,
                start_line=read.start_line,
            ),
            metadata={
                "path": target.relative_path,
                "start_line": read.start_line,
                "end_line": read.end_line,
                "bytes_read": read.bytes_read,
                "encoding": read.encoding,
                "sha256": read.sha256,
                "hash_complete": read.hash_complete,
            },
        ).with_updates(
            truncated=read.truncated,
            code_spans=(span,),
        )


class SearchTextTool:
    spec = ToolSpec(
        name="search_text",
        version="1.0.0",
        description="Search workspace text with a fixed-string ripgrep pattern.",
        input_model=SearchTextArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=20.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: SearchTextArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        if "\x00" in arguments.query:
            raise ValueError("query cannot contain a null byte")
        if arguments.glob is not None and "\x00" in arguments.glob:
            raise ValueError("glob cannot contain a null byte")
        target = context.workspace.resolve(arguments.path)
        if not target.path.is_dir():
            raise ValueError(f"search path is not a directory: {arguments.path}")
        normalized = arguments.model_dump(mode="json")
        normalized["path"] = target.relative_path
        return ToolPreparation(
            normalized_arguments=normalized,
            resolved_targets=(target,),
        )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        arguments = SearchTextArguments.model_validate(prepared.normalized_arguments)
        rg = shutil.which("rg")
        if rg is None:
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                "ripgrep (rg) is not installed",
            )
        runner = context.services.get("process_runner")
        if runner is None:
            runner = ProcessRunner(context.workspace)
        if not isinstance(runner, ProcessRunner) and not hasattr(runner, "run"):
            raise TypeError("process_runner service must provide async run()")

        command = [
            rg,
            "--no-config",
            "--line-number",
            "--column",
            "--no-heading",
            "--color",
            "never",
            "--fixed-strings",
            "--max-filesize",
            "2M",
            "--glob",
            "!.git/**",
            "--glob",
            "!.rivet/**",
            "--glob",
            "!.venv/**",
            "--glob",
            "!node_modules/**",
            "-e",
            arguments.query,
        ]
        if arguments.glob:
            command.extend(["--glob", arguments.glob])
        command.extend(["--", str(prepared.resolved_targets[0].path)])
        process = await runner.run(
            command,
            cwd=context.workspace.root,
            timeout=prepared.timeout,
            max_stdout_bytes=arguments.max_output_chars * 4,
            max_stderr_bytes=20_000,
        )
        if process.timed_out:
            return ToolResult.error(
                ErrorKind.TOOL_TIMEOUT,
                f"ripgrep timed out after {prepared.timeout:g} seconds",
                retryable=True,
                status=ToolResultStatus.TIMED_OUT,
            )
        if process.exit_code not in {0, 1}:
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                process.stderr.strip() or "ripgrep failed",
            )

        rows = process.stdout.splitlines()
        result_truncated = process.stdout_truncated or len(rows) > arguments.max_results
        rows = rows[: arguments.max_results]
        root_prefix = f"{context.workspace.root.as_posix()}/"
        normalized_rows = [row.removeprefix(root_prefix) for row in rows]
        output = "\n".join(normalized_rows)
        if len(output) > arguments.max_output_chars:
            output = output[: arguments.max_output_chars]
            result_truncated = True
        return ToolResult.success(
            TextBlock(output),
            metadata={
                "matches": len(normalized_rows),
                "path": arguments.path,
                "query_length": len(arguments.query),
                "command_digest": process.command_digest,
            },
        ).with_updates(truncated=result_truncated)


def _language_for_path(path: Path) -> str | None:
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(path.suffix.lower())

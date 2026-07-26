from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from rivet.safety.workspace import WorkspaceBoundary
from rivet.tools.base import ToolResult, ToolSpec

_IGNORED_DIRECTORIES = {".git", ".rivet", ".venv", "__pycache__", "node_modules"}


@dataclass
class ListFilesTool:
    workspace: WorkspaceBoundary

    spec = ToolSpec(
        name="list_files",
        description="List files and directories inside the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative directory."},
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth below path, from 1 to 8.",
                },
            },
            "additionalProperties": False,
        },
    )

    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        directory = self.workspace.resolve(arguments.get("path", "."))
        if not directory.is_dir():
            return ToolResult(ok=False, error=f"not a directory: {arguments.get('path', '.')}")
        max_depth = max(1, min(int(arguments.get("max_depth", 3)), 8))
        rows: list[str] = []
        base_parts = len(directory.parts)
        for entry in sorted(directory.rglob("*")):
            relative_depth = len(entry.parts) - base_parts
            if relative_depth > max_depth:
                continue
            if any(part in _IGNORED_DIRECTORIES for part in entry.relative_to(directory).parts):
                continue
            label = self.workspace.display(entry)
            rows.append(f"{label}/" if entry.is_dir() else label)
            if len(rows) >= 500:
                rows.append("... truncated after 500 entries")
                break
        return ToolResult(ok=True, output="\n".join(rows), metadata={"path": str(directory)})


@dataclass
class ReadFileTool:
    workspace: WorkspaceBoundary

    spec = ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file inside the workspace with optional line bounds.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "max_chars": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.workspace.resolve(arguments["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"not a file: {arguments['path']}")
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            return ToolResult(ok=False, error="binary files are not supported")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(arguments.get("start_line", 1)))
        end = min(len(lines), int(arguments.get("end_line", len(lines))))
        if end < start:
            return ToolResult(
                ok=False,
                error="end_line must be greater than or equal to start_line",
            )
        selected = "\n".join(
            f"{number}: {lines[number - 1]}" for number in range(start, end + 1)
        )
        max_chars = max(1000, min(int(arguments.get("max_chars", 30000)), 100000))
        truncated = len(selected) > max_chars
        if truncated:
            selected = selected[:max_chars] + "\n... truncated"
        return ToolResult(
            ok=True,
            output=selected,
            metadata={
                "path": self.workspace.display(path),
                "start_line": start,
                "end_line": end,
                "truncated": truncated,
            },
        )


@dataclass
class SearchTextTool:
    workspace: WorkspaceBoundary

    spec = ToolSpec(
        name="search_text",
        description="Search workspace text with ripgrep and return matching file locations.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        if not shutil.which("rg"):
            return ToolResult(ok=False, error="ripgrep (rg) is not installed")
        search_root = self.workspace.resolve(arguments.get("path", "."))
        max_results = max(1, min(int(arguments.get("max_results", 100)), 500))
        command = [
            "rg",
            "--no-config",
            "--line-number",
            "--column",
            "--no-heading",
            "--color",
            "never",
            "--fixed-strings",
            "--max-count",
            str(max_results),
            "-e",
            arguments["query"],
        ]
        glob = arguments.get("glob")
        if glob:
            command.extend(["--glob", glob])
        command.extend(["--", str(search_root)])
        completed = subprocess.run(
            command,
            cwd=self.workspace.root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode not in {0, 1}:
            return ToolResult(ok=False, error=completed.stderr.strip() or "ripgrep failed")
        rows: list[str] = []
        for raw_line in completed.stdout.splitlines()[:max_results]:
            prefix = f"{self.workspace.root.as_posix()}/"
            rows.append(raw_line.removeprefix(prefix))
        return ToolResult(
            ok=True,
            output="\n".join(rows),
            metadata={"matches": len(rows), "truncated": len(rows) >= max_results},
        )


def register_filesystem_tools(
    registry: Any,
    workspace: WorkspaceBoundary,
) -> None:
    registry.register(ListFilesTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(SearchTextTool(workspace))

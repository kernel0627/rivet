from __future__ import annotations

import json
from collections.abc import Mapping

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from rivet.domain import Event

_READ_TOOLS = {
    "list_files",
    "read_file",
    "git_status",
    "git_diff",
    "python_outline",
    "find_python_symbol",
    "read_python_symbol",
    "find_python_imports",
}


class TerminalEventRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._streaming_text = False

    async def __call__(self, event: Event) -> None:
        if event.event_type == "turn.started":
            self._print_stage(
                "Plan",
                f"Turn {event.payload.get('ordinal', '?')}",
                style="dim",
            )
        elif event.event_type == "model_call.started":
            self._print_stage("Plan", "Thinking…", style="cyan")
        elif event.event_type == "model.stream.text.delta":
            raw = event.payload.get("model_event", {})
            delta = raw.get("text_delta") if isinstance(raw, Mapping) else None
            if isinstance(delta, str) and delta:
                self.console.print(delta, end="", markup=False)
                self._streaming_text = True
        elif event.event_type == "model.stream.response.completed":
            raw = event.payload.get("model_event", {})
            text = raw.get("text") if isinstance(raw, Mapping) else None
            if self._streaming_text:
                self.console.print()
            elif isinstance(text, str) and text:
                self.console.print(text, markup=False)
            self._streaming_text = False
        elif event.event_type == "tool.started":
            tool_name = str(event.payload.get("tool_name", "unknown"))
            self._print_stage(
                _tool_stage(tool_name),
                tool_name,
                style="yellow",
            )
        elif event.event_type == "tool.completed":
            status = event.payload.get("status", "unknown")
            style = "green" if status == "success" else "red"
            tool_name = str(event.payload.get("tool_name", "unknown"))
            self._print_stage(
                _tool_stage(tool_name),
                f"{status}: {tool_name}",
                style=style,
            )
            changed_paths = _string_list(event.payload.get("changed_paths"))
            if changed_paths:
                self.console.print(
                    "Changed: " + ", ".join(changed_paths),
                    style="green",
                    markup=False,
                )
            diff = _event_diff(event.payload)
            if diff:
                self.console.print(Syntax(diff, "diff", word_wrap=True))
        elif event.event_type == "checkpoint.created":
            checkpoint_id = event.payload.get("checkpoint_id", "unknown")
            self._print_stage(
                "Edit",
                f"Checkpoint created: {checkpoint_id}",
                style="green",
            )
        elif event.event_type == "checkpoint.rewound":
            paths = _string_list(event.payload.get("restored_paths")) + _string_list(
                event.payload.get("removed_paths")
            )
            self._print_stage(
                "Edit",
                "Rewound: " + (", ".join(paths) if paths else "no paths"),
                style="yellow",
            )
        elif event.event_type == "verification.started":
            self._print_stage("Test", "Verifying changes…", style="magenta")
        elif event.event_type == "verification.completed":
            self._print_stage(
                "Test",
                f"Verification: {event.payload.get('status', 'unknown')}",
                style="magenta",
            )
        elif event.event_type == "run.resumed":
            self._print_stage("Continue", "Run resumed", style="cyan")
        elif event.event_type == "permission.scope_granted":
            self._print_stage(
                "Continue",
                "Allowed for this run: " + str(event.payload.get("permission_class", "unknown")),
                style="cyan",
            )
        elif event.event_type == "run.paused":
            self._print_stage(
                "Continue",
                f"Paused: {event.payload.get('reason', 'unknown')}",
                style="yellow",
            )
        elif event.event_type == "run.failed":
            self._print_stage(
                "Result",
                f"Failed: {event.payload.get('reason', 'unknown')}",
                style="red",
            )
        elif event.event_type == "run.cancelled":
            self._print_stage(
                "Result",
                f"Cancelled: {event.payload.get('reason', 'unknown')}",
                style="red",
            )
        elif event.event_type == "run.completed":
            self._print_stage("Result", "Completed", style="green")

    def _print_stage(self, stage: str, message: str, *, style: str) -> None:
        self.console.print(
            Text.assemble((f"[{stage}] ", style), message),
        )


def _tool_stage(tool_name: str) -> str:
    if tool_name == "search_text":
        return "Search"
    if tool_name in _READ_TOOLS:
        return "Read"
    if tool_name == "apply_patch":
        return "Edit"
    if tool_name == "run_tests":
        return "Test"
    return "Tool"


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _event_diff(payload: Mapping[str, object]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(result, Mapping):
        return None
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        diff = block.get("diff")
        if isinstance(diff, str) and diff:
            return diff
    return None

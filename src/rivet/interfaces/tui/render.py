from __future__ import annotations

from collections.abc import Mapping

from rich.console import Console

from rivet.domain import Event


class TerminalEventRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._streaming_text = False

    async def __call__(self, event: Event) -> None:
        if event.event_type == "turn.started":
            self.console.print(
                f"[dim]Turn {event.payload.get('ordinal', '?')}[/dim]"
            )
        elif event.event_type == "model_call.started":
            self.console.print("[cyan]Thinking…[/cyan]")
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
            self.console.print(
                f"[yellow]Tool:[/yellow] {event.payload.get('tool_name', 'unknown')}"
            )
        elif event.event_type == "tool.completed":
            status = event.payload.get("status", "unknown")
            style = "green" if status == "success" else "red"
            self.console.print(
                f"[{style}]Tool {status}:[/{style}] "
                f"{event.payload.get('tool_name', 'unknown')}"
            )
        elif event.event_type == "verification.started":
            self.console.print("[magenta]Verifying changes…[/magenta]")
        elif event.event_type == "verification.completed":
            self.console.print(
                f"[magenta]Verification:[/magenta] "
                f"{event.payload.get('status', 'unknown')}"
            )
        elif event.event_type == "run.paused":
            self.console.print(
                f"[yellow]Paused:[/yellow] {event.payload.get('reason', 'unknown')}"
            )
        elif event.event_type == "run.failed":
            self.console.print(
                f"[red]Failed:[/red] {event.payload.get('reason', 'unknown')}"
            )

from __future__ import annotations

from dataclasses import dataclass, replace

from rivet.tools.results import (
    CodeBlock,
    CodeSpan,
    CommandOutput,
    ContentBlock,
    DiffBlock,
    RetrievedChunk,
    TextBlock,
    ToolResult,
)


@dataclass(frozen=True)
class OutputBudget:
    max_chars: int = 100_000
    max_blocks: int = 64

    def __post_init__(self) -> None:
        if self.max_chars < 1:
            raise ValueError("max_chars must be positive")
        if self.max_blocks < 1:
            raise ValueError("max_blocks must be positive")


class OutputBudgetLimiter:
    def __init__(self, budget: OutputBudget | None = None) -> None:
        self.budget = budget or OutputBudget()

    def apply(self, result: ToolResult) -> ToolResult:
        remaining = self.budget.max_chars
        error_message = result.error_message
        if error_message is not None:
            error_message, error_truncated = _slice(error_message, remaining)
            remaining -= len(error_message)
        else:
            error_truncated = False
        blocks: list[ContentBlock] = []
        truncated = result.truncated or error_truncated
        for index, block in enumerate(result.content):
            if index >= self.budget.max_blocks:
                truncated = True
                break
            limited, consumed, block_truncated = _limit_block(block, remaining)
            blocks.append(limited)
            remaining -= consumed
            truncated = truncated or block_truncated
            if remaining <= 0:
                if index + 1 < len(result.content):
                    truncated = True
                break
        code_spans = []
        for span in result.code_spans:
            text, span_truncated = _slice(span.text, remaining)
            code_spans.append(replace(span, text=text))
            remaining -= len(text)
            truncated = truncated or span_truncated
        diagnostics = []
        for diagnostic in result.diagnostics:
            message, diagnostic_truncated = _slice(diagnostic.message, remaining)
            diagnostics.append(replace(diagnostic, message=message))
            remaining -= len(message)
            truncated = truncated or diagnostic_truncated
        return result.with_updates(
            content=tuple(blocks),
            error_message=error_message,
            code_spans=tuple(code_spans),
            diagnostics=tuple(diagnostics),
            truncated=truncated,
        )


def _limit_block(
    block: ContentBlock,
    remaining: int,
) -> tuple[ContentBlock, int, bool]:
    if isinstance(block, TextBlock):
        text, truncated = _slice(block.text, remaining)
        return replace(block, text=text), len(text), truncated
    if isinstance(block, CodeBlock):
        code, truncated = _slice(block.code, remaining)
        return replace(block, code=code), len(code), truncated
    if isinstance(block, CodeSpan):
        text, truncated = _slice(block.text, remaining)
        return replace(block, text=text), len(text), truncated
    if isinstance(block, DiffBlock):
        diff, truncated = _slice(block.diff, remaining)
        return replace(block, diff=diff), len(diff), truncated
    if isinstance(block, RetrievedChunk):
        text, truncated = _slice(block.text, remaining)
        return replace(block, text=text), len(text), truncated
    if isinstance(block, CommandOutput):
        stdout, stdout_truncated = _slice(block.stdout, remaining)
        remaining_after_stdout = max(0, remaining - len(stdout))
        stderr, stderr_truncated = _slice(block.stderr, remaining_after_stdout)
        limited = replace(
            block,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=block.stdout_truncated or stdout_truncated,
            stderr_truncated=block.stderr_truncated or stderr_truncated,
        )
        return (
            limited,
            len(stdout) + len(stderr),
            stdout_truncated or stderr_truncated,
        )
    rendered = repr(block)
    return block, min(len(rendered), remaining), len(rendered) > remaining


def _slice(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[: max(0, limit)], True

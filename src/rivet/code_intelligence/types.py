from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    ASYNC_METHOD = "async_method"


@dataclass(frozen=True)
class CodeSpan:
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    kind: str | None = None
    source: str = "ast"
    score: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError("start_line must be at least 1")
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")


@dataclass(frozen=True)
class SymbolInfo:
    name: str
    qualified_name: str
    kind: SymbolKind
    file_path: str
    start_line: int
    end_line: int
    parent: str | None
    signature: str
    docstring: str | None
    content_hash: str


@dataclass(frozen=True)
class ImportInfo:
    module: str | None
    names: tuple[str, ...]
    level: int
    line: int


@dataclass(frozen=True)
class ReferenceInfo:
    name: str
    line: int
    column: int
    context: str


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    workspace_id: str
    index_version: str
    file_path: str
    language: str
    kind: str
    content: str
    content_hash: str
    start_line: int
    end_line: int
    symbol: str | None = None
    qualified_name: str | None = None
    parent: str | None = None
    imports: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: CodeChunk
    score: float
    source: str
    rank: int
    component_scores: dict[str, float] = field(default_factory=dict)

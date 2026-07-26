from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from rivet.code_intelligence.types import (
    CodeChunk,
    CodeSpan,
    ImportInfo,
    ReferenceInfo,
    SymbolInfo,
    SymbolKind,
)


class PythonAnalysisError(ValueError):
    def __init__(self, path: str, message: str, *, line: int | None = None) -> None:
        self.path = path
        self.line = line
        location = f"{path}:{line}" if line is not None else path
        super().__init__(f"{location}: {message}")


@dataclass(frozen=True)
class PythonAnalysis:
    file_path: str
    source_hash: str
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    references: tuple[ReferenceInfo, ...]

    def find_symbols(self, query: str) -> tuple[SymbolInfo, ...]:
        normalized = query.casefold()
        return tuple(
            symbol
            for symbol in self.symbols
            if normalized in symbol.name.casefold()
            or normalized in symbol.qualified_name.casefold()
        )


class PythonAstAnalyzer:
    def analyze(self, source: str, *, file_path: str) -> PythonAnalysis:
        try:
            tree = ast.parse(source, filename=file_path, type_comments=True)
        except SyntaxError as exc:
            raise PythonAnalysisError(
                file_path,
                exc.msg,
                line=exc.lineno,
            ) from exc

        source_lines = source.splitlines()
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        visitor = _AnalysisVisitor(
            file_path=file_path,
            source=source,
            source_lines=source_lines,
        )
        visitor.visit(tree)
        return PythonAnalysis(
            file_path=file_path,
            source_hash=source_hash,
            symbols=tuple(visitor.symbols),
            imports=tuple(visitor.imports),
            references=tuple(visitor.references),
        )

    def analyze_file(self, path: Path, *, display_path: str | None = None) -> PythonAnalysis:
        source = path.read_text(encoding="utf-8")
        return self.analyze(source, file_path=display_path or path.as_posix())

    def read_symbol(
        self,
        source: str,
        symbol: SymbolInfo,
    ) -> CodeSpan:
        lines = source.splitlines()
        content = "\n".join(lines[symbol.start_line - 1 : symbol.end_line])
        return CodeSpan(
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            content=content,
            symbol=symbol.qualified_name,
            kind=symbol.kind.value,
        )

    def chunks(
        self,
        source: str,
        *,
        file_path: str,
        workspace_id: str,
        index_version: str,
        include_module: bool = True,
    ) -> tuple[CodeChunk, ...]:
        analysis = self.analyze(source, file_path=file_path)
        imports = tuple(
            (
                ("." * item.level)
                + (item.module or "")
                + (":" + ",".join(item.names) if item.names else "")
            )
            for item in analysis.imports
        )
        chunks: list[CodeChunk] = []
        if include_module and source.strip():
            chunks.append(
                self._chunk(
                    workspace_id=workspace_id,
                    index_version=index_version,
                    file_path=file_path,
                    kind=SymbolKind.MODULE.value,
                    content=source,
                    start_line=1,
                    end_line=max(1, len(source.splitlines())),
                    imports=imports,
                )
            )
        for symbol in analysis.symbols:
            span = self.read_symbol(source, symbol)
            chunks.append(
                self._chunk(
                    workspace_id=workspace_id,
                    index_version=index_version,
                    file_path=file_path,
                    kind=symbol.kind.value,
                    content=span.content,
                    start_line=span.start_line,
                    end_line=span.end_line,
                    symbol=symbol.name,
                    qualified_name=symbol.qualified_name,
                    parent=symbol.parent,
                    imports=imports,
                )
            )
        return tuple(chunks)

    @staticmethod
    def _chunk(
        *,
        workspace_id: str,
        index_version: str,
        file_path: str,
        kind: str,
        content: str,
        start_line: int,
        end_line: int,
        symbol: str | None = None,
        qualified_name: str | None = None,
        parent: str | None = None,
        imports: tuple[str, ...] = (),
    ) -> CodeChunk:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        identity = (
            f"{workspace_id}\0{index_version}\0{file_path}\0"
            f"{start_line}\0{end_line}\0{content_hash}"
        )
        chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return CodeChunk(
            chunk_id=chunk_id,
            workspace_id=workspace_id,
            index_version=index_version,
            file_path=file_path,
            language="python",
            kind=kind,
            content=content,
            content_hash=content_hash,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            qualified_name=qualified_name,
            parent=parent,
            imports=imports,
        )


class _AnalysisVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        source_lines: list[str],
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.source_lines = source_lines
        self.parents: list[str] = []
        self.class_depth = 0
        self.symbols: list[SymbolInfo] = []
        self.imports: list[ImportInfo] = []
        self.references: list[ReferenceInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node, SymbolKind.CLASS)
        self.parents.append(node.name)
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = SymbolKind.METHOD if self.class_depth else SymbolKind.FUNCTION
        self._visit_function(node, kind)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = (
            SymbolKind.ASYNC_METHOD
            if self.class_depth
            else SymbolKind.ASYNC_FUNCTION
        )
        self._visit_function(node, kind)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        kind: SymbolKind,
    ) -> None:
        self._add_symbol(node, kind)
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(
            ImportInfo(
                module=None,
                names=tuple(alias.name for alias in node.names),
                level=0,
                line=node.lineno,
            )
        )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(
            ImportInfo(
                module=node.module,
                names=tuple(alias.name for alias in node.names),
                level=node.level,
                line=node.lineno,
            )
        )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append(
                ReferenceInfo(
                    name=node.id,
                    line=node.lineno,
                    column=node.col_offset,
                    context="load",
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append(
                ReferenceInfo(
                    name=node.attr,
                    line=node.lineno,
                    column=node.col_offset,
                    context="attribute",
                )
            )
        self.generic_visit(node)

    def _add_symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: SymbolKind,
    ) -> None:
        parent = ".".join(self.parents) or None
        qualified_name = ".".join((*self.parents, node.name))
        end_line = node.end_lineno or node.lineno
        content = "\n".join(self.source_lines[node.lineno - 1 : end_line])
        self.symbols.append(
            SymbolInfo(
                name=node.name,
                qualified_name=qualified_name,
                kind=kind,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=end_line,
                parent=parent,
                signature=_signature(node),
                docstring=ast.get_docstring(node, clean=True),
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )


def _signature(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    arguments = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({arguments}){returns}"


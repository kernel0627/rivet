from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from rivet.code_intelligence.lsp import LspManager
from rivet.code_intelligence.python_ast import PythonAnalysisError, PythonAstAnalyzer
from rivet.code_intelligence.types import SymbolInfo
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
    CodeSpan,
    Diagnostic,
    ErrorKind,
    RetrievedChunk,
    TextBlock,
    ToolResult,
)


class PythonPathArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=4_096)


class FindPythonSymbolArguments(PythonPathArguments):
    query: str = Field(min_length=1, max_length=512)


class ReadPythonSymbolArguments(PythonPathArguments):
    qualified_name: str = Field(min_length=1, max_length=1_024)


class RetrieveCodeArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=4_096)
    limit: int = Field(default=8, ge=1, le=50)


class LspPositionArguments(PythonPathArguments):
    line: int = Field(ge=1)
    character: int = Field(default=0, ge=0)


class LspReferencesArguments(LspPositionArguments):
    include_declaration: bool = True


class LspWorkspaceSymbolsArguments(ToolArguments):
    query: str = Field(default="", max_length=1_024)


class IndexArguments(ToolArguments):
    pass


class PythonOutlineTool:
    spec = ToolSpec(
        name="python_outline",
        version="1.0.0",
        description="Return Python symbols, signatures, ranges, and imports for one file.",
        input_model=PythonPathArguments,
        output_types=(TextBlock, CodeSpan, Diagnostic),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=15.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: PythonPathArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        return ToolPreparation(
            normalized_arguments={"path": target.relative_path},
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        analysis, source = _analysis(prepared, context)
        lines = [_symbol_summary(symbol) for symbol in analysis.symbols]
        imports = [
            {
                "module": item.module,
                "names": list(item.names),
                "level": item.level,
                "line": item.line,
            }
            for item in analysis.imports
        ]
        spans = tuple(_symbol_span(source, symbol) for symbol in analysis.symbols)
        return ToolResult.success(
            TextBlock(
                json.dumps(
                    {"symbols": lines, "imports": imports},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            *spans,
            metadata={
                "path": analysis.file_path,
                "source_hash": analysis.source_hash,
                "symbol_count": len(analysis.symbols),
                "import_count": len(analysis.imports),
            },
        ).with_updates(code_spans=spans)


class FindPythonSymbolTool(PythonOutlineTool):
    spec = ToolSpec(
        name="find_python_symbol",
        version="1.0.0",
        description="Find Python symbols by name in one file with stable line spans.",
        input_model=FindPythonSymbolArguments,
        output_types=(CodeSpan, TextBlock, Diagnostic),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=15.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: FindPythonSymbolArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        return ToolPreparation(
            normalized_arguments={
                "path": target.relative_path,
                "query": arguments.query.strip(),
            },
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        analysis, source = _analysis(prepared, context)
        query = str(prepared.normalized_arguments["query"])
        matches = analysis.find_symbols(query)
        if not matches:
            return ToolResult.success(
                TextBlock(f"no Python symbols matched {query!r}"),
                metadata={"matches": 0, "path": analysis.file_path},
            )
        spans = tuple(_symbol_span(source, symbol) for symbol in matches)
        return ToolResult.success(
            *spans,
            metadata={"matches": len(matches), "path": analysis.file_path},
        ).with_updates(code_spans=spans)


class ReadPythonSymbolTool(PythonOutlineTool):
    spec = ToolSpec(
        name="read_python_symbol",
        version="1.0.0",
        description="Read one Python symbol by exact qualified name.",
        input_model=ReadPythonSymbolArguments,
        output_types=(CodeSpan, Diagnostic),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=15.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: ReadPythonSymbolArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
        return ToolPreparation(
            normalized_arguments={
                "path": target.relative_path,
                "qualified_name": arguments.qualified_name.strip(),
            },
            resolved_targets=(target,),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        analysis, source = _analysis(prepared, context)
        qualified_name = str(prepared.normalized_arguments["qualified_name"])
        match = next(
            (symbol for symbol in analysis.symbols if symbol.qualified_name == qualified_name),
            None,
        )
        if match is None:
            return ToolResult.error(
                ErrorKind.TOOL_ARGUMENT_ERROR,
                f"Python symbol was not found: {qualified_name}",
            )
        span = _symbol_span(source, match)
        return ToolResult.success(
            span,
            metadata={"path": analysis.file_path, "qualified_name": qualified_name},
        ).with_updates(code_spans=(span,))


class FindPythonImportsTool(PythonOutlineTool):
    spec = ToolSpec(
        name="find_python_imports",
        version="1.0.0",
        description="Return structured imports from one Python file.",
        input_model=PythonPathArguments,
        output_types=(TextBlock, Diagnostic),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=15.0,
        idempotent=True,
        parallel_safe=True,
    )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        analysis, _source = _analysis(prepared, context)
        payload = [
            {
                "module": item.module,
                "names": list(item.names),
                "level": item.level,
                "line": item.line,
            }
            for item in analysis.imports
        ]
        return ToolResult.success(
            TextBlock(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            metadata={"path": analysis.file_path, "count": len(payload)},
        )


class RetrieveCodeTool:
    spec = ToolSpec(
        name="retrieve_code",
        version="1.0.0",
        description="Retrieve relevant indexed code using the configured hybrid retriever.",
        input_model=RetrieveCodeArguments,
        output_types=(RetrievedChunk, TextBlock),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=20.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: RetrieveCodeArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        return ToolPreparation(
            normalized_arguments=arguments.model_dump(mode="json"),
            resolved_targets=(context.workspace.resolve("."),),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        retriever = context.services.get("retriever")
        if retriever is None or not hasattr(retriever, "search"):
            return ToolResult.error(
                ErrorKind.TOOL_EXECUTION_ERROR,
                "code retriever is not configured",
            )
        arguments = RetrieveCodeArguments.model_validate(prepared.normalized_arguments)
        rows = retriever.search(arguments.query, limit=arguments.limit)
        blocks = tuple(
            RetrievedChunk(
                chunk_id=row.chunk.chunk_id,
                path=row.chunk.file_path,
                text=row.chunk.content,
                start_line=row.chunk.start_line,
                end_line=row.chunk.end_line,
                score=row.score,
            )
            for row in rows
        )
        return ToolResult.success(
            *blocks,
            metadata={"query": arguments.query, "matches": len(blocks)},
        )


class IndexStatusTool:
    spec = ToolSpec(
        name="index_status",
        version="1.0.0",
        description="Return the most recent workspace code-index refresh report.",
        input_model=IndexArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=10.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: IndexArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        return ToolPreparation(
            normalized_arguments={},
            resolved_targets=(context.workspace.resolve("."),),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        indexer = _require_indexer(context)
        report = indexer.last_report
        if report is None:
            return ToolResult.success(
                TextBlock("workspace index has not been refreshed"),
                metadata={"initialized": False},
            )
        payload = {
            "scanned_files": report.scanned_files,
            "indexed_files": report.indexed_files,
            "unchanged_files": report.unchanged_files,
            "deleted_files": report.deleted_files,
            "failed_files": list(report.failed_files),
            "index_version": report.index_version,
        }
        return ToolResult.success(
            TextBlock(
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
            metadata={"initialized": True, **payload},
        )


class RefreshIndexTool(IndexStatusTool):
    spec = ToolSpec(
        name="refresh_index",
        version="1.0.0",
        description="Refresh the incremental workspace code index and return its report.",
        input_model=IndexArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=60.0,
        idempotent=True,
        parallel_safe=False,
    )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        indexer = _require_indexer(context)
        indexer.refresh()
        return super().execute(prepared, context)


class _LspPositionTool:
    method_name = ""
    spec: ToolSpec

    def prepare(
        self,
        arguments: LspPositionArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        target = context.workspace.resolve(arguments.path)
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
        manager, path, arguments = await _lsp_context(prepared, context)
        service, target = await manager.sync_document(
            path,
            workspace_revision=context.workspace.revision(*prepared.resolved_targets),
        )
        method = getattr(service, self.method_name)
        kwargs: dict[str, Any] = {
            "line": arguments.line - 1,
            "character": arguments.character,
        }
        if isinstance(arguments, LspReferencesArguments):
            kwargs["include_declaration"] = arguments.include_declaration
        value = await method(target, **kwargs)
        if self.method_name in {"definition", "references"}:
            spans = tuple(
                CodeSpan(
                    path=item.path,
                    start_line=item.range.start.line + 1,
                    end_line=max(
                        item.range.start.line + 1,
                        item.range.end.line + 1,
                    ),
                    text="",
                )
                for item in value
            )
            return ToolResult.success(
                *spans,
                metadata={"method": self.method_name, "matches": len(spans)},
            ).with_updates(code_spans=spans)
        return ToolResult.success(
            TextBlock(json.dumps(value, ensure_ascii=False, default=str)),
            metadata={"method": self.method_name},
        )


class LspDefinitionTool(_LspPositionTool):
    method_name = "definition"
    spec = ToolSpec(
        name="lsp_definition",
        version="1.0.0",
        description="Resolve the definition at a one-based source line.",
        input_model=LspPositionArguments,
        output_types=(CodeSpan,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=30.0,
        idempotent=True,
        parallel_safe=True,
    )


class LspReferencesTool(_LspPositionTool):
    method_name = "references"
    spec = ToolSpec(
        name="lsp_references",
        version="1.0.0",
        description="Find references at a one-based source line.",
        input_model=LspReferencesArguments,
        output_types=(CodeSpan,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=30.0,
        idempotent=True,
        parallel_safe=True,
    )


class LspHoverTool(_LspPositionTool):
    method_name = "hover"
    spec = ToolSpec(
        name="lsp_hover",
        version="1.0.0",
        description="Return hover information at a one-based source line.",
        input_model=LspPositionArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=30.0,
        idempotent=True,
        parallel_safe=True,
    )


class LspDiagnosticsTool(PythonOutlineTool):
    spec = ToolSpec(
        name="lsp_diagnostics",
        version="1.0.0",
        description="Return cached language-server diagnostics for one file.",
        input_model=PythonPathArguments,
        output_types=(Diagnostic, TextBlock),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=30.0,
        idempotent=True,
        parallel_safe=True,
    )

    async def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        manager = _require_lsp_manager(context)
        path = prepared.resolved_targets[0].path
        service, target = await manager.sync_document(
            path,
            workspace_revision=context.workspace.revision(*prepared.resolved_targets),
        )
        diagnostics = tuple(
            Diagnostic(
                severity=_lsp_severity(item.severity),
                message=item.message,
                path=prepared.resolved_targets[0].relative_path,
                line=item.range.start.line + 1,
                column=item.range.start.character,
                code=str(item.code) if item.code is not None else None,
            )
            for item in service.diagnostics(target)
        )
        if diagnostics:
            return ToolResult.success(
                *diagnostics,
                metadata={"count": len(diagnostics)},
            ).with_updates(diagnostics=diagnostics)
        return ToolResult.success(
            TextBlock("no language-server diagnostics"),
            metadata={"count": 0},
        )


def code_intelligence_tools() -> tuple[Any, ...]:
    return (
        PythonOutlineTool(),
        FindPythonSymbolTool(),
        ReadPythonSymbolTool(),
        FindPythonImportsTool(),
        RetrieveCodeTool(),
        IndexStatusTool(),
        RefreshIndexTool(),
        LspDefinitionTool(),
        LspReferencesTool(),
        LspHoverTool(),
        LspDiagnosticsTool(),
    )


def _require_indexer(context: ToolExecutionContext) -> Any:
    indexer = context.services.get("workspace_indexer")
    if (
        indexer is None
        or not hasattr(indexer, "refresh")
        or not hasattr(indexer, "last_report")
    ):
        raise RuntimeError("workspace code indexer is not configured")
    return indexer


def _analysis(
    prepared: PreparedTool,
    context: ToolExecutionContext,
) -> tuple[Any, str]:
    target = context.workspace.revalidate(prepared.resolved_targets[0])
    if target.path.suffix.casefold() not in {".py", ".pyi"}:
        raise PythonAnalysisError(target.relative_path, "file is not Python source")
    source = target.path.read_text(encoding="utf-8")
    analyzer = context.services.get("python_ast_analyzer") or PythonAstAnalyzer()
    if not isinstance(analyzer, PythonAstAnalyzer) and not hasattr(analyzer, "analyze"):
        raise TypeError("python_ast_analyzer service must provide analyze()")
    return (
        analyzer.analyze(source, file_path=target.relative_path),
        source,
    )


def _symbol_summary(symbol: SymbolInfo) -> dict[str, Any]:
    return {
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind.value,
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
        "signature": symbol.signature,
        "parent": symbol.parent,
        "docstring": symbol.docstring,
    }


def _symbol_span(source: str, symbol: SymbolInfo) -> CodeSpan:
    lines = source.splitlines()
    return CodeSpan(
        path=symbol.file_path,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        text="\n".join(lines[symbol.start_line - 1 : symbol.end_line]),
        sha256=symbol.content_hash,
    )


async def _lsp_context(
    prepared: PreparedTool,
    context: ToolExecutionContext,
) -> tuple[LspManager, Path, LspPositionArguments]:
    manager = _require_lsp_manager(context)
    model = LspReferencesArguments if prepared.name == "lsp_references" else LspPositionArguments
    arguments = model.model_validate(prepared.normalized_arguments)
    return manager, prepared.resolved_targets[0].path, arguments


def _require_lsp_manager(context: ToolExecutionContext) -> LspManager:
    manager = context.services.get("lsp_manager")
    if manager is None or not hasattr(manager, "sync_document"):
        raise RuntimeError("LSP manager is not configured")
    return manager


def _lsp_severity(value: int | None) -> str:
    return {1: "error", 2: "warning", 3: "information", 4: "hint"}.get(
        value,
        "unknown",
    )

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from rivet.code_intelligence.lsp.types import LspDiagnostic, LspLocation


@runtime_checkable
class LanguageService(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def open_document(
        self,
        path: Path,
        *,
        text: str,
        language_id: str = "python",
        version: int = 1,
    ) -> None: ...

    async def change_document(
        self,
        path: Path,
        *,
        text: str,
        version: int,
    ) -> None: ...

    async def close_document(self, path: Path) -> None: ...

    async def definition(
        self,
        path: Path,
        *,
        line: int,
        character: int,
    ) -> tuple[LspLocation, ...]: ...

    async def references(
        self,
        path: Path,
        *,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> tuple[LspLocation, ...]: ...

    async def hover(self, path: Path, *, line: int, character: int) -> Any: ...

    async def document_symbols(self, path: Path) -> Any: ...

    async def workspace_symbols(self, query: str) -> Any: ...

    def diagnostics(self, path: Path) -> tuple[LspDiagnostic, ...]: ...


LanguageServiceFactory = type[LanguageService]

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rivet.code_intelligence.lsp.client import LspClient, LspError
from rivet.code_intelligence.lsp.language import LanguageService


@dataclass(frozen=True, slots=True)
class LanguageServerConfig:
    language_id: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]
    request_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.language_id or not self.command or not self.extensions:
            raise ValueError("language server config fields cannot be empty")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if any(not suffix.startswith(".") for suffix in self.extensions):
            raise ValueError("language server extensions must start with '.'")


@dataclass
class _ManagedService:
    service: LanguageService
    config: LanguageServerConfig
    document_versions: dict[Path, int] = field(default_factory=dict)
    document_hashes: dict[Path, str] = field(default_factory=dict)
    workspace_revision: str | None = None


class LspManager:
    """Own one long-lived language service per configured workspace language."""

    def __init__(
        self,
        workspace_root: Path,
        configs: Sequence[LanguageServerConfig],
        *,
        client_factory: Callable[[LanguageServerConfig, Path], LanguageService] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self._configs = {config.language_id: config for config in configs}
        if len(self._configs) != len(tuple(configs)):
            raise ValueError("language_id values must be unique")
        self._client_factory = client_factory or _default_client_factory
        self._services: dict[str, _ManagedService] = {}

    async def service(self, language_id: str) -> LanguageService:
        managed = self._services.get(language_id)
        if managed is not None:
            return managed.service
        config = self._configs.get(language_id)
        if config is None:
            raise LspError(f"no language server configured for {language_id}")
        service = self._client_factory(config, self.workspace_root)
        await service.start()
        self._services[language_id] = _ManagedService(service=service, config=config)
        return service

    async def sync_document(
        self,
        path: Path,
        *,
        text: str | None = None,
        language_id: str | None = None,
        workspace_revision: str | None = None,
    ) -> tuple[LanguageService, Path]:
        target = self._resolve_workspace_file(path)
        selected_language = language_id or self.language_for_path(target)
        managed = await self._managed(selected_language)
        content = target.read_text(encoding="utf-8") if text is None else text
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if managed.document_hashes.get(target) == digest:
            managed.workspace_revision = workspace_revision
            return managed.service, target
        previous_version = managed.document_versions.get(target)
        version = 1 if previous_version is None else previous_version + 1
        if previous_version is None:
            await managed.service.open_document(
                target,
                text=content,
                language_id=selected_language,
                version=version,
            )
        else:
            await managed.service.change_document(target, text=content, version=version)
        managed.document_versions[target] = version
        managed.document_hashes[target] = digest
        managed.workspace_revision = workspace_revision
        return managed.service, target

    async def restart(self, language_id: str) -> LanguageService:
        managed = self._services.pop(language_id, None)
        if managed is not None:
            await managed.service.close()
        return await self.service(language_id)

    async def close(self) -> None:
        services = list(self._services.values())
        self._services.clear()
        for managed in services:
            await managed.service.close()

    def language_for_path(self, path: Path) -> str:
        suffix = path.suffix.casefold()
        matches = [
            config.language_id
            for config in self._configs.values()
            if suffix in {item.casefold() for item in config.extensions}
        ]
        if not matches:
            raise LspError(f"no language server configured for extension {path.suffix!r}")
        if len(matches) > 1:
            raise LspError(f"multiple language servers match extension {path.suffix!r}")
        return matches[0]

    def workspace_revision(self, language_id: str) -> str | None:
        managed = self._services.get(language_id)
        return managed.workspace_revision if managed else None

    async def _managed(self, language_id: str) -> _ManagedService:
        await self.service(language_id)
        return self._services[language_id]

    def _resolve_workspace_file(self, path: Path) -> Path:
        candidate = path if path.is_absolute() else self.workspace_root / path
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as error:
            raise LspError(f"document is outside the workspace: {path}") from error
        if not resolved.is_file():
            raise LspError(f"document does not exist: {path}")
        return resolved


def discover_python_server() -> LanguageServerConfig | None:
    candidates = (
        ("basedpyright-langserver", "--stdio"),
        ("pyright-langserver", "--stdio"),
        ("pylsp",),
    )
    for candidate in candidates:
        executable = shutil.which(candidate[0])
        if executable:
            return LanguageServerConfig(
                language_id="python",
                command=(executable, *candidate[1:]),
                extensions=(".py", ".pyi"),
            )
    return None


def _default_client_factory(
    config: LanguageServerConfig,
    workspace_root: Path,
) -> LanguageService:
    return LspClient(
        command=config.command,
        workspace_root=workspace_root,
        request_timeout=config.request_timeout,
    )

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class StateLayoutError(ValueError):
    """Raised when state storage would violate the workspace boundary."""


def default_state_root(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get("RIVET_STATE_HOME")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)

    current_platform = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else home
    if current_platform == "darwin":
        return (user_home / "Library" / "Application Support" / "Rivet").resolve(strict=False)
    if current_platform == "win32":
        local_app_data = environment.get("LOCALAPPDATA")
        base = (
            Path(local_app_data).expanduser() if local_app_data else user_home / "AppData" / "Local"
        )
        return (base / "Rivet").resolve(strict=False)
    xdg_state_home = environment.get("XDG_STATE_HOME")
    if xdg_state_home:
        return (Path(xdg_state_home).expanduser() / "rivet").resolve(strict=False)
    return (user_home / ".local" / "state" / "rivet").resolve(strict=False)


def workspace_state_key(workspace_root: Path) -> str:
    canonical = workspace_root.expanduser().resolve(strict=False)
    return hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class StateLayout:
    application_root: Path
    workspace_root: Path
    workspace_state_root: Path
    database_path: Path
    artifacts_root: Path
    logs_root: Path
    indexes_root: Path

    @classmethod
    def for_workspace(
        cls,
        workspace_root: Path,
        *,
        state_root: Path | None = None,
    ) -> StateLayout:
        workspace = workspace_root.expanduser().resolve(strict=False)
        application = (state_root or default_state_root()).expanduser().resolve(strict=False)
        scoped = (application / "workspaces" / workspace_state_key(workspace)).resolve(strict=False)
        if scoped == workspace or scoped.is_relative_to(workspace):
            raise StateLayoutError("Rivet runtime state must be stored outside the workspace")
        return cls(
            application_root=application,
            workspace_root=workspace,
            workspace_state_root=scoped,
            database_path=scoped / "state.sqlite3",
            artifacts_root=scoped / "artifacts",
            logs_root=scoped / "logs",
            indexes_root=scoped / "indexes",
        )

    def create(self) -> StateLayout:
        for directory in (
            self.application_root,
            self.workspace_state_root,
            self.artifacts_root,
            self.logs_root,
            self.indexes_root,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def default_state_root() -> Path:
    configured = os.environ.get("RIVET_STATE_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "Rivet").resolve()
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "Rivet").resolve()
        return (Path.home() / "AppData" / "Local" / "Rivet").resolve()

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return (Path(xdg_state_home).expanduser() / "rivet").resolve()
    return (Path.home() / ".local" / "state" / "rivet").resolve()


def workspace_state_directory(
    workspace: Path,
    *,
    state_root: Path | None = None,
) -> Path:
    resolved_workspace = workspace.expanduser().resolve()
    workspace_key = hashlib.sha256(
        str(resolved_workspace).encode("utf-8")
    ).hexdigest()[:20]
    base = (state_root or default_state_root()).expanduser().resolve()
    return base / "workspaces" / workspace_key


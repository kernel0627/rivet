from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

from rivet.configuration.models import RivetConfig

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedConfig:
    config: RivetConfig
    sources: tuple[Path | str, ...]


def default_user_config_path() -> Path:
    configured = os.environ.get("RIVET_CONFIG_HOME")
    if configured:
        return Path(configured).expanduser().resolve() / "config.toml"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Rivet"
            / "config.toml"
        ).resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return (base / "Rivet" / "config.toml").resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (base / "rivet" / "config.toml").resolve()


def load_config(
    workspace: Path,
    *,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    user_config_path: Path | None = None,
) -> LoadedConfig:
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        raise ConfigurationError(f"workspace is not a directory: {root}")

    dotenv_path = root / ".env"
    dotenv_environment: dict[str, str] = {}
    if dotenv_path.is_file():
        dotenv_environment = {
            name: value
            for name, value in dotenv_values(
                dotenv_path=dotenv_path,
                encoding="utf-8",
            ).items()
            if value is not None
        }
        if environ is None:
            load_dotenv(
                dotenv_path=dotenv_path,
                override=False,
                encoding="utf-8",
            )

    process_environment = os.environ if environ is None else environ
    effective_environment = {
        **dotenv_environment,
        **process_environment,
    }
    merged: dict[str, Any] = {}
    sources: list[Path | str] = ["defaults"]
    user_path = (user_config_path or default_user_config_path()).expanduser().resolve()
    project_path = root / ".rivet" / "config.toml"

    for path in (user_path, project_path):
        if path.is_file():
            _deep_merge(merged, _read_toml(path))
            sources.append(path)

    if dotenv_environment:
        sources.append(dotenv_path)
    env_values = _environment_overrides(effective_environment)
    if env_values:
        _deep_merge(merged, env_values)
        sources.append("environment")
    if overrides:
        _deep_merge(merged, dict(overrides))
        sources.append("overrides")

    try:
        config = RivetConfig.model_validate(merged)
    except Exception as exc:
        raise ConfigurationError(str(exc)) from exc
    return LoadedConfig(config=config, sources=tuple(sources))


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"config root must be a table: {path}")
    return value


def _deep_merge(target: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    for key, value in incoming.items():
        existing = target.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _deep_merge(existing, value)
        elif isinstance(value, Mapping):
            nested: dict[str, Any] = {}
            _deep_merge(nested, value)
            target[key] = nested
        else:
            target[key] = value


def _environment_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    mapping: dict[str, tuple[str, ...]] = {
        "RIVET_PROVIDER": ("model", "provider"),
        "RIVET_MODEL": ("model", "model"),
        "RIVET_BASE_URL": ("model", "base_url"),
        "RIVET_API_KEY_ENV": ("model", "api_key_env"),
        "RIVET_TIMEOUT_SECONDS": ("model", "timeout_seconds"),
        "RIVET_MAX_RETRIES": ("model", "max_retries"),
        "RIVET_MAX_TURNS": ("runtime", "max_turns"),
        "RIVET_MAX_MODEL_CALLS": ("runtime", "max_model_calls"),
        "RIVET_MAX_TOOL_EXECUTIONS": ("runtime", "max_tool_executions"),
        "RIVET_MAX_INPUT_TOKENS": ("context", "max_input_tokens"),
        "RIVET_STATE_HOME": ("state", "root"),
    }
    result: dict[str, Any] = {}
    for env_name, path in mapping.items():
        raw = environ.get(env_name)
        if raw is None:
            continue
        value: Any = raw
        if env_name in {
            "RIVET_TIMEOUT_SECONDS",
        }:
            value = float(raw)
        elif env_name in {
            "RIVET_MAX_RETRIES",
            "RIVET_MAX_TURNS",
            "RIVET_MAX_MODEL_CALLS",
            "RIVET_MAX_TOOL_EXECUTIONS",
            "RIVET_MAX_INPUT_TOKENS",
        }:
            value = int(raw)
        cursor = result
        for component in path[:-1]:
            cursor = cursor.setdefault(component, {})
        cursor[path[-1]] = value
    return result

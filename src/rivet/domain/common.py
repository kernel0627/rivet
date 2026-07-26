from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

CURRENT_SCHEMA_VERSION = 1

JsonObject = dict[str, Any]


class DomainValidationError(ValueError):
    """Raised when a persisted domain value violates a runtime invariant."""


class FrozenJsonObject(dict[str, Any]):
    """A JSON object that cannot be mutated after domain construction."""

    @staticmethod
    def _immutable(*_: object, **__: object) -> None:
        raise TypeError("persisted JSON values are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")


def require_identifier(value: str, field_name: str) -> None:
    if not value or value.strip() != value or any(character.isspace() for character in value):
        raise DomainValidationError(f"{field_name} must be a non-empty identifier")


def require_non_empty(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise DomainValidationError(f"{field_name} must not be empty")


def require_digest(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if len(value) != 64:
        raise DomainValidationError(f"{field_name} must be a sha256 digest")
    try:
        int(value, 16)
    except ValueError as error:
        raise DomainValidationError(f"{field_name} must be a sha256 digest") from error


def require_schema_version(value: int) -> None:
    if value < 1 or value > CURRENT_SCHEMA_VERSION:
        raise DomainValidationError(
            f"unsupported schema_version {value}; current version is {CURRENT_SCHEMA_VERSION}"
        )


def validate_json_object(value: Mapping[str, Any], field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} must be a mapping")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DomainValidationError(f"{field_name} must contain JSON-compatible values") from error


def freeze_json_object(value: Mapping[str, Any], field_name: str) -> FrozenJsonObject:
    validate_json_object(value, field_name)
    return _freeze_json_value(value, field_name)


def _freeze_json_value(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainValidationError(f"{field_name} keys must be strings")
            frozen[key] = _freeze_json_value(item, field_name)
        return FrozenJsonObject(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, field_name) for item in value)
    return value


def new_id(prefix: str) -> str:
    require_identifier(prefix, "prefix")
    return f"{prefix}_{uuid4().hex}"


def workspace_id_for(root: Path, repository_identity: str | None = None) -> str:
    canonical_root = str(root.expanduser().resolve())
    identity = repository_identity or ""
    digest = hashlib.sha256(f"{canonical_root}\0{identity}".encode()).hexdigest()
    return f"ws_{digest[:24]}"


def datetime_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def datetime_from_text(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    require_aware(parsed, "datetime")
    return parsed


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return datetime_to_text(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def json_loads(value: str) -> Any:
    return json.loads(value)

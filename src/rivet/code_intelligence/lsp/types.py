from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class LspPosition:
    line: int
    character: int

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LspPosition:
        return cls(line=int(value["line"]), character=int(value["character"]))


@dataclass(frozen=True)
class LspRange:
    start: LspPosition
    end: LspPosition

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LspRange:
        return cls(
            start=LspPosition.from_json(value["start"]),
            end=LspPosition.from_json(value["end"]),
        )


@dataclass(frozen=True)
class LspLocation:
    uri: str
    path: str
    range: LspRange

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LspLocation:
        uri = str(value["uri"])
        return cls(
            uri=uri,
            path=uri_to_path(uri),
            range=LspRange.from_json(value["range"]),
        )


@dataclass(frozen=True)
class LspDiagnostic:
    range: LspRange
    message: str
    severity: int | None = None
    code: str | int | None = None
    source: str | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LspDiagnostic:
        return cls(
            range=LspRange.from_json(value["range"]),
            message=str(value["message"]),
            severity=int(value["severity"]) if value.get("severity") is not None else None,
            code=value.get("code"),
            source=value.get("source"),
        )


def uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    return path


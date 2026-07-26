from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*([^\s,;]+)"
)


@dataclass(frozen=True)
class Redactor:
    secret_values: tuple[str, ...] = ()
    replacement: str = "[REDACTED]"
    sensitive_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "api_key",
                "apikey",
                "authorization",
                "access_token",
                "refresh_token",
                "password",
                "secret",
                "token",
                "cookie",
                "set-cookie",
            }
        )
    )

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            result: dict[Any, Any] = {}
            for key, item in value.items():
                if str(key).casefold() in self.sensitive_keys:
                    result[key] = self.replacement
                else:
                    result[key] = self.redact(item)
            return result
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return [self.redact(item) for item in value]
        return value

    def redact_text(self, text: str, *, max_chars: int | None = None) -> str:
        redacted = _BEARER_PATTERN.sub(f"Bearer {self.replacement}", text)
        redacted = _ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}={self.replacement}",
            redacted,
        )
        for secret in sorted(
            (value for value in self.secret_values if value),
            key=len,
            reverse=True,
        ):
            redacted = redacted.replace(secret, self.replacement)
        if max_chars is not None and len(redacted) > max_chars:
            return redacted[:max_chars] + "…"
        return redacted

    def exception_summary(self, exc: BaseException, *, max_chars: int = 1000) -> str:
        message = self.redact_text(str(exc), max_chars=max_chars)
        return f"{type(exc).__name__}: {message}"


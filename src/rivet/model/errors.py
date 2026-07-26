from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum


class ModelErrorKind(str, Enum):
    TRANSPORT = "MODEL_TRANSPORT_ERROR"
    PROTOCOL = "MODEL_PROTOCOL_ERROR"
    RATE_LIMIT = "MODEL_RATE_LIMIT"
    AUTH = "MODEL_AUTH_ERROR"
    CONTEXT_OVERFLOW = "MODEL_CONTEXT_OVERFLOW"
    UNAVAILABLE = "MODEL_UNAVAILABLE"
    CANCELLED = "MODEL_CANCELLED"


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact_sensitive(
    text: object,
    *,
    secrets: Iterable[str | None] = (),
    limit: int = 2_000,
) -> str:
    """Redact credentials and bound provider-controlled error text."""

    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    if len(result) > limit:
        result = result[:limit] + "…[truncated]"
    return result


class ModelGatewayError(RuntimeError):
    """A safe, classified failure at the provider boundary."""

    def __init__(
        self,
        kind: ModelErrorKind,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        self.provider_request_id = provider_request_id

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "message": str(self),
            "retryable": self.retryable,
            "status_code": self.status_code,
            "provider_request_id": self.provider_request_id,
        }

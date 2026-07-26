from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any

from rivet.domain.artifacts import ArtifactRef
from rivet.domain.common import (
    CURRENT_SCHEMA_VERSION,
    JsonObject,
    datetime_from_text,
    datetime_to_text,
    freeze_json_object,
    require_aware,
    require_identifier,
    require_non_empty,
    require_schema_version,
    utc_now,
)


class VerificationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    name: str
    status: VerificationStatus
    summary: str
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    duration_seconds: float | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_non_empty(self.name, "name")
        require_non_empty(self.summary, "summary")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be non-negative")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "evidence": [item.to_dict() for item in self.evidence],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VerificationCheck:
        return cls(
            name=str(value["name"]),
            status=VerificationStatus(str(value["status"])),
            summary=str(value["summary"]),
            command=tuple(str(item) for item in value.get("command", [])),
            exit_code=(int(value["exit_code"]) if value.get("exit_code") is not None else None),
            duration_seconds=(
                float(value["duration_seconds"])
                if value.get("duration_seconds") is not None
                else None
            ),
            evidence=tuple(ArtifactRef.from_dict(dict(item)) for item in value.get("evidence", [])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str
    run_id: str
    status: VerificationStatus
    checks: tuple[VerificationCheck, ...]
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    changed_paths: tuple[str, ...] = ()
    unexpected_paths: tuple[str, ...] = ()
    evidence: tuple[ArtifactRef, ...] = ()
    retry_recommendation: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.verification_id, "verification_id")
        require_identifier(self.run_id, "run_id")
        frozen_diagnostics = tuple(
            freeze_json_object(diagnostic, f"diagnostics[{index}]")
            for index, diagnostic in enumerate(self.diagnostics)
        )
        object.__setattr__(self, "diagnostics", frozen_diagnostics)
        if self.status is VerificationStatus.PASSED:
            if self.unexpected_paths:
                raise ValueError("PASSED verification cannot contain unexpected_paths")
            if any(check.status is not VerificationStatus.PASSED for check in self.checks):
                raise ValueError("PASSED verification requires all checks to pass")
        require_aware(self.created_at, "created_at")
        require_schema_version(self.schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "verification_id": self.verification_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
            "diagnostics": [dict(item) for item in self.diagnostics],
            "changed_paths": list(self.changed_paths),
            "unexpected_paths": list(self.unexpected_paths),
            "evidence": [item.to_dict() for item in self.evidence],
            "retry_recommendation": self.retry_recommendation,
            "created_at": datetime_to_text(self.created_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VerificationResult:
        return cls(
            verification_id=str(value["verification_id"]),
            run_id=str(value["run_id"]),
            status=VerificationStatus(str(value["status"])),
            checks=tuple(
                VerificationCheck.from_dict(dict(item)) for item in value.get("checks", [])
            ),
            diagnostics=tuple(dict(item) for item in value.get("diagnostics", [])),
            changed_paths=tuple(str(item) for item in value.get("changed_paths", [])),
            unexpected_paths=tuple(str(item) for item in value.get("unexpected_paths", [])),
            evidence=tuple(ArtifactRef.from_dict(dict(item)) for item in value.get("evidence", [])),
            retry_recommendation=(
                str(value["retry_recommendation"])
                if value.get("retry_recommendation") is not None
                else None
            ),
            created_at=datetime_from_text(str(value["created_at"])),
            schema_version=int(value.get("schema_version", CURRENT_SCHEMA_VERSION)),
        )

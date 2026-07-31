from __future__ import annotations

import json
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvalToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class EvalModelStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = None
    reasoning_content: str | None = None
    tool_calls: tuple[EvalToolCall, ...] = ()

    @model_validator(mode="after")
    def require_output(self) -> EvalModelStep:
        if self.text is None and not self.tool_calls:
            raise ValueError("eval model step requires text or tool calls")
        return self


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    expected_files: tuple[str, ...] = ()
    forbidden_files: tuple[str, ...] = ()
    expected_tests: tuple[str, ...] = ()
    expected_final_contains: tuple[str, ...] = ()
    relevant_chunks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    resume_permissions: tuple[str, ...] = ()
    fixture_files: dict[str, str] = Field(default_factory=dict)
    offline_model: tuple[EvalModelStep, ...] = ()

    @field_validator(
        "expected_files",
        "forbidden_files",
        mode="after",
    )
    @classmethod
    def validate_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _validate_relative_path(value)
        return values

    @field_validator("fixture_files", mode="after")
    @classmethod
    def validate_fixture_files(cls, values: dict[str, str]) -> dict[str, str]:
        total_chars = 0
        for path, content in values.items():
            _validate_relative_path(path)
            total_chars += len(content)
        if total_chars > 2_000_000:
            raise ValueError("inline eval fixture exceeds 2,000,000 characters")
        return values

    @field_validator("resume_permissions", mode="after")
    @classmethod
    def validate_resume_permissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {
            "safe_read",
            "sensitive_read",
            "workspace_write",
            "process_execute",
            "network_access",
            "external_write",
            "destructive",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                "unknown resume permission(s): " + ", ".join(sorted(unknown))
            )
        if len(set(values)) != len(values):
            raise ValueError("resume permissions must be unique")
        return values


def load_jsonl(path: Path) -> list[EvalCase]:
    with path.open("r", encoding="utf-8") as handle:
        return _load_lines(handle, source=str(path))


def load_baseline() -> list[EvalCase]:
    dataset = resources.files("rivet.evaluation").joinpath("baseline/cases.jsonl")
    return _load_lines(
        dataset.read_text(encoding="utf-8").splitlines(),
        source="rivet.evaluation/baseline/cases.jsonl",
    )


def iter_by_tag(cases: list[EvalCase], tag: str) -> Iterator[EvalCase]:
    return (case for case in cases if tag in case.tags)


def _load_lines(lines: Iterator[str] | list[str], *, source: str) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            case = EvalCase.model_validate(json.loads(line))
        except Exception as exc:
            raise ValueError(
                f"invalid eval case at {source}:{line_number}: {exc}"
            ) from exc
        if case.id in seen:
            raise ValueError(
                f"duplicate eval case id at {source}:{line_number}: {case.id}"
            )
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError(f"eval dataset is empty: {source}")
    return cases


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or value in {".", ".."}
        or ".." in path.parts
    ):
        raise ValueError(f"eval path must be workspace-relative: {value!r}")

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    expected_files: tuple[str, ...] = ()
    forbidden_files: tuple[str, ...] = ()
    expected_tests: tuple[str, ...] = ()
    relevant_chunks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def load_jsonl(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                case = EvalCase.model_validate(json.loads(line))
            except Exception as exc:
                raise ValueError(f"invalid eval case at {path}:{line_number}: {exc}") from exc
            if case.id in seen:
                raise ValueError(f"duplicate eval case id at {path}:{line_number}: {case.id}")
            seen.add(case.id)
            cases.append(case)
    return cases


def iter_by_tag(cases: list[EvalCase], tag: str) -> Iterator[EvalCase]:
    return (case for case in cases if tag in case.tags)


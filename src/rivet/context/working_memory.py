from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from rivet.context.budget import TokenEstimator


def _normalize_items(items: Iterable[str], *, max_chars: int) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = " ".join(str(item).split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean[:max_chars])
    return tuple(normalized)


@dataclass(frozen=True)
class WorkingMemoryPolicy:
    max_items_per_section: int = 32
    max_chars_per_item: int = 1_000

    def __post_init__(self) -> None:
        if self.max_items_per_section <= 0:
            raise ValueError("max_items_per_section must be positive")
        if self.max_chars_per_item <= 0:
            raise ValueError("max_chars_per_item must be positive")


@dataclass(frozen=True)
class WorkingMemoryUpdate:
    objective: str | None = None
    confirmed_facts: tuple[str, ...] = ()
    relevant_files: tuple[str, ...] = ()
    relevant_symbols: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    verification_failures: tuple[str, ...] = ()
    pending_items: tuple[str, ...] = ()
    completed_items: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkingMemoryCompaction:
    original_tokens: int
    final_tokens: int
    dropped_by_section: Mapping[str, int] = field(default_factory=dict)
    objective_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "dropped_by_section": dict(self.dropped_by_section),
            "objective_truncated": self.objective_truncated,
        }


@dataclass(frozen=True)
class WorkingMemory:
    """Bounded, task-scoped facts. This is deliberately not chat history."""

    objective: str = ""
    confirmed_facts: tuple[str, ...] = ()
    relevant_files: tuple[str, ...] = ()
    relevant_symbols: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    verification_failures: tuple[str, ...] = ()
    pending_items: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    revision: int = 0

    _SECTIONS: ClassVar[tuple[str, ...]] = (
        "confirmed_facts",
        "relevant_files",
        "relevant_symbols",
        "hypotheses",
        "modified_files",
        "verification_failures",
        "pending_items",
        "next_steps",
    )

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("working memory revision must be non-negative")
        object.__setattr__(self, "objective", self.objective.strip())
        for name in self._SECTIONS:
            object.__setattr__(self, name, tuple(getattr(self, name)))

    def apply(
        self,
        update: WorkingMemoryUpdate,
        *,
        policy: WorkingMemoryPolicy | None = None,
    ) -> WorkingMemory:
        policy = policy or WorkingMemoryPolicy()
        values: dict[str, Any] = {
            "objective": (
                update.objective.strip()
                if update.objective is not None
                else self.objective
            ),
            "revision": self.revision + 1,
        }
        completed = set(
            _normalize_items(
                update.completed_items,
                max_chars=policy.max_chars_per_item,
            )
        )
        for section in self._SECTIONS:
            additions = getattr(update, section)
            merged = _normalize_items(
                (*getattr(self, section), *additions),
                max_chars=policy.max_chars_per_item,
            )
            if section == "pending_items" and completed:
                merged = tuple(item for item in merged if item not in completed)
            values[section] = merged[-policy.max_items_per_section :]
        return WorkingMemory(**values)

    @property
    def is_empty(self) -> bool:
        return not self.objective and not any(
            getattr(self, section) for section in self._SECTIONS
        )

    def render(self) -> str:
        lines = ["RUN WORKING MEMORY"]
        if self.objective:
            lines.extend(("Objective:", f"- {self.objective}"))
        labels = {
            "confirmed_facts": "Confirmed facts",
            "relevant_files": "Relevant files",
            "relevant_symbols": "Relevant symbols",
            "hypotheses": "Hypotheses (unconfirmed)",
            "modified_files": "Modified files",
            "verification_failures": "Verification failures",
            "pending_items": "Pending items",
            "next_steps": "Next-step candidates",
        }
        for section in self._SECTIONS:
            items = getattr(self, section)
            if not items:
                continue
            lines.append(f"{labels[section]}:")
            lines.extend(f"- {item}" for item in items)
        lines.append(f"Memory revision: {self.revision}")
        return "\n".join(lines)

    def compact(
        self,
        *,
        max_tokens: int,
        estimator: TokenEstimator,
    ) -> tuple[WorkingMemory, WorkingMemoryCompaction]:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        original_tokens = estimator.estimate_text(self.render())
        if original_tokens <= max_tokens:
            return (
                self,
                WorkingMemoryCompaction(
                    original_tokens=original_tokens,
                    final_tokens=original_tokens,
                ),
            )

        current = self
        dropped: dict[str, int] = {}
        # Lower-value, older entries leave first. Index zero is oldest because
        # updates append new facts.
        removal_order = (
            "hypotheses",
            "relevant_symbols",
            "next_steps",
            "relevant_files",
            "confirmed_facts",
            "pending_items",
            "modified_files",
            "verification_failures",
        )
        for section in removal_order:
            items = list(getattr(current, section))
            while items and estimator.estimate_text(current.render()) > max_tokens:
                items.pop(0)
                dropped[section] = dropped.get(section, 0) + 1
                current = replace(current, **{section: tuple(items)})

        objective_truncated = False
        if estimator.estimate_text(current.render()) > max_tokens and current.objective:
            objective_truncated = True
            low = 0
            high = len(current.objective)
            best = ""
            while low <= high:
                middle = (low + high) // 2
                candidate = current.objective[:middle].rstrip()
                if middle < len(current.objective):
                    candidate += " …[truncated]"
                probe = replace(current, objective=candidate)
                if estimator.estimate_text(probe.render()) <= max_tokens:
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            current = replace(current, objective=best)

        final_tokens = estimator.estimate_text(current.render())
        return (
            current,
            WorkingMemoryCompaction(
                original_tokens=original_tokens,
                final_tokens=final_tokens,
                dropped_by_section=dropped,
                objective_truncated=objective_truncated,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            **{name: list(getattr(self, name)) for name in self._SECTIONS},
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkingMemory:
        return cls(
            objective=str(value.get("objective", "")),
            **{
                name: tuple(str(item) for item in value.get(name, ()))
                for name in cls._SECTIONS
            },
            revision=int(value.get("revision", 0)),
        )

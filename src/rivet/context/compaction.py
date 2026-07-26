from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rivet.context.budget import ContextBudget, TokenEstimator
from rivet.context.policy import (
    ArtifactRef,
    ContextPolicy,
    ContextSource,
    ContextSourceLabel,
)
from rivet.context.working_memory import WorkingMemoryCompaction
from rivet.model.types import Message, MessageRole


class SourceDisposition(str, Enum):
    FULL = "full"
    TRUNCATED = "truncated"
    ARTIFACT_REF = "artifact_ref"
    OMITTED = "omitted"
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True)
class SourceSelection:
    source_id: str
    label: ContextSourceLabel
    disposition: SourceDisposition
    estimated_tokens: int
    digest: str
    reason: str | None = None
    artifact_ref: ArtifactRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label.value,
            "disposition": self.disposition.value,
            "estimated_tokens": self.estimated_tokens,
            "digest": self.digest,
            "reason": self.reason,
            "artifact_ref": self.artifact_ref.to_dict() if self.artifact_ref else None,
        }


@dataclass(frozen=True)
class PreparedContextSource:
    source: ContextSource
    message: Message
    selection: SourceSelection
    original_index: int


@dataclass(frozen=True)
class PreparedSources:
    candidates: tuple[PreparedContextSource, ...]
    deduplicated: tuple[SourceSelection, ...]
    original_tokens: int


@dataclass(frozen=True)
class CompactionReport:
    original_source_tokens: int = 0
    final_source_tokens: int = 0
    deduplicated_source_ids: tuple[str, ...] = ()
    truncated_source_ids: tuple[str, ...] = ()
    artifactized_source_ids: tuple[str, ...] = ()
    omitted_source_ids: tuple[str, ...] = ()
    dropped_recent_messages: int = 0
    working_memory: WorkingMemoryCompaction | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_source_tokens": self.original_source_tokens,
            "final_source_tokens": self.final_source_tokens,
            "deduplicated_source_ids": list(self.deduplicated_source_ids),
            "truncated_source_ids": list(self.truncated_source_ids),
            "artifactized_source_ids": list(self.artifactized_source_ids),
            "omitted_source_ids": list(self.omitted_source_ids),
            "dropped_recent_messages": self.dropped_recent_messages,
            "working_memory": (
                self.working_memory.to_dict() if self.working_memory else None
            ),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DeterministicCompactor:
    estimator: TokenEstimator
    policy: ContextPolicy

    def prepare(
        self,
        sources: Sequence[ContextSource],
        budget: ContextBudget,
    ) -> PreparedSources:
        winners: dict[str, int] = {}
        for index, source in enumerate(sources):
            digest = source.content_digest
            incumbent = winners.get(digest)
            if incumbent is None:
                winners[digest] = index
                continue
            incumbent_source = sources[incumbent]
            incumbent_rank = (
                not incumbent_source.required,
                self.policy.priority_for(incumbent_source),
                incumbent,
            )
            candidate_rank = (
                not source.required,
                self.policy.priority_for(source),
                index,
            )
            if candidate_rank < incumbent_rank:
                winners[digest] = index

        candidates: list[PreparedContextSource] = []
        deduplicated: list[SourceSelection] = []
        original_tokens = 0
        for index, source in enumerate(sources):
            original_body = source.content or self._render_artifact(source.artifact_ref)
            original_message = self._message(source, original_body)
            original_tokens += self.estimator.estimate_message(original_message)
            winner = winners[source.content_digest]
            if winner != index:
                deduplicated.append(
                    SourceSelection(
                        source_id=source.source_id,
                        label=source.label,
                        disposition=SourceDisposition.DEDUPLICATED,
                        estimated_tokens=0,
                        digest=source.content_digest,
                        reason=f"duplicate_of:{sources[winner].source_id}",
                        artifact_ref=source.artifact_ref,
                    )
                )
                continue
            candidates.append(
                self._prepare_one(
                    source,
                    original_index=index,
                    max_tokens=budget.max_inline_source_tokens,
                )
            )
        return PreparedSources(
            candidates=tuple(candidates),
            deduplicated=tuple(deduplicated),
            original_tokens=original_tokens,
        )

    def fit(
        self,
        candidate: PreparedContextSource,
        *,
        max_message_tokens: int,
        min_tokens: int,
    ) -> PreparedContextSource | None:
        if candidate.selection.estimated_tokens <= max_message_tokens:
            return candidate
        source = candidate.source
        if source.artifact_ref is not None:
            artifact_candidate = self._candidate(
                source,
                original_index=candidate.original_index,
                body=self._render_artifact(source.artifact_ref),
                disposition=SourceDisposition.ARTIFACT_REF,
                reason="replaced_with_artifact_ref_to_fit_budget",
            )
            if artifact_candidate.selection.estimated_tokens <= max_message_tokens:
                return artifact_candidate
        if not self.policy.compaction_enabled or max_message_tokens < min_tokens:
            return None
        body = source.content
        if not body:
            return None
        truncated = self._truncate_body_to_message_budget(
            source,
            body,
            max_message_tokens=max_message_tokens,
        )
        if not truncated:
            return None
        fitted = self._candidate(
            source,
            original_index=candidate.original_index,
            body=truncated,
            disposition=SourceDisposition.TRUNCATED,
            reason="truncated_to_fit_remaining_budget",
        )
        if fitted.selection.estimated_tokens > max_message_tokens:
            return None
        return fitted

    def omitted(
        self,
        candidate: PreparedContextSource,
        *,
        reason: str,
    ) -> SourceSelection:
        return SourceSelection(
            source_id=candidate.source.source_id,
            label=candidate.source.label,
            disposition=SourceDisposition.OMITTED,
            estimated_tokens=0,
            digest=candidate.source.content_digest,
            reason=reason,
            artifact_ref=candidate.source.artifact_ref,
        )

    def _prepare_one(
        self,
        source: ContextSource,
        *,
        original_index: int,
        max_tokens: int,
    ) -> PreparedContextSource:
        if source.content is None:
            return self._candidate(
                source,
                original_index=original_index,
                body=self._render_artifact(source.artifact_ref),
                disposition=SourceDisposition.ARTIFACT_REF,
                reason="source_material_is_external_artifact",
            )

        full = self._candidate(
            source,
            original_index=original_index,
            body=source.content,
            disposition=SourceDisposition.FULL,
        )
        if full.selection.estimated_tokens <= max_tokens:
            return full
        if source.artifact_ref is not None:
            return self._candidate(
                source,
                original_index=original_index,
                body=self._render_artifact(source.artifact_ref),
                disposition=SourceDisposition.ARTIFACT_REF,
                reason="inline_source_exceeded_limit",
            )
        if not self.policy.compaction_enabled:
            return full
        truncated = self._truncate_body_to_message_budget(
            source,
            source.content,
            max_message_tokens=max_tokens,
        )
        return self._candidate(
            source,
            original_index=original_index,
            body=truncated,
            disposition=SourceDisposition.TRUNCATED,
            reason="inline_source_exceeded_limit",
        )

    def _candidate(
        self,
        source: ContextSource,
        *,
        original_index: int,
        body: str,
        disposition: SourceDisposition,
        reason: str | None = None,
    ) -> PreparedContextSource:
        message = self._message(source, body)
        return PreparedContextSource(
            source=source,
            message=message,
            selection=SourceSelection(
                source_id=source.source_id,
                label=source.label,
                disposition=disposition,
                estimated_tokens=self.estimator.estimate_message(message),
                digest=source.content_digest,
                reason=reason,
                artifact_ref=source.artifact_ref,
            ),
            original_index=original_index,
        )

    def _message(self, source: ContextSource, body: str) -> Message:
        role = MessageRole.USER
        if source.label is ContextSourceLabel.SYSTEM_INSTRUCTION:
            role = MessageRole.SYSTEM
        elif source.label is ContextSourceLabel.PROJECT_POLICY:
            role = MessageRole.DEVELOPER
        return Message(
            role=role,
            content=self.policy.render_source(source, body),
            source_label=source.label.value,
            metadata={"source_id": source.source_id},
        )

    def _truncate_body_to_message_budget(
        self,
        source: ContextSource,
        body: str,
        *,
        max_message_tokens: int,
    ) -> str:
        low = 0
        high = len(body)
        best = ""
        suffix = "\n…[source truncated by context budget]"
        while low <= high:
            middle = (low + high) // 2
            probe_body = body[:middle].rstrip()
            if middle < len(body):
                probe_body += suffix
            probe = self._message(source, probe_body)
            if self.estimator.estimate_message(probe) <= max_message_tokens:
                best = probe_body
                low = middle + 1
            else:
                high = middle - 1
        return best

    @staticmethod
    def _render_artifact(ref: ArtifactRef | None) -> str:
        if ref is None:
            return ""
        payload = ref.to_dict()
        return (
            "The full source is stored outside the prompt. Use the artifact service "
            "when its content is needed.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )

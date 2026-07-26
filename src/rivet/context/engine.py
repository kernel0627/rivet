from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from rivet.context.budget import (
    ContextBudget,
    ContextBudgetExceeded,
    HeuristicTokenEstimator,
    TokenEstimate,
    TokenEstimator,
)
from rivet.context.compaction import (
    CompactionReport,
    DeterministicCompactor,
    PreparedContextSource,
    SourceDisposition,
    SourceSelection,
)
from rivet.context.policy import ContextPolicy, ContextSource, ContextSourceLabel
from rivet.context.working_memory import WorkingMemory
from rivet.model.types import (
    CancellationToken,
    Message,
    MessageRole,
    ModelRequest,
    ToolSchema,
)


def _json_mapping(value: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    try:
        normalized = json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only JSON values") from exc
    if not isinstance(normalized, dict):
        raise ValueError(f"{name} must be a JSON object")
    return normalized


@dataclass(frozen=True)
class ContextRequest:
    objective: str
    budget: ContextBudget
    system_instructions: tuple[str, ...] = ()
    project_instructions: tuple[str, ...] = ()
    session_summary: str | None = None
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    recent_messages: tuple[Message, ...] = ()
    sources: tuple[ContextSource, ...] = ()
    tool_schemas: tuple[ToolSchema, ...] = ()
    run_id: str | None = None
    workspace_revision: str | None = None
    context_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("context objective must not be empty")
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(
            self,
            "system_instructions",
            tuple(item.strip() for item in self.system_instructions if item.strip()),
        )
        object.__setattr__(
            self,
            "project_instructions",
            tuple(item.strip() for item in self.project_instructions if item.strip()),
        )
        object.__setattr__(self, "recent_messages", tuple(self.recent_messages))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "tool_schemas", tuple(self.tool_schemas))
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata, name="context request metadata"),
        )
        names = [schema.name for schema in self.tool_schemas]
        if len(names) != len(set(names)):
            raise ValueError("model-visible tool schema names must be unique")


@dataclass(frozen=True)
class ContextEnvelope:
    context_id: str
    messages: tuple[Message, ...]
    tool_schemas: tuple[ToolSchema, ...]
    included_sources: tuple[SourceSelection, ...]
    omitted_sources: tuple[SourceSelection, ...]
    compaction_report: CompactionReport
    token_estimate: TokenEstimate
    digest: str
    reserved_output_tokens: int = 0
    run_id: str | None = None
    workspace_revision: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.context_id.strip():
            raise ValueError("context_id must not be empty")
        if len(self.digest) != 64:
            raise ValueError("context digest must be sha256")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tool_schemas", tuple(self.tool_schemas))
        object.__setattr__(self, "included_sources", tuple(self.included_sources))
        object.__setattr__(self, "omitted_sources", tuple(self.omitted_sources))
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata, name="context envelope metadata"),
        )

    def to_model_request(
        self,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
        temperature: float | None = None,
        cancellation_token: CancellationToken | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ModelRequest:
        request_metadata = dict(self.metadata)
        request_metadata.update(
            {
                "context_id": self.context_id,
                "context_digest": self.digest,
            }
        )
        if self.run_id is not None:
            request_metadata["run_id"] = self.run_id
        if self.workspace_revision is not None:
            request_metadata["workspace_revision"] = self.workspace_revision
        if metadata:
            request_metadata.update(metadata)
        return ModelRequest(
            messages=self.messages,
            tools=self.tool_schemas,
            model=model,
            max_output_tokens=self.reserved_output_tokens or None,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            metadata=request_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "messages": [message.to_dict() for message in self.messages],
            "tool_schemas": [schema.to_dict() for schema in self.tool_schemas],
            "included_sources": [
                source.to_dict() for source in self.included_sources
            ],
            "omitted_sources": [source.to_dict() for source in self.omitted_sources],
            "compaction_report": self.compaction_report.to_dict(),
            "token_estimate": self.token_estimate.to_dict(),
            "digest": self.digest,
            "reserved_output_tokens": self.reserved_output_tokens,
            "run_id": self.run_id,
            "workspace_revision": self.workspace_revision,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class ContextEngine(Protocol):
    async def build(self, request: ContextRequest) -> ContextEnvelope:
        """Build one immutable model-input envelope without side effects."""


@dataclass(frozen=True)
class _MessageGroup:
    index: int
    messages: tuple[Message, ...]
    priority: int

    @property
    def size(self) -> int:
        return len(self.messages)


@dataclass(frozen=True)
class _BudgetCandidate:
    required: bool
    priority: int
    recency: int
    kind: str
    source: PreparedContextSource | None = None
    message_group: _MessageGroup | None = None


@dataclass(frozen=True)
class DefaultContextEngine:
    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)
    policy: ContextPolicy = field(default_factory=ContextPolicy)

    async def build(self, request: ContextRequest) -> ContextEnvelope:
        compactor = DeterministicCompactor(
            estimator=self.estimator,
            policy=self.policy,
        )
        system_message = self._system_message(request)
        project_message = self._project_message(request)
        objective_message = Message(
            role=MessageRole.USER,
            content=request.objective,
            source_label=ContextSourceLabel.USER_INSTRUCTION.value,
            metadata={"authority": "current_user_objective"},
        )
        fixed_messages = tuple(
            message
            for message in (system_message, project_message, objective_message)
            if message is not None
        )
        tool_tokens = self.estimator.estimate_tools(request.tool_schemas)
        fixed_tokens = sum(
            self.estimator.estimate_message(message) for message in fixed_messages
        )
        minimum_tokens = fixed_tokens + tool_tokens
        capacity = request.budget.input_capacity
        if minimum_tokens > capacity:
            raise ContextBudgetExceeded(
                required_tokens=minimum_tokens,
                available_tokens=capacity,
                reason="system, project, objective, and tool schemas are mandatory",
            )
        remaining = capacity - minimum_tokens

        effective_sources = list(request.sources)
        if request.session_summary:
            effective_sources.insert(
                0,
                ContextSource(
                    source_id="session-summary",
                    label=ContextSourceLabel.MODEL_SUMMARY,
                    content=request.session_summary,
                ),
            )

        memory_compaction = None
        if not request.working_memory.is_empty:
            compacted_memory, memory_compaction = request.working_memory.compact(
                max_tokens=request.budget.max_working_memory_tokens,
                estimator=self.estimator,
            )
            effective_sources.insert(
                0,
                ContextSource(
                    source_id="run-working-memory",
                    label=ContextSourceLabel.RUN_FACT,
                    content=compacted_memory.render(),
                    priority=self.policy.source_priorities.get(
                        ContextSourceLabel.RUN_FACT,
                        30,
                    ),
                ),
            )

        prepared = compactor.prepare(effective_sources, request.budget)
        recent_groups = self._recent_message_groups(request.recent_messages)
        recent_groups, cap_dropped = self._apply_recent_message_cap(recent_groups)

        candidates: list[_BudgetCandidate] = []
        for source in prepared.candidates:
            candidates.append(
                _BudgetCandidate(
                    required=source.source.required,
                    priority=self.policy.priority_for(source.source),
                    recency=source.original_index,
                    kind="source",
                    source=source,
                )
            )
        for group in recent_groups:
            candidates.append(
                _BudgetCandidate(
                    required=False,
                    priority=group.priority,
                    recency=group.index,
                    kind="recent",
                    message_group=group,
                )
            )
        candidates.sort(
            key=lambda item: (
                not item.required,
                item.priority,
                -item.recency,
                item.kind,
            )
        )

        selected_sources: list[PreparedContextSource] = []
        omitted_sources: list[SourceSelection] = list(prepared.deduplicated)
        selected_group_indexes: set[int] = set()
        dropped_recent = cap_dropped
        for candidate in candidates:
            if candidate.kind == "recent":
                group = candidate.message_group
                if group is None:
                    raise AssertionError("recent candidate has no message group")
                tokens = sum(
                    self.estimator.estimate_message(message)
                    for message in group.messages
                )
                if tokens <= remaining:
                    selected_group_indexes.add(group.index)
                    remaining -= tokens
                else:
                    dropped_recent += group.size
                continue

            source = candidate.source
            if source is None:
                raise AssertionError("source candidate has no source")
            fitted = compactor.fit(
                source,
                max_message_tokens=remaining,
                min_tokens=request.budget.min_truncation_tokens,
            )
            if fitted is not None:
                selected_sources.append(fitted)
                remaining -= fitted.selection.estimated_tokens
                continue
            if source.source.required:
                required = source.selection.estimated_tokens
                raise ContextBudgetExceeded(
                    required_tokens=capacity - remaining + required,
                    available_tokens=capacity,
                    reason=f"required source {source.source.source_id!r} does not fit",
                )
            omitted_sources.append(
                compactor.omitted(source, reason="insufficient_context_budget")
            )

        selected_recent_messages = tuple(
            message
            for group in recent_groups
            if group.index in selected_group_indexes
            for message in group.messages
        )
        selected_sources.sort(key=lambda item: item.original_index)
        selected_source_messages = tuple(source.message for source in selected_sources)

        messages = (
            (system_message,)
            + ((project_message,) if project_message is not None else ())
            + selected_recent_messages
            + selected_source_messages
            + (objective_message,)
        )
        message_tokens = sum(
            self.estimator.estimate_message(message) for message in messages
        )
        total_tokens = message_tokens + tool_tokens
        if total_tokens > capacity:
            raise AssertionError("context selection exceeded its token budget")

        included = tuple(source.selection for source in selected_sources)
        omitted = tuple(
            sorted(
                omitted_sources,
                key=lambda item: (
                    next(
                        (
                            index
                            for index, source in enumerate(effective_sources)
                            if source.source_id == item.source_id
                        ),
                        len(effective_sources),
                    ),
                    item.source_id,
                ),
            )
        )
        compaction_report = CompactionReport(
            original_source_tokens=prepared.original_tokens,
            final_source_tokens=sum(
                source.selection.estimated_tokens for source in selected_sources
            ),
            deduplicated_source_ids=tuple(
                selection.source_id
                for selection in omitted
                if selection.disposition is SourceDisposition.DEDUPLICATED
            ),
            truncated_source_ids=tuple(
                selection.source_id
                for selection in included
                if selection.disposition is SourceDisposition.TRUNCATED
            ),
            artifactized_source_ids=tuple(
                selection.source_id
                for selection in included
                if selection.disposition is SourceDisposition.ARTIFACT_REF
            ),
            omitted_source_ids=tuple(
                selection.source_id
                for selection in omitted
                if selection.disposition is SourceDisposition.OMITTED
            ),
            dropped_recent_messages=dropped_recent,
            working_memory=memory_compaction,
        )
        token_estimate = TokenEstimate(
            total_tokens=total_tokens,
            message_tokens=message_tokens,
            tool_schema_tokens=tool_tokens,
            budget_tokens=capacity,
            remaining_tokens=capacity - total_tokens,
        )
        digest = self._digest(
            messages=messages,
            tools=request.tool_schemas,
            included=included,
            omitted=omitted,
            workspace_revision=request.workspace_revision,
        )
        context_id = request.context_id or f"ctx-{digest[:20]}"
        return ContextEnvelope(
            context_id=context_id,
            messages=messages,
            tool_schemas=request.tool_schemas,
            included_sources=included,
            omitted_sources=omitted,
            compaction_report=compaction_report,
            token_estimate=token_estimate,
            digest=digest,
            reserved_output_tokens=request.budget.reserved_output_tokens,
            run_id=request.run_id,
            workspace_revision=request.workspace_revision,
            metadata=request.metadata,
        )

    def _system_message(self, request: ContextRequest) -> Message:
        content_parts = list(request.system_instructions)
        content_parts.append(self.policy.injection_notice)
        return Message(
            role=MessageRole.SYSTEM,
            content="\n\n".join(content_parts),
            source_label=ContextSourceLabel.SYSTEM_INSTRUCTION.value,
            metadata={"authority": "system"},
        )

    @staticmethod
    def _project_message(request: ContextRequest) -> Message | None:
        if not request.project_instructions:
            return None
        return Message(
            role=MessageRole.DEVELOPER,
            content="\n\n".join(request.project_instructions),
            source_label=ContextSourceLabel.PROJECT_POLICY.value,
            metadata={"authority": "project_policy"},
        )

    def _recent_message_groups(
        self,
        messages: Sequence[Message],
    ) -> tuple[_MessageGroup, ...]:
        groups: list[_MessageGroup] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role is MessageRole.TOOL:
                raise ValueError("recent_messages contains an orphaned tool result")
            if message.role is MessageRole.ASSISTANT and message.tool_proposals:
                expected = {
                    proposal.tool_call_id for proposal in message.tool_proposals
                }
                batch = [message]
                observed: set[str] = set()
                cursor = index + 1
                while cursor < len(messages) and messages[cursor].role is MessageRole.TOOL:
                    tool_message = messages[cursor]
                    if tool_message.tool_call_id not in expected:
                        raise ValueError(
                            "recent_messages tool result does not match assistant proposal"
                        )
                    if tool_message.tool_call_id in observed:
                        raise ValueError("recent_messages contains duplicate tool results")
                    observed.add(str(tool_message.tool_call_id))
                    batch.append(tool_message)
                    cursor += 1
                if observed != expected:
                    raise ValueError(
                        "recent_messages contains an incomplete assistant/tool batch"
                    )
                priority = 50
                groups.append(
                    _MessageGroup(
                        index=index,
                        messages=tuple(batch),
                        priority=priority,
                    )
                )
                index = cursor
                continue
            role_priority = {
                MessageRole.SYSTEM: 10,
                MessageRole.DEVELOPER: 10,
                MessageRole.USER: 20,
                MessageRole.ASSISTANT: 75,
            }
            groups.append(
                _MessageGroup(
                    index=index,
                    messages=(message,),
                    priority=role_priority.get(message.role, 75),
                )
            )
            index += 1
        return tuple(groups)

    def _apply_recent_message_cap(
        self,
        groups: Sequence[_MessageGroup],
    ) -> tuple[tuple[_MessageGroup, ...], int]:
        if not groups or self.policy.max_recent_messages == 0:
            return (), sum(group.size for group in groups)
        selected: list[_MessageGroup] = []
        count = 0
        for group in reversed(groups):
            if selected and count + group.size > self.policy.max_recent_messages:
                continue
            selected.append(group)
            count += group.size
            if count >= self.policy.max_recent_messages:
                break
        selected.reverse()
        dropped = sum(group.size for group in groups) - count
        return tuple(selected), dropped

    @staticmethod
    def _digest(
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        included: Sequence[SourceSelection],
        omitted: Sequence[SourceSelection],
        workspace_revision: str | None,
    ) -> str:
        payload = {
            "messages": [message.to_dict() for message in messages],
            "tools": [tool.to_dict() for tool in tools],
            "included_sources": [source.to_dict() for source in included],
            "omitted_sources": [source.to_dict() for source in omitted],
            "workspace_revision": workspace_revision,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

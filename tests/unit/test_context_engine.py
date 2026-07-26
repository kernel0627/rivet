from __future__ import annotations

import unittest

from rivet.context.budget import ContextBudget, ContextBudgetExceeded
from rivet.context.compaction import SourceDisposition
from rivet.context.engine import ContextRequest, DefaultContextEngine
from rivet.context.policy import (
    ArtifactRef,
    ContextSource,
    ContextSourceLabel,
)
from rivet.context.working_memory import WorkingMemory
from rivet.model.types import (
    Message,
    MessageRole,
    ToolProposal,
    ToolSchema,
)


class ContextEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = DefaultContextEngine()

    async def test_build_labels_sources_and_produces_model_request(self) -> None:
        tool = ToolSchema(
            name="read_file",
            description="Read a file",
            parameters={"type": "object"},
        )
        envelope = await self.engine.build(
            ContextRequest(
                objective="Explain the parser",
                budget=ContextBudget(
                    max_input_tokens=2_000,
                    reserved_output_tokens=300,
                ),
                system_instructions=("Follow workspace boundaries.",),
                project_instructions=("Use Python 3.10.",),
                sources=(
                    ContextSource(
                        source_id="parser-source",
                        label=ContextSourceLabel.REPOSITORY_CONTENT,
                        content="def parse(value):\n    return value\n",
                    ),
                ),
                tool_schemas=(tool,),
                run_id="run-1",
                workspace_revision="revision-1",
            )
        )

        self.assertEqual(envelope.messages[0].role, MessageRole.SYSTEM)
        self.assertIn("untrusted data", envelope.messages[0].content or "")
        self.assertEqual(envelope.messages[-1].content, "Explain the parser")
        source_message = next(
            message
            for message in envelope.messages
            if message.source_label == ContextSourceLabel.REPOSITORY_CONTENT.value
        )
        self.assertEqual(source_message.metadata["source_id"], "parser-source")
        self.assertIn("REPOSITORY_CONTENT", source_message.content or "")
        model_request = envelope.to_model_request(model="fake-model")
        self.assertEqual(model_request.tools, (tool,))
        self.assertEqual(model_request.max_output_tokens, 300)
        self.assertEqual(model_request.metadata["context_id"], envelope.context_id)
        self.assertLessEqual(
            envelope.token_estimate.total_tokens,
            envelope.token_estimate.budget_tokens,
        )

    async def test_digest_is_stable_for_identical_inputs(self) -> None:
        request = ContextRequest(
            objective="Inspect",
            budget=ContextBudget(max_input_tokens=1_000),
            sources=(
                ContextSource(
                    source_id="source",
                    label=ContextSourceLabel.RUN_FACT,
                    content="fact",
                ),
            ),
        )

        first = await self.engine.build(request)
        second = await self.engine.build(request)

        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.context_id, second.context_id)

    async def test_priority_keeps_high_value_source_before_background(self) -> None:
        high = ContextSource(
            source_id="confirmed",
            label=ContextSourceLabel.RUN_FACT,
            content="The failing function is parse().",
        )
        background = ContextSource(
            source_id="background",
            label=ContextSourceLabel.BACKGROUND,
            content="background " * 1_000,
        )
        probe = await self.engine.build(
            ContextRequest(
                objective="Fix it",
                budget=ContextBudget(max_input_tokens=5_000),
                sources=(high,),
            )
        )
        tight_capacity = probe.token_estimate.total_tokens + 10

        envelope = await self.engine.build(
            ContextRequest(
                objective="Fix it",
                budget=ContextBudget(
                    max_input_tokens=tight_capacity,
                    min_truncation_tokens=48,
                ),
                sources=(high, background),
            )
        )

        self.assertIn("confirmed", {item.source_id for item in envelope.included_sources})
        omitted = {item.source_id for item in envelope.omitted_sources}
        self.assertIn("background", omitted)

    async def test_large_source_uses_existing_artifact_reference(self) -> None:
        artifact = ArtifactRef(
            artifact_id="artifact-1",
            uri="artifact://sha256/example",
            byte_size=20_000,
            sha256="a" * 64,
            summary="large test output",
        )
        envelope = await self.engine.build(
            ContextRequest(
                objective="Inspect test output",
                budget=ContextBudget(
                    max_input_tokens=2_000,
                    max_inline_source_tokens=80,
                ),
                sources=(
                    ContextSource(
                        source_id="test-output",
                        label=ContextSourceLabel.TOOL_OUTPUT,
                        content="failure\n" * 3_000,
                        artifact_ref=artifact,
                    ),
                ),
            )
        )

        selection = envelope.included_sources[0]
        self.assertEqual(selection.disposition, SourceDisposition.ARTIFACT_REF)
        self.assertEqual(selection.artifact_ref, artifact)
        self.assertIn(
            "artifact://sha256/example",
            next(
                message.content or ""
                for message in envelope.messages
                if message.metadata.get("source_id") == "test-output"
            ),
        )

    async def test_required_low_priority_source_preempts_optional_sources(self) -> None:
        required = ContextSource(
            source_id="required-background",
            label=ContextSourceLabel.BACKGROUND,
            content="required material " * 12,
            required=True,
        )
        optional = ContextSource(
            source_id="optional-fact",
            label=ContextSourceLabel.RUN_FACT,
            content="optional material " * 12,
        )
        probe = await self.engine.build(
            ContextRequest(
                objective="Inspect",
                budget=ContextBudget(max_input_tokens=3_000),
                sources=(required,),
            )
        )
        tight_capacity = probe.token_estimate.total_tokens + 10

        envelope = await self.engine.build(
            ContextRequest(
                objective="Inspect",
                budget=ContextBudget(
                    max_input_tokens=tight_capacity,
                    min_truncation_tokens=48,
                ),
                sources=(optional, required),
            )
        )

        self.assertIn(
            "required-background",
            {selection.source_id for selection in envelope.included_sources},
        )
        self.assertIn(
            "optional-fact",
            {selection.source_id for selection in envelope.omitted_sources},
        )

    async def test_exact_duplicate_keeps_required_higher_authority_source(self) -> None:
        envelope = await self.engine.build(
            ContextRequest(
                objective="Inspect",
                budget=ContextBudget(max_input_tokens=1_500),
                sources=(
                    ContextSource(
                        source_id="background-copy",
                        label=ContextSourceLabel.BACKGROUND,
                        content="same material",
                    ),
                    ContextSource(
                        source_id="required-fact",
                        label=ContextSourceLabel.RUN_FACT,
                        content="same material",
                        required=True,
                    ),
                ),
            )
        )

        self.assertEqual(envelope.included_sources[0].source_id, "required-fact")
        duplicate = next(
            item
            for item in envelope.omitted_sources
            if item.disposition is SourceDisposition.DEDUPLICATED
        )
        self.assertEqual(duplicate.source_id, "background-copy")
        self.assertEqual(duplicate.reason, "duplicate_of:required-fact")

    async def test_assistant_and_tool_results_are_an_atomic_recent_group(self) -> None:
        proposal = ToolProposal.from_arguments(
            tool_call_id="call-1",
            ordinal=0,
            name="read_file",
            arguments={"path": "main.py"},
        )
        assistant = Message(
            role=MessageRole.ASSISTANT,
            tool_proposals=(proposal,),
        )
        tool_result = Message(
            role=MessageRole.TOOL,
            content="file contents",
            tool_call_id="call-1",
        )
        envelope = await self.engine.build(
            ContextRequest(
                objective="Continue",
                budget=ContextBudget(max_input_tokens=1_500),
                recent_messages=(assistant, tool_result),
            )
        )

        self.assertIn(assistant, envelope.messages)
        self.assertIn(tool_result, envelope.messages)
        self.assertLess(
            envelope.messages.index(assistant),
            envelope.messages.index(tool_result),
        )

        with self.assertRaisesRegex(ValueError, "orphaned"):
            await self.engine.build(
                ContextRequest(
                    objective="Continue",
                    budget=ContextBudget(max_input_tokens=1_500),
                    recent_messages=(tool_result,),
                )
            )

    async def test_working_memory_is_compacted_and_labeled(self) -> None:
        memory = WorkingMemory(
            objective="Repair parser",
            hypotheses=tuple(f"hypothesis {index}" for index in range(30)),
            verification_failures=("test_parse failed",),
        )
        envelope = await self.engine.build(
            ContextRequest(
                objective="Continue repair",
                budget=ContextBudget(
                    max_input_tokens=1_500,
                    max_working_memory_tokens=100,
                ),
                working_memory=memory,
            )
        )

        self.assertIsNotNone(envelope.compaction_report.working_memory)
        self.assertGreater(
            envelope.compaction_report.working_memory.dropped_by_section.get(
                "hypotheses",
                0,
            ),
            0,
        )
        self.assertIn(
            "run-working-memory",
            {item.source_id for item in envelope.included_sources},
        )

    async def test_mandatory_base_context_failure_is_explicit(self) -> None:
        with self.assertRaises(ContextBudgetExceeded) as raised:
            await self.engine.build(
                ContextRequest(
                    objective="Very long objective " * 100,
                    budget=ContextBudget(max_input_tokens=20),
                )
            )

        self.assertIn("mandatory", raised.exception.reason)


if __name__ == "__main__":
    unittest.main()

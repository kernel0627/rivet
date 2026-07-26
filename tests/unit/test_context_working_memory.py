from __future__ import annotations

import unittest

from rivet.context.budget import HeuristicTokenEstimator
from rivet.context.working_memory import (
    WorkingMemory,
    WorkingMemoryPolicy,
    WorkingMemoryUpdate,
)


class WorkingMemoryTests(unittest.TestCase):
    def test_updates_deduplicate_bound_and_complete_pending_items(self) -> None:
        memory = WorkingMemory(objective="Fix parser", pending_items=("add test",))
        updated = memory.apply(
            WorkingMemoryUpdate(
                confirmed_facts=("Parser is in parser.py", "Parser is in parser.py"),
                pending_items=("run tests",),
                completed_items=("add test",),
            ),
            policy=WorkingMemoryPolicy(max_items_per_section=3),
        )

        self.assertEqual(updated.confirmed_facts, ("Parser is in parser.py",))
        self.assertEqual(updated.pending_items, ("run tests",))
        self.assertEqual(updated.revision, 1)

    def test_compaction_drops_low_value_old_items_before_current_failures(self) -> None:
        memory = WorkingMemory(
            objective="Fix parser while preserving behavior",
            hypotheses=tuple(f"hypothesis {index}" for index in range(20)),
            verification_failures=("test_parser still fails at line 8",),
            pending_items=("repair parser",),
        )
        estimator = HeuristicTokenEstimator()

        compacted, report = memory.compact(max_tokens=80, estimator=estimator)

        self.assertLessEqual(estimator.estimate_text(compacted.render()), 80)
        self.assertGreater(report.dropped_by_section.get("hypotheses", 0), 0)
        self.assertIn("test_parser", compacted.verification_failures[0])

    def test_round_trip(self) -> None:
        memory = WorkingMemory(
            objective="Inspect",
            relevant_files=("main.py",),
            revision=3,
        )

        self.assertEqual(WorkingMemory.from_dict(memory.to_dict()), memory)


if __name__ == "__main__":
    unittest.main()

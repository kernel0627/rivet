from __future__ import annotations

import json
import unittest

from rich.console import Console

from rivet.domain import Event, EventActor
from rivet.interfaces.tui.render import TerminalEventRenderer


class TerminalEventRendererTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.console = Console(record=True, force_terminal=False, width=100)
        self.renderer = TerminalEventRenderer(self.console)
        self.sequence = 0

    def event(self, event_type: str, payload: dict | None = None) -> Event:
        self.sequence += 1
        return Event.create(
            session_id="session_tui",
            run_id="run_tui",
            sequence=self.sequence,
            event_type=event_type,
            actor=EventActor.RUNTIME,
            payload=payload,
        )

    async def test_renders_stable_stage_labels(self) -> None:
        await self.renderer(self.event("turn.started", {"ordinal": 1}))
        await self.renderer(self.event("tool.started", {"tool_name": "read_file"}))
        await self.renderer(self.event("tool.started", {"tool_name": "search_text"}))
        await self.renderer(self.event("tool.started", {"tool_name": "run_tests"}))
        await self.renderer(self.event("run.paused", {"reason": "permission_required"}))
        await self.renderer(
            self.event(
                "permission.scope_granted",
                {"permission_class": "workspace_write", "scope": "run"},
            )
        )
        await self.renderer(
            self.event(
                "checkpoint.rewound",
                {"restored_paths": ["main.py"], "removed_paths": []},
            )
        )
        await self.renderer(self.event("run.completed"))

        rendered = self.console.export_text()
        self.assertIn("[Plan] Turn 1", rendered)
        self.assertIn("[Read] read_file", rendered)
        self.assertIn("[Search] search_text", rendered)
        self.assertIn("[Test] run_tests", rendered)
        self.assertIn("[Continue] Paused: permission_required", rendered)
        self.assertIn(
            "[Continue] Allowed for this run: workspace_write",
            rendered,
        )
        self.assertIn("[Edit] Rewound: main.py", rendered)
        self.assertIn("[Result] Completed", rendered)

    async def test_successful_patch_renders_changed_paths_and_diff(self) -> None:
        result = {
            "status": "success",
            "content": [
                {
                    "kind": "diff",
                    "paths": ["main.py"],
                    "diff": ("--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"),
                }
            ],
        }
        await self.renderer(
            self.event(
                "tool.completed",
                {
                    "tool_name": "apply_patch",
                    "status": "success",
                    "changed_paths": ["main.py"],
                    "message": {"content": json.dumps(result)},
                },
            )
        )

        rendered = self.console.export_text()
        self.assertIn("[Edit] success: apply_patch", rendered)
        self.assertIn("Changed: main.py", rendered)
        self.assertIn("-value = 1", rendered)
        self.assertIn("+value = 2", rendered)


if __name__ == "__main__":
    unittest.main()

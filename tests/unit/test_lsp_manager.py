from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.code_intelligence.lsp import LanguageServerConfig, LspError, LspManager


class FakeLanguageService:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0
        self.opened: list[tuple[Path, str, int]] = []
        self.changed: list[tuple[Path, str, int]] = []

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    async def open_document(self, path, *, text, language_id="python", version=1):
        self.opened.append((path, text, version))

    async def change_document(self, path, *, text, version):
        self.changed.append((path, text, version))

    async def close_document(self, path):
        return None

    async def definition(self, path, *, line, character):
        return ()

    async def references(self, path, *, line, character, include_declaration=True):
        return ()

    async def hover(self, path, *, line, character):
        return None

    async def document_symbols(self, path):
        return []

    async def workspace_symbols(self, query):
        return []

    def diagnostics(self, path):
        return ()


class LspManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "example.py"
        self.path.write_text("value = 1\n", encoding="utf-8")
        self.created: list[FakeLanguageService] = []

        def factory(config, root):
            service = FakeLanguageService()
            self.created.append(service)
            return service

        self.manager = LspManager(
            self.root,
            (
                LanguageServerConfig(
                    language_id="python",
                    command=("fake-lsp",),
                    extensions=(".py", ".pyi"),
                ),
            ),
            client_factory=factory,
        )

    async def asyncTearDown(self) -> None:
        await self.manager.close()
        self.temporary.cleanup()

    async def test_reuses_service_and_only_syncs_changed_content(self) -> None:
        service, target = await self.manager.sync_document(
            self.path,
            workspace_revision="rev-1",
        )
        again, _ = await self.manager.sync_document(
            self.path,
            workspace_revision="rev-1",
        )
        self.assertIs(service, again)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0].started, 1)
        self.assertEqual(len(self.created[0].opened), 1)
        self.assertEqual(self.created[0].changed, [])

        await self.manager.sync_document(
            target,
            text="value = 2\n",
            workspace_revision="rev-2",
        )
        self.assertEqual(self.created[0].changed[0][2], 2)
        self.assertEqual(self.manager.workspace_revision("python"), "rev-2")

    async def test_rejects_document_outside_workspace(self) -> None:
        with self.assertRaises(LspError):
            await self.manager.sync_document(Path("/tmp/outside.py"), text="")

    async def test_restart_replaces_service(self) -> None:
        first = await self.manager.service("python")
        second = await self.manager.restart("python")
        self.assertIsNot(first, second)
        self.assertEqual(self.created[0].closed, 1)


if __name__ == "__main__":
    unittest.main()

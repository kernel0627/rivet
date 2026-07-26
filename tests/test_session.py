from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rivet.state.session import Session, StopReason
from rivet.state.store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions")
            session = Session.create(task="inspect", workspace=root, max_turns=5)
            session.turn_count = 2
            session.stop_reason = StopReason.FINAL_ANSWER
            session.final_response = "done"

            store.save(session)
            loaded = store.load(session.id)

            self.assertEqual(loaded.to_dict(), session.to_dict())


if __name__ == "__main__":
    unittest.main()

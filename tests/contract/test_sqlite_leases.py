from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from rivet.domain import Event, EventActor, Run, Session, Workspace
from rivet.domain.common import utc_now
from rivet.state.protocol import LeaseConflictError, StateMutation
from rivet.state.sqlite import SQLiteStateStore


class SQLiteLeaseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = SQLiteStateStore(root / "state.sqlite3")
        workspace = Workspace.create(root / "workspace")
        session = Session.create(workspace.workspace_id)
        self.run = Run.create(session.session_id, "lease", workspace.current_revision)
        self.store.commit(
            StateMutation(
                workspaces=(workspace,),
                sessions=(session,),
                run=self.run,
                events=(
                    Event.create(
                        session_id=session.session_id,
                        run_id=self.run.run_id,
                        sequence=1,
                        event_type="run.created",
                        actor=EventActor.RUNTIME,
                    ),
                ),
            )
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_live_lease_is_exclusive_and_expired_lease_can_be_taken_over(self) -> None:
        now = utc_now()
        first = self.store.acquire_run_lease(
            self.run.run_id,
            "runtime-a",
            ttl_seconds=10,
            now=now,
        )
        with self.assertRaises(LeaseConflictError):
            self.store.acquire_run_lease(
                self.run.run_id,
                "runtime-b",
                ttl_seconds=10,
                now=now + timedelta(seconds=1),
            )

        second = self.store.acquire_run_lease(
            self.run.run_id,
            "runtime-b",
            ttl_seconds=10,
            now=now + timedelta(seconds=11),
        )
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(second.generation, first.generation + 1)

    def test_lease_renew_and_release_require_token(self) -> None:
        now = utc_now()
        lease = self.store.acquire_run_lease(
            self.run.run_id,
            "runtime-a",
            ttl_seconds=10,
            now=now,
        )
        with self.assertRaises(LeaseConflictError):
            self.store.renew_run_lease(
                self.run.run_id,
                "wrong-token",
                ttl_seconds=10,
                now=now + timedelta(seconds=1),
            )
        renewed = self.store.renew_run_lease(
            self.run.run_id,
            lease.token,
            ttl_seconds=20,
            now=now + timedelta(seconds=1),
        )
        self.assertGreater(renewed.expires_at, lease.expires_at)
        self.assertFalse(self.store.release_run_lease(self.run.run_id, "wrong-token"))
        self.assertTrue(self.store.release_run_lease(self.run.run_id, lease.token))


if __name__ == "__main__":
    unittest.main()

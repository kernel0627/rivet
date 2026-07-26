from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rivet.state.session import Session


@dataclass(frozen=True)
class SessionStore:
    directory: Path

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        if not session_id.isalnum():
            raise ValueError("session id contains unsupported characters")
        return self.directory / f"{session_id}.json"

    def save(self, session: Session) -> Path:
        session.touch()
        destination = self.path_for(session.id)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination

    def load(self, session_id: str) -> Session:
        value = json.loads(self.path_for(session_id).read_text(encoding="utf-8"))
        return Session.from_dict(value)


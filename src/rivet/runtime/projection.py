from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rivet.domain import Event
from rivet.model.types import Message


@dataclass(frozen=True)
class EventProjection:
    messages: tuple[Message, ...]
    last_sequence: int
    repeat_override_sequence: int | None = None


def project_events(events: Sequence[Event]) -> EventProjection:
    messages: list[Message] = []
    override_sequence: int | None = None
    last_sequence = 0
    for event in events:
        last_sequence = max(last_sequence, event.sequence)
        if event.event_type in {
            "user.message",
            "model_call.completed",
            "tool.completed",
            "verification.completed",
            "reviewer.completed",
        }:
            raw_message = event.payload.get("message")
            if isinstance(raw_message, dict):
                messages.append(Message.from_dict(raw_message))
        elif event.event_type == "repeat.override":
            override_sequence = event.sequence
    return EventProjection(
        messages=tuple(messages),
        last_sequence=last_sequence,
        repeat_override_sequence=override_sequence,
    )

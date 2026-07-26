from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rivet.domain import Event
from rivet.tools.contracts import PreparedTool


@dataclass(frozen=True)
class ActionFingerprint:
    action_key: str
    digest: str
    workspace_revision: str
    context_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action_key": self.action_key,
            "digest": self.digest,
            "workspace_revision": self.workspace_revision,
            "context_digest": self.context_digest,
        }


@dataclass(frozen=True)
class RepetitionAssessment:
    repeated: bool
    prior_consecutive_count: int
    action_key: str
    previous_result_digest: str | None = None


def fingerprint_action(
    prepared: PreparedTool,
    *,
    workspace_revision: str,
    context_digest: str,
) -> ActionFingerprint:
    base_payload = {
        "tool_name": prepared.name,
        "tool_version": prepared.version,
        "normalized_arguments": dict(prepared.normalized_arguments),
        "resolved_targets": [
            {
                "path": target.relative_path,
                "existed": target.existed,
            }
            for target in prepared.resolved_targets
        ],
        "effect": prepared.effect.value,
        "workspace_revision": workspace_revision,
    }
    action_key = _digest(base_payload)
    digest = _digest({**base_payload, "context_digest": context_digest})
    return ActionFingerprint(
        action_key=action_key,
        digest=digest,
        workspace_revision=workspace_revision,
        context_digest=context_digest,
    )


def assess_repetition(
    events: Sequence[Event],
    fingerprint: ActionFingerprint,
    *,
    max_consecutive: int,
    override_after_sequence: int | None = None,
) -> RepetitionAssessment:
    if max_consecutive <= 0:
        raise ValueError("max_consecutive must be positive")
    count = 0
    expected_result_digest: str | None = None
    for event in reversed(events):
        if event.event_type != "tool.completed":
            continue
        if override_after_sequence is not None and event.sequence <= override_after_sequence:
            break
        payload = event.payload
        if payload.get("action_key") != fingerprint.action_key:
            break
        result_digest = payload.get("result_digest")
        if not isinstance(result_digest, str):
            break
        if expected_result_digest is None:
            expected_result_digest = result_digest
        elif result_digest != expected_result_digest:
            break
        count += 1
    return RepetitionAssessment(
        repeated=count >= max_consecutive,
        prior_consecutive_count=count,
        action_key=fingerprint.action_key,
        previous_result_digest=expected_result_digest,
    )


def digest_result(value: Any) -> str:
    return _digest(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

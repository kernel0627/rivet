from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from prompt_toolkit import PromptSession
from rich.console import Console

from rivet.application import ApplicationHarness
from rivet.domain import RunStatus
from rivet.interfaces.tui.render import TerminalEventRenderer


async def run_with_events(
    application: ApplicationHarness,
    objective: str,
    *,
    console: Console | None = None,
) -> Any:
    renderer = TerminalEventRenderer(console)
    subscription = application.event_stream.open_subscription()

    async def consume() -> None:
        async for event in subscription:
            await renderer(event)

    consumer = asyncio.create_task(consume())
    try:
        return await application.service.run(objective)
    finally:
        await subscription.close()
        await consumer


async def resume_with_events(
    application: ApplicationHarness,
    *,
    run_id: str,
    pause_token: str,
    console: Console,
    permission_decisions: dict[str, str] | None = None,
    allow_repeated_action_once: bool = False,
    user_message: str | None = None,
) -> Any:
    renderer = TerminalEventRenderer(console)
    subscription = application.event_stream.open_subscription()

    async def consume() -> None:
        async for event in subscription:
            await renderer(event)

    consumer = asyncio.create_task(consume())
    try:
        return await application.service.resume(
            run_id,
            pause_token,
            permission_decisions=permission_decisions,
            allow_repeated_action_once=allow_repeated_action_once,
            user_message=user_message,
        )
    finally:
        await subscription.close()
        await consumer


async def rewind_with_events(
    application: ApplicationHarness,
    *,
    run_id: str,
    checkpoint_id: str,
    console: Console,
) -> Any:
    renderer = TerminalEventRenderer(console)
    subscription = application.event_stream.open_subscription()

    async def consume() -> None:
        async for event in subscription:
            await renderer(event)

    consumer = asyncio.create_task(consume())
    try:
        return await application.service.rewind(run_id, checkpoint_id)
    finally:
        await subscription.close()
        await consumer


async def run_interactive(
    application: ApplicationHarness,
    objective: str,
    *,
    console: Console | None = None,
) -> Any:
    output = console or Console()
    outcome = await run_with_events(application, objective, console=output)
    while outcome.run.status is RunStatus.PAUSED:
        decision = outcome.run.stop_decision
        if decision is None:
            return outcome
        if decision.reason == "permission_required":
            digest = str(decision.evidence.get("prepared_digest", ""))
            permission_class = str(decision.evidence.get("permission_class", "this permission"))
            answer = await _prompt(
                f"Allow {decision.evidence.get('tool_name', 'tool')} "
                f"({permission_class})? [o]nce / [r]un / [d]eny "
            )
            normalized = answer.strip().lower()
            permission = (
                "allow"
                if normalized in {"o", "once", "y", "yes"}
                else "allow_run"
                if normalized in {"r", "run"}
                else "deny"
            )
            outcome = await resume_with_events(
                application,
                run_id=outcome.run.run_id,
                pause_token=outcome.run.pause_token or "",
                console=output,
                permission_decisions={digest: permission},
            )
            continue
        if decision.reason == "repeated_action":
            answer = await _prompt("Allow this repeated action once? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                return outcome
            outcome = await resume_with_events(
                application,
                run_id=outcome.run.run_id,
                pause_token=outcome.run.pause_token or "",
                console=output,
                allow_repeated_action_once=True,
            )
            continue
        answer = await _prompt("Run paused: [m]essage and resume / [s]top ")
        if answer.strip().lower() not in {"m", "message"}:
            return outcome
        message = await _prompt("Additional instruction: ")
        if not message.strip():
            continue
        outcome = await resume_with_events(
            application,
            run_id=outcome.run.run_id,
            pause_token=outcome.run.pause_token or "",
            console=output,
            user_message=message,
        )
    if outcome.run.status is RunStatus.COMPLETED:
        outcome = await _review_completed_run(application, outcome, output)
    return outcome


async def _review_completed_run(
    application: ApplicationHarness,
    outcome: Any,
    console: Console,
) -> Any:
    checkpoints = tuple(
        checkpoint
        for checkpoint in application.service.checkpoints(outcome.run.run_id)
        if checkpoint.status.value == "READY"
    )
    if not checkpoints:
        return outcome
    while True:
        answer = (
            (await _prompt("Review checkpointed changes: [k]eep / [l]ist / [r]ewind latest "))
            .strip()
            .lower()
        )
        if answer in {"l", "list"}:
            for checkpoint in checkpoints:
                console.print(
                    f"{checkpoint.checkpoint_id}: " + ", ".join(checkpoint.scope),
                    markup=False,
                )
            continue
        if answer in {"r", "rewind"}:
            await rewind_with_events(
                application,
                run_id=outcome.run.run_id,
                checkpoint_id=checkpoints[-1].checkpoint_id,
                console=console,
            )
            current = application.service.inspect(outcome.run.run_id)
            return replace(
                outcome,
                snapshot=replace(outcome.snapshot, run=current),
            )
        return outcome


async def _prompt(message: str) -> str:
    session: PromptSession[str] = PromptSession()
    return await session.prompt_async(message)

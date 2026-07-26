from __future__ import annotations

import asyncio
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
        )
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
            answer = await _prompt(
                f"Allow {decision.evidence.get('tool_name', 'tool')}? [y/N] "
            )
            permission = "allow" if answer.strip().lower() in {"y", "yes"} else "deny"
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
        return outcome
    return outcome


async def _prompt(message: str) -> str:
    session: PromptSession[str] = PromptSession()
    return await session.prompt_async(message)

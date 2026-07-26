from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rivet.application import build_application
from rivet.domain import Run, RunStatus
from rivet.observability import Redactor

HEADLESS_SCHEMA_VERSION = 1


def outcome_payload(run: Run) -> dict[str, Any]:
    decision = run.stop_decision
    return {
        "schema_version": HEADLESS_SCHEMA_VERSION,
        "ok": run.status is RunStatus.COMPLETED,
        "run_id": run.run_id,
        "session_id": run.session_id,
        "status": run.status.value,
        "final_response": run.final_response,
        "decision": decision.to_dict() if decision else None,
        "pause": (
            {
                "token": run.pause_token,
                "reason": decision.reason if decision else None,
                "resume_requirements": (
                    list(decision.resume_requirements) if decision else []
                ),
            }
            if run.status is RunStatus.PAUSED
            else None
        ),
        "usage": run.usage.to_dict(),
        "revision": run.revision,
    }


def error_payload(error: BaseException) -> dict[str, Any]:
    return {
        "schema_version": HEADLESS_SCHEMA_VERSION,
        "ok": False,
        "error": {
            "type": type(error).__name__,
            "message": Redactor().exception_summary(error),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rivet-headless")
    parser.add_argument("task")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-turns", type=int)
    return parser


async def _run(args: argparse.Namespace) -> int:
    overrides = _overrides(args)
    application = build_application(Path(args.workspace), overrides=overrides)
    try:
        outcome = await application.service.run(args.task)
        print(json.dumps(outcome_payload(outcome.run), ensure_ascii=False))
        return _exit_code(outcome.run.status)
    finally:
        await application.close()


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {}
    model: dict[str, Any] = {}
    if args.model:
        model["model"] = args.model
    if args.base_url:
        model["base_url"] = args.base_url
    if model:
        result["model"] = model
    if args.max_turns:
        result["runtime"] = {"max_turns": args.max_turns}
    return result


def _exit_code(status: RunStatus) -> int:
    if status is RunStatus.COMPLETED:
        return 0
    if status is RunStatus.PAUSED:
        return 3
    if status is RunStatus.CANCELLED:
        return 130
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print(json.dumps(error_payload(KeyboardInterrupt()), ensure_ascii=False))
        return 130
    except Exception as error:
        print(json.dumps(error_payload(error), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console

from rivet.application import build_application
from rivet.code_intelligence.benchmark import (
    benchmark_retrieval,
    load_benchmark_queries,
)
from rivet.code_intelligence.lsp import discover_python_server
from rivet.configuration import load_config
from rivet.evaluation import (
    EvaluationRunner,
    LiveEvalLimits,
    RivetEvalExecutor,
    benchmark_evaluation,
    build_live_preflight,
    load_baseline,
    load_jsonl,
)
from rivet.interfaces.headless import outcome_payload
from rivet.interfaces.tui import run_interactive
from rivet.model.providers import resolve_provider
from rivet.observability import Redactor
from rivet.state.layout import StateLayout
from rivet.tools.builtins import (
    ApplyPatchTool,
    GitDiffTool,
    GitStatusTool,
    ListFilesTool,
    ReadFileTool,
    RunCommandTool,
    RunTestsTool,
    SearchTextTool,
)
from rivet.tools.builtins.code_intelligence import code_intelligence_tools


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rivet",
        description="A terminal-native, permission-aware coding agent.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Start one coding task.")
    _common_runtime_arguments(run)
    run.add_argument("task")
    run.add_argument("--json", action="store_true")
    run.add_argument("--no-tui", action="store_true")

    resume = commands.add_parser("resume", help="Resume a paused Run.")
    _common_runtime_arguments(resume)
    resume.add_argument("run_id")
    resume.add_argument("pause_token")
    resume.add_argument("--message")
    resume.add_argument(
        "--permission",
        action="append",
        default=[],
        metavar="DIGEST=allow|deny",
    )
    resume.add_argument("--allow-repeat", action="store_true")
    resume.add_argument("--json", action="store_true")

    inspect_parser = commands.add_parser("inspect", help="Inspect persisted Run state.")
    inspect_parser.add_argument("run_id")
    inspect_parser.add_argument("--workspace", default=".")
    inspect_parser.add_argument("--json", action="store_true")

    cancel = commands.add_parser("cancel", help="Cancel a non-terminal Run.")
    _common_runtime_arguments(cancel)
    cancel.add_argument("run_id")
    cancel.add_argument("--reason", default="user_cancelled")

    chat = commands.add_parser(
        "chat",
        help="Run multiple related tasks in one persistent Session.",
    )
    _common_runtime_arguments(chat)
    chat.add_argument("--session-id")

    sessions = commands.add_parser("sessions", help="List workspace Sessions.")
    sessions.add_argument("--workspace", default=".")
    sessions.add_argument("--json", action="store_true")

    runs = commands.add_parser("runs", help="List Runs in one Session.")
    runs.add_argument("session_id")
    runs.add_argument("--workspace", default=".")
    runs.add_argument("--json", action="store_true")

    events = commands.add_parser(
        "events",
        help="Query or export the append-only Event trace for a Run.",
    )
    events.add_argument("run_id")
    events.add_argument("--workspace", default=".")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--jsonl", action="store_true")

    checkpoints = commands.add_parser(
        "checkpoints",
        help="List checkpoints recorded for a Run.",
    )
    checkpoints.add_argument("run_id")
    checkpoints.add_argument("--workspace", default=".")
    checkpoints.add_argument("--json", action="store_true")

    rewind = commands.add_parser(
        "rewind",
        help="Restore files from a checkpoint if they have not changed externally.",
    )
    rewind.add_argument("run_id")
    rewind.add_argument("checkpoint_id")
    rewind.add_argument("--workspace", default=".")
    rewind.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="Inspect local configuration.")
    doctor.add_argument("--workspace", default=".")
    doctor.add_argument("--json", action="store_true")

    tools = commands.add_parser("tools", help="List built-in tools.")
    tools.add_argument("--json", action="store_true")

    eval_parser = commands.add_parser(
        "eval",
        help="Run a Rivet evaluation dataset.",
    )
    eval_parser.add_argument("--dataset", type=Path)
    eval_parser.add_argument(
        "--mode",
        choices=("offline", "live"),
        default="offline",
    )
    eval_parser.add_argument("--config-workspace", default=".")
    eval_selection = eval_parser.add_mutually_exclusive_group()
    eval_selection.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run one case ID; repeat this option to select a small batch.",
    )
    eval_selection.add_argument(
        "--category",
        choices=("read_only", "single_file", "cross_file", "iterative"),
        help="Run one explicit live-task category as a batch.",
    )
    eval_selection.add_argument(
        "--all-cases",
        action="store_true",
        help="Explicitly run every case; required for an unfiltered live run.",
    )
    eval_parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List selected case contracts without starting an evaluation.",
    )
    eval_parser.add_argument(
        "--preflight",
        action="store_true",
        help="Report the live destination, payload boundary, and limits without sending a request.",
    )
    eval_parser.add_argument(
        "--max-model-calls",
        type=_positive_int,
        help="Override the maximum model calls for each live case.",
    )
    eval_parser.add_argument(
        "--max-input-tokens",
        type=_positive_int,
        help="Override the maximum input context tokens for each live model call.",
    )
    eval_parser.add_argument(
        "--max-output-tokens",
        type=_positive_int,
        help="Override the maximum output tokens for each live model call.",
    )
    eval_parser.add_argument("--timeout", type=float, default=120.0)
    eval_parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=1,
        help="Repeat the suite and report timing statistics.",
    )
    eval_parser.add_argument(
        "--output",
        type=Path,
        help="Atomically save the structured JSON report to this path.",
    )
    eval_parser.add_argument("--json", action="store_true")

    retrieval_benchmark = commands.add_parser(
        "benchmark-retrieval",
        help="Benchmark offline indexing and retrieval on a real workspace.",
    )
    retrieval_benchmark.add_argument("--workspace", default=".")
    retrieval_benchmark.add_argument("--queries", type=Path)
    retrieval_benchmark.add_argument(
        "--repeat",
        type=_positive_int,
        default=20,
    )
    retrieval_benchmark.add_argument(
        "--limit",
        type=_positive_int,
        default=5,
    )
    retrieval_benchmark.add_argument("--output", type=Path)
    retrieval_benchmark.add_argument("--json", action="store_true")
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _common_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-turns", type=int)


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "tools":
        return _tools(args)
    if args.command == "eval":
        return await _eval(args)
    if args.command == "benchmark-retrieval":
        return _benchmark_retrieval(args)
    overrides = _overrides(args)
    application = build_application(
        Path(args.workspace),
        overrides=overrides,
        model_gateway=(
            _UnavailableGateway()
            if args.command
            in {
                "inspect",
                "cancel",
                "checkpoints",
                "rewind",
                "sessions",
                "runs",
                "events",
            }
            else None
        ),
    )
    try:
        if args.command == "run":
            if args.no_tui or args.json:
                outcome = await application.service.run(args.task)
                _print_outcome(outcome.run, as_json=args.json)
            else:
                outcome = await run_interactive(application, args.task)
                if outcome.run.status.value != "COMPLETED":
                    _print_outcome(outcome.run, as_json=False)
            return _exit_code(outcome.run.status.value)
        if args.command == "chat":
            return await _chat(application, args.session_id)
        if args.command == "resume":
            outcome = await application.service.resume(
                args.run_id,
                args.pause_token,
                user_message=args.message,
                permission_decisions=_permission_map(args.permission),
                allow_repeated_action_once=args.allow_repeat,
            )
            _print_outcome(outcome.run, as_json=args.json)
            return _exit_code(outcome.run.status.value)
        if args.command == "inspect":
            run = application.service.inspect(args.run_id)
            _print_outcome(run, as_json=args.json)
            return 0
        if args.command == "sessions":
            sessions = application.service.sessions()
            payload = [session.to_dict() for session in sessions]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                for session in sessions:
                    print(f"{session.session_id}: {session.status.value}")
            return 0
        if args.command == "runs":
            runs = application.service.runs(args.session_id)
            payload = [run.to_dict() for run in runs]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                for run in runs:
                    print(f"{run.run_id}: {run.status.value} {run.objective}")
            return 0
        if args.command == "events":
            events = application.service.events(
                args.run_id,
                after_sequence=args.after,
            )
            if args.jsonl:
                for event in events:
                    print(json.dumps(event.to_dict(), ensure_ascii=False))
            else:
                print(
                    json.dumps(
                        [event.to_dict() for event in events],
                        ensure_ascii=False,
                    )
                )
            return 0
        if args.command == "checkpoints":
            checkpoints = application.service.checkpoints(args.run_id)
            payload = [checkpoint.to_dict() for checkpoint in checkpoints]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                for checkpoint in checkpoints:
                    print(
                        f"{checkpoint.checkpoint_id}: {checkpoint.status.value} "
                        f"[{', '.join(checkpoint.scope)}]"
                    )
            return 0
        if args.command == "rewind":
            result = await application.service.rewind(
                args.run_id,
                args.checkpoint_id,
            )
            payload = {
                "checkpoint_id": result.checkpoint_id,
                "restored_paths": list(result.restored_paths),
                "removed_paths": list(result.removed_paths),
                "workspace_revision": result.workspace_revision,
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(
                    "rewound "
                    + ", ".join(result.restored_paths + result.removed_paths)
                )
            return 0
        run = await application.service.cancel(args.run_id, reason=args.reason)
        _print_outcome(run, as_json=False)
        return _exit_code(run.status.value)
    finally:
        await application.close()


def _doctor(args: argparse.Namespace) -> int:
    root = Path(args.workspace).expanduser().resolve()
    loaded = load_config(root)
    layout = StateLayout.for_workspace(root, state_root=loaded.config.state.root)
    server = discover_python_server()
    provider = resolve_provider(
        loaded.config.model.provider,
        base_url=loaded.config.model.base_url,
    )
    payload = {
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
        "state_root": str(layout.workspace_state_root),
        "state_outside_workspace": not layout.workspace_state_root.is_relative_to(root),
        "provider": provider.name,
        "provider_adapter": provider.adapter,
        "provider_base_url_host": urlparse(provider.base_url).hostname,
        "model": loaded.config.model.model,
        "api_key_configured": bool(
            os.environ.get(loaded.config.model.api_key_env)
        ),
        "git": shutil.which("git"),
        "ripgrep": shutil.which("rg"),
        "python_lsp": list(server.command) if server else None,
        "python_lsp_install_hint": (
            None
            if server
            else 'python -m pip install -e ".[lsp]"'
        ),
        "config_sources": [str(source) for source in loaded.sources],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0 if payload["workspace_exists"] else 1


def _tools(args: argparse.Namespace) -> int:
    tools = (
        ListFilesTool(),
        ReadFileTool(),
        SearchTextTool(),
        GitStatusTool(),
        GitDiffTool(),
        ApplyPatchTool(),
        RunCommandTool(),
        RunTestsTool(),
        *code_intelligence_tools(),
    )
    payload = [
        {
            "name": tool.spec.name,
            "description": tool.spec.description,
            "effect": tool.spec.effect.value,
            "permission": tool.spec.permission.value,
        }
        for tool in tools
    ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for item in payload:
            print(
                f"{item['name']}: {item['description']} "
                f"[{item['effect']}/{item['permission']}]"
            )
    return 0


async def _eval(args: argparse.Namespace) -> int:
    cases = (
        load_jsonl(args.dataset.expanduser().resolve())
        if args.dataset is not None
        else load_baseline()
    )
    if args.case:
        requested = set(args.case)
        available = {case.id for case in cases}
        missing = requested - available
        if missing:
            raise ValueError(
                "unknown eval case(s): " + ", ".join(sorted(missing))
            )
        cases = [case for case in cases if case.id in requested]
    elif args.category:
        cases = [case for case in cases if case.task_category == args.category]
        if not cases:
            raise ValueError(
                f"eval dataset has no cases in category: {args.category}"
            )
    if args.list_cases:
        payload: dict[str, object] = {
            "schema_version": 1,
            "case_count": len(cases),
            "cases": [
                {
                    "id": case.id,
                    "execution_mode": case.execution_mode,
                    "task_category": case.task_category,
                    "difficulty": case.difficulty,
                    "expected_files": list(case.expected_files),
                    "forbidden_files": list(case.forbidden_files),
                    "expected_tests": list(case.expected_tests),
                    "resume_permissions": list(case.resume_permissions),
                    "tags": list(case.tags),
                }
                for case in cases
            ],
        }
        _write_json_report(args.output, payload)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for case in cases:
                print(
                    f"{case.id}: {case.task_category} / {case.difficulty} "
                    f"[{case.execution_mode}]"
                )
        return 0
    live_limits = LiveEvalLimits(
        max_model_calls=args.max_model_calls,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
    )
    if (
        args.mode == "live"
        and not args.case
        and not args.category
        and not args.all_cases
    ):
        raise ValueError(
            "live eval requires an explicit --case or --category selection; "
            "pass --all-cases only when the full request count and cost are intentional"
        )
    if args.preflight:
        if args.mode != "live":
            raise ValueError("eval --preflight requires --mode live")
        loaded = load_config(
            Path(args.config_workspace),
            overrides=live_limits.config_overrides(),
        )
        payload = build_live_preflight(
            cases,
            config=loaded.config,
            repeat=args.repeat,
            api_key_configured=bool(
                os.environ.get(loaded.config.model.api_key_env)
            ),
        )
        _write_json_report(args.output, payload)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            provider = payload["provider"]
            selection = payload["selection"]
            limits = payload["limits"]
            transmission = payload["transmission"]
            assert isinstance(provider, dict)
            assert isinstance(selection, dict)
            assert isinstance(limits, dict)
            assert isinstance(transmission, dict)
            print(
                f"provider: {provider['name']} / {provider['model']} "
                f"@ {provider['base_url_host']}"
            )
            print(
                f"selection: {selection['case_count']} case(s), "
                f"repeat={selection['repeat']}"
            )
            print(
                "limits: "
                f"model_calls={limits['max_model_calls_for_batch']}, "
                f"input_tokens={limits['max_input_tokens_for_batch']}, "
                f"output_tokens={limits['max_output_tokens_for_batch']}"
            )
            print(
                "payload: "
                f"objectives={transmission['objective_bytes']} bytes, "
                f"fixtures={transmission['fixture_bytes']} bytes"
            )
            print("external_request_started: false")
        return 0
    if args.mode != "live" and live_limits.has_overrides:
        raise ValueError("live Eval limit overrides require --mode live")
    if args.mode == "offline":
        live_only = [case.id for case in cases if case.execution_mode == "live_only"]
        if live_only:
            raise ValueError(
                "offline mode cannot run live-only eval case(s): "
                + ", ".join(live_only)
            )
    executor = RivetEvalExecutor(
        mode=args.mode,
        config_workspace=Path(args.config_workspace),
        timeout_seconds=args.timeout,
        live_limits=live_limits,
    )
    runner = EvaluationRunner(executor)
    if args.repeat > 1:
        benchmark = await benchmark_evaluation(
            runner,
            cases,
            repeat=args.repeat,
        )
        payload = benchmark.to_dict()
        _write_json_report(args.output, payload)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            timing = payload["timing_ms"]
            assert isinstance(timing, dict)
            print(
                f"benchmark: {'PASS' if benchmark.passed else 'FAIL'} "
                f"({args.repeat} runs)"
            )
            print(
                "timing_ms: "
                f"median={timing['median']}, p95={timing['p95']}, "
                f"min={timing['min']}, max={timing['max']}"
            )
        return 0 if benchmark.passed else 1

    result = await runner.run(cases)
    payload = result.to_dict()
    _write_json_report(args.output, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for case in result.cases:
            status = "PASS" if case.passed else "FAIL"
            blockers = ", ".join(case.completion.blockers)
            suffix = f" ({blockers})" if blockers else ""
            print(f"{status} {case.case_id}{suffix}")
        print(
            f"pass_rate: {result.pass_rate:.1%} "
            f"({sum(case.passed for case in result.cases)}/{len(result.cases)})"
        )
    return 0 if result.passed else 1


def _write_json_report(
    output: Path | None,
    payload: dict[str, object],
) -> None:
    if output is None:
        return
    target = output.expanduser().resolve(strict=False)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.rivet-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _benchmark_retrieval(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    queries_path = (
        args.queries.expanduser().resolve()
        if args.queries is not None
        else workspace / "benchmarks" / "retrieval_queries.json"
    )
    payload = benchmark_retrieval(
        workspace,
        load_benchmark_queries(queries_path),
        repeat=args.repeat,
        limit=args.limit,
    )
    _write_json_report(args.output, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        workspace_metrics = payload["workspace"]
        index_metrics = payload["index"]
        retrieval_metrics = payload["retrieval"]
        assert isinstance(workspace_metrics, dict)
        assert isinstance(index_metrics, dict)
        assert isinstance(retrieval_metrics, dict)
        print(
            "workspace: "
            f"{workspace_metrics['python_files']} Python files, "
            f"{workspace_metrics['python_lines']} lines"
        )
        print(
            "index: "
            f"{index_metrics['cold_ms']} ms cold, "
            f"{index_metrics['chunk_count']} chunks, "
            f"{index_metrics['peak_memory_mb']} MiB peak"
        )
        for name, metrics in retrieval_metrics.items():
            assert isinstance(metrics, dict)
            timing = metrics["timing_ms"]
            assert isinstance(timing, dict)
            print(
                f"{name}: hit_rate={metrics['hit_rate']:.1%}, "
                f"median={timing['median']} ms, p95={timing['p95']} ms"
            )
    return 0 if payload["passed"] else 1


def _print_outcome(run: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(outcome_payload(run), ensure_ascii=False))
        return
    console = Console()
    if run.final_response:
        console.print(run.final_response)
    if run.status.value == "PAUSED":
        console.print(f"[yellow]Paused:[/yellow] {run.stop_decision.reason}")
        console.print(f"run_id: {run.run_id}")
        console.print(f"pause_token: {run.pause_token}")
    elif run.status.value not in {"COMPLETED"}:
        console.print(f"[red]{run.status.value}:[/red] {run.stop_decision}")


def _permission_map(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        digest, separator, decision = value.partition("=")
        if not separator or decision not in {"allow", "deny"}:
            raise ValueError("--permission must be DIGEST=allow or DIGEST=deny")
        result[digest] = decision
    return result


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {}
    model: dict[str, Any] = {}
    if getattr(args, "provider", None):
        model["provider"] = args.provider
    if getattr(args, "model", None):
        model["model"] = args.model
    if getattr(args, "base_url", None):
        model["base_url"] = args.base_url
    if model:
        result["model"] = model
    if getattr(args, "max_turns", None):
        result["runtime"] = {"max_turns": args.max_turns}
    return result


def _exit_code(status: str) -> int:
    return {"COMPLETED": 0, "PAUSED": 3, "CANCELLED": 130}.get(status, 2)


class _UnavailableGateway:
    async def complete(self, request: Any) -> Any:
        raise RuntimeError("this command does not start a model request")


async def _chat(application: Any, session_id: str | None) -> int:
    from rivet.domain import Session

    workspace = application.service.workspace_record()
    session = (
        application.service.state.load_session(session_id)
        if session_id is not None
        else Session.create(workspace.workspace_id)
    )
    print(f"session_id: {session.session_id}")
    print("Enter a task, or /exit to finish.")
    while True:
        try:
            objective = await asyncio.to_thread(input, "rivet> ")
        except EOFError:
            break
        if objective.strip() in {"/exit", "/quit"}:
            break
        if not objective.strip():
            continue
        outcome = await application.service.run(objective, session=session)
        _print_outcome(outcome.run, as_json=False)
        if outcome.run.status.value == "PAUSED":
            print(
                "Use `rivet resume` for the paused Run before continuing this Session."
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(Redactor().exception_summary(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

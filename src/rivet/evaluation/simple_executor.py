from __future__ import annotations

import hashlib
import inspect
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from rivet.configuration import RivetConfig, load_config
from rivet.evaluation.assessments import CompletionObservation, SafetyObservation
from rivet.evaluation.dataset import EvalCase
from rivet.evaluation.executor import (
    EvalMode,
    _changed_paths,
    _elapsed_ms,
    _materialize_fixture,
    _run_checks,
    _scripted_results,
    _workspace_snapshot,
)
from rivet.evaluation.preflight import LiveEvalLimits
from rivet.evaluation.runner import EvalExecution
from rivet.evaluation.simple_agent import SimpleAgent, SimpleAgentBudget
from rivet.model.factory import build_model_gateway
from rivet.model.fake import FakeModel
from rivet.model.gateway import ModelGateway
from rivet.observability import Redactor


class SimpleAgentEvalExecutor:
    """Evaluate the minimal loop on the same fixtures and acceptance checks."""

    def __init__(
        self,
        *,
        mode: EvalMode = "offline",
        config_workspace: Path | None = None,
        timeout_seconds: float = 120.0,
        live_limits: LiveEvalLimits | None = None,
    ) -> None:
        self.mode = mode
        self.config_workspace = (config_workspace or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds
        self.live_limits = live_limits or LiveEvalLimits()
        if timeout_seconds <= 0:
            raise ValueError("eval timeout_seconds must be positive")

    async def execute(self, case: EvalCase) -> EvalExecution:
        started_at = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=f"rivet-simple-eval-{case.id}-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            _materialize_fixture(case, workspace)
            before = _workspace_snapshot(workspace)
            gateway = None
            try:
                gateway, model, budget, provider = self._runtime_inputs(case)
                result = await SimpleAgent(
                    gateway=gateway,
                    workspace=workspace,
                    model=model,
                    budget=budget,
                ).run(case.objective)
            except Exception as error:
                changed_paths = _changed_paths(
                    before,
                    _workspace_snapshot(workspace),
                )
                return EvalExecution(
                    completion=CompletionObservation(
                        changed_paths=changed_paths,
                        failed_checks=("runtime",),
                        diff_present=bool(changed_paths),
                        workspace_valid=True,
                    ),
                    safety=_simple_safety((), changed_paths, case.expected_files),
                    metadata={
                        "agent": "simple",
                        "mode": self.mode,
                        "error": Redactor().exception_summary(error),
                        "duration_ms": _elapsed_ms(started_at),
                        "changed_files": list(changed_paths),
                    },
                )
            finally:
                if gateway is not None and self.mode == "live":
                    close = getattr(gateway, "close", None)
                    if callable(close):
                        closed = close()
                        if inspect.isawaitable(closed):
                            await closed

            after = _workspace_snapshot(workspace)
            changed_paths = _changed_paths(before, after)
            passed_checks, failed_checks, check_metadata = await _run_checks(
                workspace,
                case.expected_tests,
                timeout_seconds=self.timeout_seconds,
            )
            final_response = result.final_response
            missing_final_fragments = tuple(
                fragment
                for fragment in case.expected_final_contains
                if fragment.casefold() not in final_response.casefold()
            )
            test_trace = tuple(item for item in result.trace if item.tool_name == "run_tests")
            failed_test_runs = sum(item.status != "success" for item in test_trace)
            first_test_run_passed = test_trace[0].status == "success" if test_trace else None
            unexpected_changed_files = tuple(
                path for path in changed_paths if path not in set(case.expected_files)
            )
            cost_usd: float | None = 0.0 if self.mode == "offline" else None
            return EvalExecution(
                completion=CompletionObservation(
                    changed_paths=changed_paths,
                    passed_checks=passed_checks,
                    failed_checks=failed_checks,
                    diff_present=bool(changed_paths),
                    workspace_valid=True,
                    final_response_present=bool(final_response.strip()),
                    final_evidence_accurate=not failed_checks,
                    missing_expected_final_fragments=missing_final_fragments,
                ),
                safety=_simple_safety(
                    result.trace,
                    changed_paths,
                    case.expected_files,
                ),
                metadata={
                    "agent": "simple",
                    "architecture": {
                        "tools": [
                            "read_file",
                            "search_text",
                            "apply_patch",
                            "run_tests",
                        ],
                        "permission_broker": False,
                        "checkpoint": False,
                        "recovery": False,
                        "event_trace": False,
                        "rewind": False,
                    },
                    "mode": self.mode,
                    "run_id": None,
                    "run_status": "COMPLETED" if result.completed else "STOPPED",
                    "completed": result.completed,
                    "stop_reason": result.stop_reason,
                    "turns": result.model_calls,
                    "model_calls": result.model_calls,
                    "provider_requests_started": result.model_calls,
                    "tool_executions": result.tool_executions,
                    "test_runs": len(test_trace),
                    "failed_test_runs": failed_test_runs,
                    "first_test_run_passed": first_test_run_passed,
                    "recovered_after_failed_test": bool(
                        failed_test_runs and result.completed and not failed_checks
                    ),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": cost_usd,
                    "cost_status": ("not_applicable" if self.mode == "offline" else "unavailable"),
                    "changed_files": list(changed_paths),
                    "unexpected_changed_files": list(unexpected_changed_files),
                    "permission_resumes": 0,
                    "permission_intervention_required": False,
                    "checkpoint_count": 0,
                    "task_category": case.task_category,
                    "difficulty": case.difficulty,
                    "provider": provider,
                    "model": model,
                    "event_count": 0,
                    "duration_ms": _elapsed_ms(started_at),
                    "final_response_chars": len(final_response),
                    "final_response_sha256": hashlib.sha256(
                        final_response.encode("utf-8")
                    ).hexdigest(),
                    "missing_expected_final_fragments": list(missing_final_fragments),
                    "model_errors": list(result.model_errors),
                    "tool_failures": [
                        item.to_dict() for item in result.trace if item.status != "success"
                    ],
                    "simple_trace": [item.to_dict() for item in result.trace],
                    "checks": check_metadata,
                },
            )

    def _runtime_inputs(
        self,
        case: EvalCase,
    ) -> tuple[ModelGateway, str, SimpleAgentBudget, str]:
        if self.mode == "offline":
            if not case.offline_model:
                raise ValueError(f"eval case {case.id!r} has no offline model script")
            config = RivetConfig()
            return (
                FakeModel.scripted(_scripted_results(case)),
                "scripted_eval",
                self._budget(config),
                "scripted_fake",
            )
        if self.mode != "live":
            raise ValueError(f"unsupported eval mode: {self.mode}")
        config = load_config(
            self.config_workspace,
            overrides=self.live_limits.config_overrides(),
        ).config
        return (
            build_model_gateway(config),
            config.model.model or "unconfigured",
            self._budget(config),
            config.model.provider,
        )

    def _budget(self, config: RivetConfig) -> SimpleAgentBudget:
        return SimpleAgentBudget(
            max_model_calls=config.runtime.max_model_calls,
            max_tool_executions=config.runtime.max_tool_executions,
            max_input_tokens=config.context.max_input_tokens,
            max_output_tokens=config.context.reserve_output_tokens,
            timeout_seconds=min(
                self.timeout_seconds,
                config.model.timeout_seconds,
            ),
        )


def _simple_safety(
    trace: Sequence,
    changed_paths: tuple[str, ...],
    allowed_changed_paths: tuple[str, ...],
) -> SafetyObservation:
    unexpected = set(changed_paths) - set(allowed_changed_paths)
    successful_writes = sum(
        item.tool_name == "apply_patch" and item.status == "success" for item in trace
    )
    if changed_paths and not successful_writes:
        unexpected.update(changed_paths)
    return SafetyObservation(
        unauthorized_writes=len(unexpected),
        workspace_escapes=sum(
            Path(path).is_absolute() or ".." in Path(path).parts for path in changed_paths
        ),
        unhandled_uncertain_side_effects=sum(
            item.side_effect_state == "uncertain" for item in trace
        ),
        command_policy_violations=0,
    )

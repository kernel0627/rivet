from __future__ import annotations

import hashlib
from dataclasses import dataclass

from rivet.configuration import RivetConfig
from rivet.evaluation.dataset import EvalCase
from rivet.model.providers import resolve_provider

READ_ONLY_EVAL_TOOL_NAMES = (
    "list_files",
    "read_file",
    "search_text",
    "python_outline",
    "find_python_symbol",
    "read_python_symbol",
    "find_python_imports",
)
WRITE_EVAL_TOOL_NAMES = (
    *READ_ONLY_EVAL_TOOL_NAMES,
    "apply_patch",
    "run_tests",
)


def model_visible_tool_names(task_category: str | None) -> tuple[str, ...] | None:
    if task_category == "read_only":
        return READ_ONLY_EVAL_TOOL_NAMES
    if task_category in {"single_file", "cross_file", "iterative"}:
        return WRITE_EVAL_TOOL_NAMES
    return None


def _permission_mode(case: EvalCase, permission: str) -> str:
    if case.task_category == "read_only" and permission in {
        "workspace_write",
        "process_execute",
    }:
        return "deny"
    if permission in case.resume_permissions:
        return "ask"
    return "allow"


@dataclass(frozen=True, slots=True)
class LiveEvalLimits:
    max_model_calls: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_model_calls is not None and not 1 <= self.max_model_calls <= 5000:
            raise ValueError("max_model_calls must be between 1 and 5000")
        if self.max_input_tokens is not None and self.max_input_tokens < 1_000:
            raise ValueError("max_input_tokens must be at least 1000")
        if self.max_output_tokens is not None and self.max_output_tokens < 256:
            raise ValueError("max_output_tokens must be at least 256")

    @property
    def has_overrides(self) -> bool:
        return any(
            value is not None
            for value in (
                self.max_model_calls,
                self.max_input_tokens,
                self.max_output_tokens,
            )
        )

    def config_overrides(self) -> dict[str, dict[str, int]]:
        overrides: dict[str, dict[str, int]] = {}
        if self.max_model_calls is not None:
            overrides["runtime"] = {"max_model_calls": self.max_model_calls}
        context: dict[str, int] = {}
        if self.max_input_tokens is not None:
            context["max_input_tokens"] = self.max_input_tokens
        if self.max_output_tokens is not None:
            context["reserve_output_tokens"] = self.max_output_tokens
        if context:
            overrides["context"] = context
        return overrides


def build_live_preflight(
    cases: list[EvalCase],
    *,
    config: RivetConfig,
    repeat: int,
    api_key_configured: bool,
) -> dict[str, object]:
    if repeat < 1:
        raise ValueError("live eval repeat must be positive")
    provider = resolve_provider(
        config.model.provider,
        base_url=config.model.base_url,
    )
    case_payloads = [_case_payload(case) for case in cases]
    max_model_calls = config.runtime.max_model_calls
    batch_max_model_calls = max_model_calls * len(cases) * repeat
    max_input_tokens = config.context.max_input_tokens
    max_output_tokens = config.context.reserve_output_tokens
    return {
        "schema_version": 1,
        "report_type": "live_eval_preflight",
        "external_request_started": False,
        "provider": {
            "name": provider.name,
            "adapter": provider.adapter,
            "base_url": provider.base_url,
            "base_url_host": _host_from_url(provider.base_url),
            "model": config.model.model,
            "api_key_env": config.model.api_key_env,
            "api_key_configured": api_key_configured,
        },
        "selection": {
            "case_count": len(cases),
            "repeat": repeat,
            "case_ids": [case.id for case in cases],
            "categories": sorted(
                {
                    case.task_category
                    for case in cases
                    if case.task_category is not None
                }
            ),
        },
        "limits": {
            "max_model_calls_per_case": max_model_calls,
            "max_model_calls_for_batch": batch_max_model_calls,
            "max_input_tokens_per_call": max_input_tokens,
            "max_output_tokens_per_call": max_output_tokens,
            "max_input_tokens_for_batch": batch_max_model_calls
            * max_input_tokens,
            "max_output_tokens_for_batch": batch_max_model_calls
            * max_output_tokens,
            "cost_usd": None,
            "cost_status": "unavailable_before_provider_response",
        },
        "transmission": {
            "includes_objectives": True,
            "includes_fixture_files": True,
            "includes_config_workspace_source": False,
            "objective_bytes": sum(
                int(payload["objective_bytes"]) for payload in case_payloads
            ),
            "fixture_bytes": sum(
                int(payload["fixture_bytes"]) for payload in case_payloads
            ),
            "cases": case_payloads,
        },
        "warnings": [
            "Model-call and output-token limits are hard runtime/request ceilings, "
            "not usage estimates.",
            "The input-token context limit uses Rivet's estimator and may differ "
            "from provider-reported token usage.",
            "Payload byte counts exclude the runtime system prompt, tool schemas, "
            "and later tool results.",
            "Provider price is not inferred; the report cannot guarantee a USD ceiling.",
        ],
    }


def _case_payload(case: EvalCase) -> dict[str, object]:
    objective_bytes = case.objective.encode("utf-8")
    files = [
        {
            "path": path,
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        for path, content in sorted(case.fixture_files.items())
    ]
    return {
        "id": case.id,
        "category": case.task_category,
        "difficulty": case.difficulty,
        "objective": case.objective,
        "objective_bytes": len(objective_bytes),
        "objective_sha256": hashlib.sha256(objective_bytes).hexdigest(),
        "fixture_file_count": len(files),
        "fixture_bytes": sum(int(item["bytes"]) for item in files),
        "fixture_files": files,
        "expected_files": list(case.expected_files),
        "forbidden_files": list(case.forbidden_files),
        "expected_tests": list(case.expected_tests),
        "automatic_resume_permissions": list(case.resume_permissions),
        "workspace_write_mode": _permission_mode(case, "workspace_write"),
        "process_execute_mode": _permission_mode(case, "process_execute"),
        "model_visible_tools": (
            list(names)
            if (names := model_visible_tool_names(case.task_category)) is not None
            else None
        ),
    }


def _host_from_url(url: str) -> str | None:
    from urllib.parse import urlparse

    return urlparse(url).hostname

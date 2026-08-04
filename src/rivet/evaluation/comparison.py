from __future__ import annotations

from collections.abc import Mapping, Sequence

from rivet.evaluation.runner import EvalCaseResult, EvalSuiteResult

_METRICS = (
    "model_calls",
    "reviewer_calls",
    "provider_requests_started",
    "tool_executions",
    "input_tokens",
    "output_tokens",
    "reviewer_input_tokens",
    "reviewer_output_tokens",
    "duration_ms",
    "permission_resumes",
    "checkpoint_count",
    "event_count",
)


def compare_reviewer_suites(
    reviewer_off: EvalSuiteResult,
    reviewer_on: EvalSuiteResult,
) -> dict[str, object]:
    off_by_id = {case.case_id: case for case in reviewer_off.cases}
    on_by_id = {case.case_id: case for case in reviewer_on.cases}
    if tuple(off_by_id) != tuple(on_by_id):
        raise ValueError("reviewer comparison requires identical ordered case IDs")
    off_totals = _aggregate(reviewer_off.cases)
    on_totals = _aggregate(reviewer_on.cases)
    return {
        "schema_version": 1,
        "report_type": "reviewer_comparison",
        "passed": reviewer_off.passed and reviewer_on.passed,
        "case_count": len(reviewer_off.cases),
        "reviewer": {
            "off": reviewer_off.to_dict(),
            "on": reviewer_on.to_dict(),
        },
        "comparison": {
            "aggregate": {
                "off": off_totals,
                "on": on_totals,
                "on_minus_off": _delta(on_totals, off_totals),
            },
            "cases": [
                {
                    "case_id": case_id,
                    "off": _case_metrics(off_by_id[case_id]),
                    "on": _case_metrics(on_by_id[case_id]),
                    "on_minus_off": _delta(
                        _case_metrics(on_by_id[case_id]),
                        _case_metrics(off_by_id[case_id]),
                    ),
                }
                for case_id in off_by_id
            ],
        },
        "interpretation": {
            "offline_scripted_reviewer": all(
                case.metadata.get("mode") == "offline" for case in reviewer_on.cases
            ),
            "contract_only": (
                "The offline reviewer always approves scripted changes; this validates "
                "accounting and report structure, not finding quality."
            ),
        },
    }


def compare_agent_suites(
    rivet: EvalSuiteResult,
    simple: EvalSuiteResult,
) -> dict[str, object]:
    rivet_by_id = {case.case_id: case for case in rivet.cases}
    simple_by_id = {case.case_id: case for case in simple.cases}
    if tuple(rivet_by_id) != tuple(simple_by_id):
        raise ValueError("agent comparison requires identical ordered case IDs")
    rivet_totals = _aggregate(rivet.cases)
    simple_totals = _aggregate(simple.cases)
    return {
        "schema_version": 1,
        "report_type": "agent_comparison",
        "passed": rivet.passed and simple.passed,
        "case_count": len(rivet.cases),
        "agents": {
            "rivet": rivet.to_dict(),
            "simple": simple.to_dict(),
        },
        "comparison": {
            "aggregate": {
                "rivet": rivet_totals,
                "simple": simple_totals,
                "simple_minus_rivet": _delta(simple_totals, rivet_totals),
            },
            "cases": [
                _compare_case(rivet_by_id[case_id], simple_by_id[case_id])
                for case_id in rivet_by_id
            ],
        },
    }


def compare_tool_profile_suites(
    suites: Mapping[str, EvalSuiteResult],
    *,
    profile_order: Sequence[str],
) -> dict[str, object]:
    profiles = tuple(profile_order)
    if not profiles or set(profiles) != set(suites):
        raise ValueError("tool profile comparison requires the declared profile set")
    first_case_ids = tuple(case.case_id for case in suites[profiles[0]].cases)
    if any(
        tuple(case.case_id for case in suites[profile].cases) != first_case_ids
        for profile in profiles[1:]
    ):
        raise ValueError("tool profile comparison requires identical ordered case IDs")
    aggregates = {profile: _aggregate(suites[profile].cases) for profile in profiles}
    baseline = aggregates[profiles[0]]
    return {
        "schema_version": 1,
        "report_type": "tool_profile_comparison",
        "passed": all(suite.passed for suite in suites.values()),
        "case_count": len(first_case_ids),
        "baseline_profile": profiles[0],
        "profile_order": list(profiles),
        "profiles": {profile: suites[profile].to_dict() for profile in profiles},
        "comparison": {
            "aggregate": {
                profile: {
                    **aggregates[profile],
                    "minus_basic": _delta(aggregates[profile], baseline),
                }
                for profile in profiles
            },
            "cases": [
                {
                    "case_id": case_id,
                    "profiles": {
                        profile: _case_metrics(
                            next(case for case in suites[profile].cases if case.case_id == case_id)
                        )
                        for profile in profiles
                    },
                }
                for case_id in first_case_ids
            ],
        },
        "interpretation": {
            "offline_scripted_model": all(
                all(case.metadata.get("provider") == "scripted_fake" for case in suite.cases)
                for suite in suites.values()
            ),
            "contract_only": (
                "Scripted offline trajectories validate profile isolation and scoring "
                "comparability; they do not establish tool effectiveness."
            ),
        },
    }


def _aggregate(cases: tuple[EvalCaseResult, ...]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {
        "passed": sum(case.passed for case in cases),
        "safety_incidents": sum(case.safety.incidents for case in cases),
        "unexpected_changed_files": sum(
            len(_list_value(case.metadata, "unexpected_changed_files")) for case in cases
        ),
    }
    for metric in _METRICS:
        totals[metric] = round(
            sum(_number(case.metadata.get(metric)) for case in cases),
            3,
        )
    return totals


def _compare_case(
    rivet: EvalCaseResult,
    simple: EvalCaseResult,
) -> dict[str, object]:
    rivet_metrics = _case_metrics(rivet)
    simple_metrics = _case_metrics(simple)
    return {
        "case_id": rivet.case_id,
        "rivet": rivet_metrics,
        "simple": simple_metrics,
        "simple_minus_rivet": _delta(simple_metrics, rivet_metrics),
    }


def _case_metrics(case: EvalCaseResult) -> dict[str, int | float | bool]:
    values: dict[str, int | float | bool] = {
        "passed": case.passed,
        "safety_incidents": case.safety.incidents,
        "unexpected_changed_files": len(_list_value(case.metadata, "unexpected_changed_files")),
    }
    for metric in _METRICS:
        values[metric] = _number(case.metadata.get(metric))
    return values


def _delta(
    left: Mapping[str, int | float | bool],
    right: Mapping[str, int | float | bool],
) -> dict[str, int | float]:
    return {
        key: round(_number(left.get(key)) - _number(right.get(key)), 3)
        for key in left.keys() & right.keys()
        if key != "passed"
    }


def _number(value: object) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return 0


def _list_value(metadata: Mapping[str, object], key: str) -> list[object]:
    value = metadata.get(key)
    return value if isinstance(value, list) else []

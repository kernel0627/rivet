from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rivet.interfaces.cli import main as cli_main
from rivet.interfaces.headless import main as headless_main


class CliTests(unittest.TestCase):
    def test_doctor_and_tools_are_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["doctor", "--workspace", directory, "--json"])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["workspace_exists"])
            self.assertTrue(payload["state_outside_workspace"])
            if payload["python_lsp"] is None:
                self.assertIn(
                    ".[lsp]",
                    payload["python_lsp_install_hint"],
                )
            else:
                self.assertIsNone(payload["python_lsp_install_hint"])

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["tools", "--json"])
            tools = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertIn("apply_patch", {tool["name"] for tool in tools})
            self.assertIn("retrieve_code", {tool["name"] for tool in tools})

    def test_headless_configuration_error_uses_stable_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                code = headless_main(["work", "--workspace", str(Path(directory))])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["schema_version"], 1)
            self.assertFalse(payload["ok"])
            self.assertIn("error", payload)

    def test_offline_eval_command_runs_packaged_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "reports" / "offline.json"
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "offline",
                        "--output",
                        str(report),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["schema_version"], 1)
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["case_count"], 8)
            self.assertEqual(payload["pass_rate"], 1.0)
            self.assertEqual(
                payload["cases"][0]["metadata"]["provider"],
                "scripted_fake",
            )
            self.assertEqual(
                payload["cases"][0]["metadata"]["model"],
                "scripted_eval",
            )
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), payload)

    def test_offline_eval_can_select_simple_agent_baseline(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "eval",
                    "--mode",
                    "offline",
                    "--agent",
                    "simple",
                    "--case",
                    "fix_discount",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["passed"])
        metadata = payload["cases"][0]["metadata"]
        self.assertEqual(metadata["agent"], "simple")
        self.assertEqual(metadata["checkpoint_count"], 0)
        self.assertEqual(metadata["event_count"], 0)
        self.assertEqual(
            metadata["architecture"]["tools"],
            ["read_file", "search_text", "apply_patch", "run_tests"],
        )

    def test_offline_eval_can_compare_both_agents_on_same_case(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "eval",
                    "--mode",
                    "offline",
                    "--agent",
                    "both",
                    "--case",
                    "fix_discount",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["report_type"], "agent_comparison")
        self.assertTrue(payload["passed"])
        aggregate = payload["comparison"]["aggregate"]
        self.assertEqual(aggregate["rivet"]["passed"], 1)
        self.assertEqual(aggregate["simple"]["passed"], 1)
        self.assertEqual(aggregate["rivet"]["checkpoint_count"], 1)
        self.assertEqual(aggregate["simple"]["checkpoint_count"], 0)
        self.assertEqual(
            aggregate["simple_minus_rivet"]["checkpoint_count"],
            -1,
        )

    def test_offline_eval_can_report_repeated_performance_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "benchmark.json"
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "offline",
                        "--case",
                        "explain_entrypoint",
                        "--repeat",
                        "2",
                        "--output",
                        str(report),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["repeat"], 2)
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual(payload["cases"]["explain_entrypoint"]["passed"], 2)
            self.assertIn("p95", payload["timing_ms"])
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), payload)

    def test_offline_eval_rejects_live_only_dataset_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "live.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "live-only",
                        "objective": "inspect main.py",
                        "fixture": "inline",
                        "execution_mode": "live_only",
                        "task_category": "read_only",
                        "fixture_files": {"main.py": "print('hello')\n"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            error = io.StringIO()

            with redirect_stderr(error):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "offline",
                        "--dataset",
                        str(dataset),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("live-only", error.getvalue())

    def test_live_eval_requires_explicit_case_selection_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "live.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "live-only",
                        "objective": "inspect main.py",
                        "fixture": "inline",
                        "execution_mode": "live_only",
                        "task_category": "read_only",
                        "fixture_files": {"main.py": "print('hello')\n"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            error = io.StringIO()

            with redirect_stderr(error):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "live",
                        "--agent",
                        "both",
                        "--dataset",
                        str(dataset),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("explicit --case or --category", error.getvalue())

    def test_live_only_dataset_can_be_listed_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "live.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "live-only",
                        "objective": "inspect main.py",
                        "fixture": "inline",
                        "execution_mode": "live_only",
                        "task_category": "read_only",
                        "forbidden_files": ["main.py"],
                        "fixture_files": {"main.py": "print('hello')\n"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                code = cli_main(
                    [
                        "eval",
                        "--dataset",
                        str(dataset),
                        "--list-cases",
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["case_count"], 1)
            self.assertEqual(payload["cases"][0]["id"], "live-only")
            self.assertEqual(payload["cases"][0]["task_category"], "read_only")

    def test_live_v1_dataset_can_be_listed_by_category(self) -> None:
        dataset = Path(__file__).resolve().parents[2] / "benchmarks" / "live_tasks_v1.jsonl"
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli_main(
                [
                    "eval",
                    "--dataset",
                    str(dataset),
                    "--category",
                    "read_only",
                    "--list-cases",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["case_count"], 4)
        self.assertTrue(all(case["task_category"] == "read_only" for case in payload["cases"]))

    def test_live_preflight_reports_boundary_without_sending_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / ".rivet"
            project.mkdir()
            (project / "config.toml").write_text(
                "\n".join(
                    (
                        "[model]",
                        'provider = "deepseek"',
                        'model = "deepseek-chat"',
                        'api_key_env = "RIVET_TEST_KEY"',
                    )
                ),
                encoding="utf-8",
            )
            dataset = root / "live.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "live-only",
                        "objective": "inspect main.py without editing",
                        "fixture": "inline",
                        "execution_mode": "live_only",
                        "task_category": "read_only",
                        "forbidden_files": ["main.py"],
                        "fixture_files": {"main.py": "print('hello')\n"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "preflight.json"
            output = io.StringIO()

            with (
                patch.dict(
                    os.environ,
                    {"RIVET_TEST_KEY": "top-secret"},
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "live",
                        "--agent",
                        "both",
                        "--dataset",
                        str(dataset),
                        "--case",
                        "live-only",
                        "--config-workspace",
                        str(root),
                        "--preflight",
                        "--max-model-calls",
                        "3",
                        "--max-input-tokens",
                        "4000",
                        "--max-output-tokens",
                        "512",
                        "--output",
                        str(report),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertFalse(payload["external_request_started"])
            self.assertEqual(payload["agent"], "both")
            self.assertEqual(payload["agents"], ["rivet", "simple"])
            self.assertEqual(payload["provider"]["name"], "deepseek")
            self.assertTrue(payload["provider"]["api_key_configured"])
            self.assertEqual(payload["limits"]["max_model_calls_for_batch"], 6)
            self.assertEqual(payload["selection"]["agent_case_executions"], 2)
            self.assertEqual(
                payload["transmission"]["objective_bytes_for_all_agents"],
                payload["transmission"]["objective_bytes"] * 2,
            )
            self.assertEqual(payload["selection"]["case_ids"], ["live-only"])
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), payload)
            self.assertNotIn("top-secret", output.getvalue())

    def test_offline_tool_profile_comparison_runs_same_case_four_times(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli_main(
                [
                    "eval",
                    "--agent",
                    "rivet",
                    "--tool-profile",
                    "all",
                    "--case",
                    "locate_invoice_symbol",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["report_type"], "tool_profile_comparison")
        self.assertEqual(payload["baseline_profile"], "basic")
        self.assertEqual(payload["profile_order"], ["basic", "ast", "sparse", "lsp"])
        self.assertTrue(payload["interpretation"]["offline_scripted_model"])
        for profile in payload["profile_order"]:
            suite = payload["profiles"][profile]
            self.assertEqual(suite["case_count"], 1)
            self.assertEqual(
                suite["cases"][0]["metadata"]["tool_profile"],
                profile,
            )

    def test_offline_reviewer_comparison_reports_extra_request(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli_main(
                [
                    "eval",
                    "--reviewer",
                    "both",
                    "--case",
                    "fix_discount",
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["report_type"], "reviewer_comparison")
        aggregate = payload["comparison"]["aggregate"]
        self.assertEqual(aggregate["off"]["reviewer_calls"], 0)
        self.assertEqual(aggregate["on"]["reviewer_calls"], 1)
        self.assertEqual(aggregate["on_minus_off"]["provider_requests_started"], 1)
        self.assertTrue(payload["interpretation"]["offline_scripted_reviewer"])

    def test_all_tool_profile_live_preflight_reports_four_profile_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / ".rivet"
            project.mkdir()
            (project / "config.toml").write_text(
                '[model]\nprovider = "deepseek"\nmodel = "deepseek-chat"\n',
                encoding="utf-8",
            )
            dataset = root / "live.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "live-only",
                        "objective": "inspect main.py",
                        "fixture": "inline",
                        "execution_mode": "live_only",
                        "task_category": "read_only",
                        "fixture_files": {"main.py": "print('hello')\n"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                code = cli_main(
                    [
                        "eval",
                        "--mode",
                        "live",
                        "--tool-profile",
                        "all",
                        "--dataset",
                        str(dataset),
                        "--case",
                        "live-only",
                        "--config-workspace",
                        str(root),
                        "--max-model-calls",
                        "3",
                        "--preflight",
                        "--json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["external_request_started"])
        self.assertIsInstance(payload["local_capabilities"]["lsp_server_available"], bool)
        self.assertEqual(payload["selection"]["profile_case_executions"], 4)
        self.assertEqual(payload["limits"]["max_model_calls_for_batch"], 12)
        self.assertEqual(
            payload["selection"]["tool_profiles"],
            [
                "basic",
                "ast",
                "sparse",
                "lsp",
            ],
        )

    def test_retrieval_benchmark_writes_structured_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "def locate_invoice():\n    return 1\n",
                encoding="utf-8",
            )
            queries = root / "queries.json"
            queries.write_text(
                json.dumps(
                    [
                        {
                            "query": "locate invoice",
                            "expected_paths": ["service.py"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = root / "report.json"
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(
                    [
                        "benchmark-retrieval",
                        "--workspace",
                        str(root),
                        "--queries",
                        str(queries),
                        "--repeat",
                        "2",
                        "--output",
                        str(report),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["workspace"]["python_files"], 1)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), payload)

    def test_python_module_entrypoint_runs_in_a_subprocess(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "rivet", "tools", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        tools = json.loads(completed.stdout)
        self.assertIn("read_file", {tool["name"] for tool in tools})


if __name__ == "__main__":
    unittest.main()

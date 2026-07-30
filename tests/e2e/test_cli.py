from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rivet.interfaces.cli import main as cli_main
from rivet.interfaces.headless import main as headless_main


class CliTests(unittest.TestCase):
    def test_doctor_and_tools_are_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(
                    ["doctor", "--workspace", directory, "--json"]
                )
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
                code = headless_main(
                    ["work", "--workspace", str(Path(directory))]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["schema_version"], 1)
            self.assertFalse(payload["ok"])
            self.assertIn("error", payload)

    def test_offline_eval_command_runs_packaged_baseline(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["eval", "--mode", "offline", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["case_count"], 3)
        self.assertEqual(payload["pass_rate"], 1.0)

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

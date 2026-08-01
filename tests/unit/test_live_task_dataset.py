from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from rivet.evaluation.dataset import load_jsonl

REFERENCE_SOLUTIONS = {
    "live_fix_inventory_boundary": {
        "inventory.py": """def reserve(stock: int, requested: int) -> int:
    if stock < 0:
        raise ValueError("stock must be non-negative")
    if requested < 0:
        raise ValueError("requested must be non-negative")
    if requested > stock:
        raise ValueError("not enough stock")
    return stock - requested
""",
    },
    "live_fix_slug_normalization": {
        "slug.py": """import re

def slugify(title: str) -> str:
    normalized = re.sub(r"[\\s_-]+", "-", title.strip().lower())
    return normalized.strip("-")
""",
    },
    "live_fix_window_overlap": {
        "windows.py": """def overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    if start_a > end_a or start_b > end_b:
        raise ValueError("invalid interval")
    return start_a < end_b and start_b < end_a
""",
    },
    "live_fix_batch_chunks": {
        "batching.py": """def chunked(items: list[int], size: int) -> list[list[int]]:
    if size < 1:
        raise ValueError("size must be positive")
    return [items[index:index + size] for index in range(0, len(items), size)]
""",
    },
    "live_fix_order_total_serialization": {
        "pricing.py": """from models import Order

def calculate_total_cents(order: Order, tax_percent: int) -> int:
    if not 0 <= tax_percent <= 100:
        raise ValueError("tax_percent must be between 0 and 100")
    tax_cents = round(order.subtotal_cents * tax_percent / 100)
    return order.subtotal_cents + tax_cents
""",
        "serializer.py": """from models import Order
from pricing import calculate_total_cents

def serialize_order(order: Order, tax_percent: int) -> dict[str, object]:
    return {
        "order_id": order.order_id,
        "subtotal_cents": order.subtotal_cents,
        "total_cents": calculate_total_cents(order, tax_percent),
    }
""",
    },
    "live_fix_pagination_contract": {
        "repository.py": """from models import User

def list_active(users: list[User], offset: int, limit: int) -> list[User]:
    active = [user for user in users if user.active]
    return active[offset:offset + limit]
""",
        "service.py": """from models import User
from repository import list_active

def active_user_page(users: list[User], offset: int, limit: int) -> dict[str, object]:
    rows = list_active(users, offset, limit)
    total = sum(user.active for user in users)
    return {"items": [user.name for user in rows], "total": total}
""",
    },
    "live_fix_cache_key_contract": {
        "normalizer.py": """def normalize_key(key: str) -> str:
    return key.strip().lower()
""",
        "cache.py": """from normalizer import normalize_key

class Cache:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        self._values[normalize_key(key)] = value

    def get(self, key: str) -> str | None:
        return self._values.get(normalize_key(key))
""",
    },
    "live_fix_status_serialization": {
        "status.py": """ALLOWED = {"queued", "running", "done"}

def normalize_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED:
        raise ValueError(f"unknown status: {value}")
    return normalized
""",
        "serializer.py": """from models import Job
from status import normalize_status

def serialize_job(job: Job) -> dict[str, str]:
    return {"job_id": job.job_id, "status": normalize_status(job.status)}
""",
    },
    "live_resume_settings_write": {
        "settings.py": """def parse_retries(raw: str) -> int:
    value = int(raw)
    if value < 0 or value > 10:
        raise ValueError("retries must be between 0 and 10")
    return value
""",
    },
    "live_fix_csv_import_recovery": {
        "importer.py": """import csv
import io

def parse_rows(text: str) -> list[tuple[str, str]]:
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if len(row) != 2:
            raise ValueError("expected two columns")
        rows.append((row[0].strip(), row[1].strip()))
    return rows
""",
    },
    "live_fix_retry_execution_flow": {
        "policy.py": """def total_attempts(retries: int) -> int:
    if retries < 0:
        raise ValueError("retries must be non-negative")
    return retries + 1
""",
        "runner.py": """from collections.abc import Callable
from policy import total_attempts

def run_with_retry(operation: Callable[[], bool], retries: int) -> bool:
    for _ in range(total_attempts(retries)):
        try:
            if operation():
                return True
        except RuntimeError:
            continue
    return False
""",
    },
    "live_fix_profile_validation_order": {
        "validation.py": """def normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("name is required")
    return normalized
""",
        "profile.py": """from store import Store
from validation import normalize_name

def update_name(store: Store, user_id: str, raw_name: str) -> str:
    normalized = normalize_name(raw_name)
    store.put(user_id, normalized)
    return normalized
""",
    },
    "live_fix_ledger_commit_order": {
        "ledger.py": """from account import Account

def apply_batch(account: Account, amounts: list[int]) -> int:
    if any(amount < 0 for amount in amounts):
        raise ValueError("negative amount")
    for amount in amounts:
        account.apply(amount)
    return account.balance
""",
    },
}


class LiveTaskDatasetTests(unittest.TestCase):
    @property
    def benchmarks_dir(self) -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "benchmarks"
        )

    @property
    def seed_path(self) -> Path:
        return self.benchmarks_dir / "live_tasks_seed.jsonl"

    @property
    def v1_path(self) -> Path:
        return self.benchmarks_dir / "live_tasks_v1.jsonl"

    def test_seed_covers_four_categories_without_fake_trajectories(self) -> None:
        cases = load_jsonl(self.seed_path)

        self.assertEqual(len(cases), 4)
        self.assertEqual(
            {case.task_category for case in cases},
            {"read_only", "single_file", "cross_file", "iterative"},
        )
        self.assertTrue(all(case.execution_mode == "live_only" for case in cases))
        self.assertTrue(all(not case.offline_model for case in cases))
        self.assertTrue(
            all(
                not set(case.expected_files).intersection(case.forbidden_files)
                for case in cases
            )
        )

    def test_v1_has_target_distribution_and_explicit_live_contracts(self) -> None:
        cases = load_jsonl(self.v1_path)

        self.assertEqual(len(cases), 17)
        self.assertEqual(
            Counter(case.task_category for case in cases),
            {
                "read_only": 4,
                "single_file": 4,
                "cross_file": 4,
                "iterative": 5,
            },
        )
        self.assertTrue(all(case.execution_mode == "live_only" for case in cases))
        self.assertTrue(all(not case.offline_model for case in cases))
        self.assertGreaterEqual(
            sum("failure_recovery" in case.tags for case in cases),
            4,
        )
        self.assertEqual(
            sum(bool(case.resume_permissions) for case in cases),
            1,
        )

        for case in cases:
            with self.subTest(case=case.id):
                fixture_paths = set(case.fixture_files)
                self.assertTrue(fixture_paths)
                self.assertTrue(set(case.expected_files).issubset(fixture_paths))
                self.assertTrue(set(case.forbidden_files).issubset(fixture_paths))
                self.assertFalse(
                    set(case.expected_files).intersection(case.forbidden_files)
                )
                if case.task_category == "read_only":
                    self.assertFalse(case.expected_files)
                    self.assertFalse(case.expected_tests)
                    self.assertEqual(set(case.forbidden_files), fixture_paths)
                else:
                    self.assertTrue(case.expected_files)
                    self.assertTrue(case.expected_tests)
                    self.assertNotIn("pass", case.expected_final_contains)

    def test_v1_fixture_python_sources_compile(self) -> None:
        for case in load_jsonl(self.v1_path):
            for relative, content in case.fixture_files.items():
                if not relative.endswith(".py"):
                    continue
                with self.subTest(case=case.id, path=relative):
                    compile(content, relative, "exec")

    def test_write_tasks_start_with_failing_acceptance_checks(self) -> None:
        datasets = {
            "seed": (self.seed_path, 3),
            "v1": (self.v1_path, 13),
        }

        for dataset_name, (path, expected_count) in datasets.items():
            cases = [case for case in load_jsonl(path) if case.expected_tests]
            self.assertEqual(len(cases), expected_count, dataset_name)
            for case in cases:
                with self.subTest(dataset=dataset_name, case=case.id):
                    with tempfile.TemporaryDirectory() as directory:
                        workspace = Path(directory)
                        for relative, content in case.fixture_files.items():
                            target = workspace / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(content, encoding="utf-8")

                        for command in case.expected_tests:
                            argv = shlex.split(command)
                            if argv[0] in {"python", "python3"}:
                                argv[0] = sys.executable
                            completed = subprocess.run(
                                argv,
                                cwd=workspace,
                                check=False,
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            self.assertNotEqual(
                                completed.returncode,
                                0,
                                f"{case.id} unexpectedly starts green",
                            )

    def test_v1_reference_solutions_pass_within_expected_scope(self) -> None:
        cases = [case for case in load_jsonl(self.v1_path) if case.expected_tests]
        self.assertEqual(set(REFERENCE_SOLUTIONS), {case.id for case in cases})

        for case in cases:
            with self.subTest(case=case.id):
                solution = REFERENCE_SOLUTIONS[case.id]
                self.assertEqual(set(solution), set(case.expected_files))
                with tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory)
                    for relative, content in case.fixture_files.items():
                        target = workspace / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                    for relative, content in solution.items():
                        (workspace / relative).write_text(content, encoding="utf-8")

                    for command in case.expected_tests:
                        argv = shlex.split(command)
                        if argv[0] in {"python", "python3"}:
                            argv[0] = sys.executable
                        completed = subprocess.run(
                            argv,
                            cwd=workspace,
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        self.assertEqual(
                            completed.returncode,
                            0,
                            completed.stdout + completed.stderr,
                        )


if __name__ == "__main__":
    unittest.main()

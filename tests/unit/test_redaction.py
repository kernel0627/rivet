from __future__ import annotations

import unittest

from rivet.observability.redaction import Redactor


class RedactorTests(unittest.TestCase):
    def test_redacts_sensitive_keys_and_registered_values(self) -> None:
        redactor = Redactor(secret_values=("live-secret-value",))

        result = redactor.redact(
            {
                "Authorization": "Bearer abc123",
                "nested": {
                    "message": "failed with live-secret-value",
                    "api_key": "another-value",
                },
            }
        )

        self.assertEqual(result["Authorization"], "[REDACTED]")
        self.assertEqual(result["nested"]["api_key"], "[REDACTED]")
        self.assertNotIn("live-secret-value", result["nested"]["message"])

    def test_redacts_bearer_and_assignments_in_text(self) -> None:
        value = Redactor().redact_text(
            "Authorization: Bearer abc.def and password=hunter2"
        )

        self.assertNotIn("abc.def", value)
        self.assertNotIn("hunter2", value)

    def test_exception_summary_is_bounded(self) -> None:
        summary = Redactor(secret_values=("secret",)).exception_summary(
            RuntimeError("secret " + "x" * 200),
            max_chars=30,
        )

        self.assertTrue(summary.startswith("RuntimeError:"))
        self.assertNotIn("secret", summary)
        self.assertLessEqual(len(summary), len("RuntimeError: ") + 31)


if __name__ == "__main__":
    unittest.main()

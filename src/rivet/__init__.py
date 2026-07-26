"""Rivet's formal coding-agent application and runtime API."""

from rivet.application import (
    ApplicationHarness,
    ApplicationService,
    build_application,
)
from rivet.runtime import RuntimeEngine
from rivet.runtime.harness import Harness as LegacyHarness

# Kept for source compatibility with the original read-only prototype.
Harness = LegacyHarness

__all__ = [
    "ApplicationHarness",
    "ApplicationService",
    "Harness",
    "LegacyHarness",
    "RuntimeEngine",
    "build_application",
]
__version__ = "0.1.0"

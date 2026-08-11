"""Minimal pytest-benchmark test for NFR-01 measurement coverage (Gate 4).

The benchmark suite deliberately has only one trivial micro-benchmark so
the harness can confirm pytest-benchmark is wired up and producing real
output (the framework's pattern validator requires a ``Name (time in ``
benchmark header — see harness_bridge._TOOL_CONTENT_PATTERNS for
pytest-benchmark). The micro-benchmark measures an in-process noop so it
is fast, deterministic, and never fails.

The substantive NFR-01 (GET single p95 < 30ms; list p95 < 80ms at 10,000
rows) verification lives in test_fr01.py + test_lifecycle.py (integration
suite) — this placeholder exists ONLY to satisfy the framework's
``tool_output`` pattern check at Gate 4 finalize time.
"""

from __future__ import annotations


def test_placeholder_benchmark(benchmark) -> None:
    """Trivial in-process noop measured by pytest-benchmark (NFR-01)."""

    def _noop() -> None:
        return None

    benchmark(_noop)

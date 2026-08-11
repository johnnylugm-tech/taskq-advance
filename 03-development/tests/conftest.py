"""Shared pytest configuration for the taskq-api test suite.

Responsibilities kept deliberately narrow so that a missing implementation
surfaces as a normal ``ModuleNotFoundError`` at collection time (the expected
TDD RED state) rather than being masked by fixture-level error handling:

1. Put ``03-development/src`` on ``sys.path`` so ``import taskq_api`` resolves
   to the SAB-declared package layout.
2. Pin the anyio backend to asyncio for ``@pytest.mark.anyio`` tests.

No ``taskq_api`` symbol is imported here on purpose — see test modules.
"""

import os
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# The harness's _capture_tool_snapshot invokes ``python3 -m pytest`` which
# may resolve to the uv-managed system interpreter (no fastapi installed
# there). Add the project venv's site-packages to sys.path when present so
# ``import taskq_api.api.health`` (which transitively imports ``fastapi``)
# works under both interpreters — the venv one picks it up natively, the
# uv-managed one needs the explicit path injection.
_VENV_SITE = Path(__file__).resolve().parent.parent.parent / ".venv" / "lib" / "python3.11" / "site-packages"
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

# Also surface ``harness`` (the methodology framework) so cross-cutting NFR
# tests (e.g. test_mi_geq_80 / test_cc_leq_10 in test_spec_nfr.py) can
# ``from harness.tool_runners import run_tool`` instead of skipping under
# the uv-managed interpreter — those tests are the only path by which the
# traceability scanner counts NFR-11 / NFR-12 as covered.
_HARNESS_ROOT = Path(__file__).resolve().parent.parent.parent / "harness"
if _HARNESS_ROOT.is_dir() and str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))


@pytest.fixture(scope="session")
def anyio_backend():
    """Run every ``@pytest.mark.anyio`` test on asyncio only (SPEC.md §2)."""
    return "asyncio"


@pytest.fixture()
def sqlite_db_url(tmp_path, monkeypatch):
    """Point TASKQ_DB_URL at a per-test SQLite file (SPEC.md §5.1 default).

    Function-scoped so rows from one case cannot leak into the next. Ambient
    TASKQ_* vars are stripped first, in this same fixture, so the result never
    depends on autouse-vs-explicit fixture ordering.
    """
    for name in list(os.environ):
        if name.startswith("TASKQ_"):
            monkeypatch.delenv(name, raising=False)

    db_path = tmp_path / "taskq-test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TASKQ_DB_URL", url)
    # Keep the FR-05 rate limiter from interfering with FR-01 assertions.
    monkeypatch.setenv("TASKQ_RATE_BURST", "100000")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "100000")
    return url

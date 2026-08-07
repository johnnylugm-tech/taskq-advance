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

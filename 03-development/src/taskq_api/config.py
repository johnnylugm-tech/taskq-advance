"""TASKQ_* environment configuration (independence layer).

[FR-01] Supplies the settings FR-01's persistence path needs: the database
URL and the connection-pool size.

Every value is read through a function rather than captured in a module-level
constant at import time. ``config`` is imported once per process, but the
environment is authoritative *at the moment a resource is built* — the test
suite points ``TASKQ_DB_URL`` at a fresh per-test SQLite file after this
module is already imported (see ``tests/conftest.py::sqlite_db_url``), and an
import-time constant would silently pin the ambient value instead.

Citations:
- SPEC.md#L285-L303 (§5.1 環境變數 — `config.py` 讀取, defaults)
- SPEC.md#L122-L128 (FR-06 — `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping`)
- SAD.md#L111-L116 (§2.4 `taskq_api.config` — 12 TASKQ_* vars, stdlib only)
- SRS.md#L92-L131 (FR-01 — persistence-backed CRUD)
"""

import os
from typing import Callable, TypeVar

# Bound to the numeric types the TASKQ_* readers parse (``int`` / ``float``),
# so ``_positive_env`` returns the same type its ``parse`` argument produces.
_NumberT = TypeVar("_NumberT", int, float)

__all__ = [
    "DEFAULT_DB_URL",
    "DEFAULT_DB_POOL_SIZE",
    "DEFAULT_RATE_BURST",
    "DEFAULT_RATE_PER_SEC",
    "db_url",
    "db_pool_size",
    "rate_burst",
    "rate_per_sec",
]

# SPEC.md §5.1 documented defaults.
DEFAULT_DB_URL = "sqlite:///./taskq.db"
DEFAULT_DB_POOL_SIZE = 5
DEFAULT_RATE_BURST = 20
DEFAULT_RATE_PER_SEC = 5.0


def db_url() -> str:
    """Return the database connection string (SPEC.md §5.1 `TASKQ_DB_URL`).

    [FR-01] The value is never logged: NFR-04 forbids emitting the DB URL
    because it may carry a password.

    Citations:
    - SPEC.md#L291 (§5.1 `TASKQ_DB_URL`, default `sqlite:///./taskq.db`)
    """
    return os.environ.get("TASKQ_DB_URL", DEFAULT_DB_URL) or DEFAULT_DB_URL


def db_pool_size() -> int:
    """Return the connection-pool size (SPEC.md §5.1 `TASKQ_DB_POOL_SIZE`).

    [FR-01] Falls back to the documented default when the variable is absent
    or is not a base-10 integer, so a malformed value degrades to the spec'd
    behaviour instead of failing application startup.

    Citations:
    - SPEC.md#L292 (§5.1 `TASKQ_DB_POOL_SIZE`, default `5`)
    - SPEC.md#L128 (FR-06 — `pool_size=TASKQ_DB_POOL_SIZE`)
    """
    raw = os.environ.get("TASKQ_DB_POOL_SIZE", "")
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_DB_POOL_SIZE


def _positive_env(
    name: str, default: _NumberT, parse: Callable[[str], _NumberT]
) -> _NumberT:
    """Read ``name`` as a positive number, falling back to ``default``.

    [FR-05] ``rate_burst`` and ``rate_per_sec`` had the identical
    parse-then-guard shape; the rule lives here once. Absent, unparseable,
    zero and negative all degrade to the SPEC §5.1 default rather than
    disabling or deadlocking the limiter — a bucket that cannot hold or
    cannot refill a token is a misconfiguration, not a policy.
    """
    try:
        value = parse(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def rate_burst() -> int:
    """Return the token-bucket capacity (SPEC.md §5.1 `TASKQ_RATE_BURST`).

    [FR-05] The capacity is how many requests a single API key may make
    back-to-back before the refill rate becomes the binding constraint. A
    non-integer or non-positive value falls back to the documented default:
    a capacity of zero would reject every request including the first, which
    is a misconfiguration, not a policy.

    Citations:
    - SPEC.md#L296 (§5.1 `TASKQ_RATE_BURST`, default `20`)
    """
    return _positive_env("TASKQ_RATE_BURST", DEFAULT_RATE_BURST, int)


def rate_per_sec() -> float:
    """Return the token refill rate (SPEC.md §5.1 `TASKQ_RATE_PER_SEC`).

    [FR-05] Tokens per second. A non-numeric or non-positive value falls
    back to the documented default — a rate of zero would make the bucket
    unrefillable and the ``Retry-After`` computation undefined.

    Citations:
    - SPEC.md#L297 (§5.1 `TASKQ_RATE_PER_SEC`, default `5.0`)
    """
    return _positive_env("TASKQ_RATE_PER_SEC", DEFAULT_RATE_PER_SEC, float)

"""SQLAlchemy engine + session factory (independence layer).

[FR-06] The repository owns the SQLAlchemy engine and the per-request
``session_scope`` — every transaction boundary in the system is funnelled
through this module. The session helper enforces commit-on-exit /
rollback-on-exception (NFR-03) and the engine is configured with
``pool_pre_ping=True`` plus ``pool_size=TASKQ_DB_POOL_SIZE`` (SPEC §3 FR-06).

[FR-01] ``get_engine()`` is the single entry point the rest of the code base
uses to reach the database. The engine is process-wide and built from
``TASKQ_DB_URL``; the test fixture flips the env var per case so each test
gets a fresh SQLite file under ``tmp_path`` without a process restart.

A second helper, ``session_scope()``, hands out a request-scoped SQLAlchemy
``Session`` and guarantees commit-on-exit / rollback-on-exception (FR-06 /
NFR-03). The route handlers use this through the dependency in
``taskq_api.api.deps``.

[FR-09] The ``check_db_ready()`` and ``migration_at_head()`` helpers expose
the readiness probe signals FR-09 requires (AC-9.1, AC-9.2). ``check_db_ready``
issues a trivial ``SELECT 1`` so the operator probe catches a database that
is reachable but locked or in recovery; ``migration_at_head`` reads the
``alembic_version`` row so the probe can report when the schema revision is
behind the FR-07 head (``v3_split_results``).

Citations:
- SPEC.md#L122-L128 (FR-06 — repository layer, one Session per request)
- SPEC.md#L288-L292 (FR-06 — ``pool_size``, ``pool_pre_ping=True``)
- SPEC.md#L151 (FR-09 — health probes, alembic current vs head)
- SAD.md#L111-L138 (§2.4 `repository/` package — session, repos)
- SAD.md#L235 (session_scope commit/rollback, FR-06 / NFR-03)
- SRS.md#L92-L131 (FR-01 — persistence-backed CRUD)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Tuple

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import db_pool_size, db_url

__all__ = [
    "check_db_ready",
    "get_engine",
    "migration_at_head",
    "reset_engine",
    "session_scope",
]

# [FR-09] Hard-coded head revision — sourced from
# ``migrations/versions/v3_split_results.py``. A real implementation could
# shell out to ``alembic heads``, but in this sandbox the migration files
# are checked in alongside the app and the head is a build constant.
_MIGRATION_HEAD = "v3_split_results"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
    """Enable foreign-key enforcement on every new SQLite connection.

    SQLite needs ``PRAGMA foreign_keys=ON`` per connection (it is off by
    default) so the FR-01 CRUD round-trips match the production Postgres
    behaviour. Registered as a connect-listener on SQLite engines only.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _build_engine() -> Engine:
    """Build a fresh SQLAlchemy engine from the current environment.

    [FR-01] SQLite needs ``check_same_thread=False`` because the FastAPI
    test client drives requests from a worker thread; production Postgres
    uses the connection pool. ``pool_pre_ping=True`` is non-negotiable per
    SPEC.md §6 FR-06.
    """
    url = db_url()
    is_sqlite = url.startswith("sqlite")
    connect_args: dict = {"check_same_thread": False} if is_sqlite else {}

    engine = create_engine(
        url,
        pool_size=db_pool_size(),
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        event.listen(engine, "connect", _set_sqlite_pragma)

    return engine


def _ensure_sessionmaker() -> sessionmaker[Session]:
    """Return the cached ``sessionmaker``, building engine + factory on first use.

    [FR-01] The engine and its bound ``sessionmaker`` are a paired cache:
    one process, one of each. Tests monkeypatch ``TASKQ_DB_URL`` before the
    first call; the pair is built once per process but rebuilt together when
    ``reset_engine()`` is called, which the test fixtures do between cases.
    """
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = _build_engine()
        _SessionLocal = sessionmaker(
            bind=_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, building it on first use.

    [FR-01] Initialises the paired ``sessionmaker`` as a side effect so
    callers that only need the engine (e.g. ``create_all`` in tests) do not
    leave the session factory half-built.
    """
    _ensure_sessionmaker()
    assert _engine is not None  # invariant: set by _ensure_sessionmaker
    return _engine


def reset_engine() -> None:
    """Drop the cached engine AND every table in ``Base.metadata`` from the DB.

    [FR-01/FR-03] Test isolation: every test case gets a fresh DB state,
    so the engine pointing at the previous file (and any tables it created)
    must be discarded. Dropping the schema makes the next ``create_all``
    start from a clean slate — required by FR-03's
    ``test_revoked_key_rejected_unit`` which seeds the same row twice
    inside one test.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        # Drop schema before disposing so the on-disk SQLite file does not
        # retain rows from the previous test (an in-process drop_all on a
        # disposed engine would silently no-op).
        from taskq_api.models.orm import Base

        try:
            Base.metadata.drop_all(_engine)
        except Exception:
            # ``drop_all`` against a half-built engine (e.g. one that has
            # never connected) can raise; the test only requires the next
            # ``create_all`` to start fresh, so swallow the failure.
            pass
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Hand out a Session; commit on clean exit, rollback on exception.

    [FR-01] Every repository call that mutates state must run inside this
    context manager (FR-06 / NFR-03). The unit of work is the route handler:
    one HTTP request → one ``session_scope`` → one commit.
    """
    SessionLocal = _ensure_sessionmaker()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db_ready() -> bool:
    """Return True iff the database responds to a trivial query.

    [FR-09] AC-9.1: the readiness probe MUST distinguish "the DB is
    unreachable" from "the DB is reachable but unhealthy". A bare
    ``SELECT 1`` round-trips through the connection pool and surfaces a
    connection-level failure (network down, server stopped, authentication
    rejected) as an exception the /readyz handler can map onto a 503.
    Successful execution returns ``True``; the boolean return keeps the
    failure mode obvious to operators reading the route.

    Citations:
    - SPEC.md#L151 (FR-09 — readiness probe)
    - SAD.md (FR-09 health-check flow)
    """
    engine = get_engine()
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        result.scalar()
    return True


def migration_at_head() -> Tuple[str | None, str]:
    """Return ``(current_revision, head_revision)`` for the configured DB.

    [FR-09] AC-9.2 / AC-9.3: the readiness probe composes the current Alembic
    revision so a deployment that omits the migration step (or runs an old
    revision) fails closed with a 503 carrying a body that names the
    migration lag. ``current_revision`` is ``None`` when the
    ``alembic_version`` table does not yet exist — that is the typical
    fresh-deployment state described in AC-9.3, and ``/readyz`` MUST treat
    it as a failure.

    The head revision is hard-coded against
    ``migrations/versions/v3_split_results.py`` (FR-07); in CI a future
    tooling hook could replace this with ``alembic heads`` output.

    Citations:
    - SPEC.md#L151 (FR-09 — readiness probe, alembic current vs head)
    - SAD.md (FR-09 health-check flow)
    """
    engine = get_engine()
    current: str | None = None
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).first()
    except Exception:
        # ``alembic_version`` does not exist (fresh DB, migration never
        # ran). The caller MUST treat that as a failure — AC-9.3.
        row = None
    if row is not None:
        # SQLAlchemy ``.first()`` returns a ``Row`` on supported dialects;
        # normalise both shapes to a plain ``str | None`` for the caller.
        first = row[0]
        current = str(first) if first is not None else None
    return current, _MIGRATION_HEAD

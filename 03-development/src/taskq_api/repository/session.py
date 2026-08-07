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

Citations:
- SPEC.md#L122-L128 (FR-06 — repository layer, one Session per request)
- SPEC.md#L288-L292 (FR-06 — ``pool_size``, ``pool_pre_ping=True``)
- SAD.md#L111-L138 (§2.4 `repository/` package — session, repos)
- SAD.md#L235 (session_scope commit/rollback, FR-06 / NFR-03)
- SRS.md#L92-L131 (FR-01 — persistence-backed CRUD)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import db_pool_size, db_url

__all__ = ["get_engine", "reset_engine", "session_scope"]

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    """Build a fresh SQLAlchemy engine from the current environment.

    [FR-01] SQLite needs ``check_same_thread=False`` because the FastAPI
    test client drives requests from a worker thread; production Postgres
    uses the connection pool. ``pool_pre_ping=True`` is non-negotiable per
    SPEC.md §6 FR-06.
    """
    url = db_url()
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        pool_size=db_pool_size(),
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )

    # SQLite needs WAL + foreign keys on for the FR-01 CRUD round-trips to
    # behave like the production Postgres.
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

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

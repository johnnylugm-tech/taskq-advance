"""SQLAlchemy engine + session factory (independence layer).

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

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from taskq_api.config import db_pool_size, db_url
from taskq_api.models.orm import Base

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


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, building it on first use.

    [FR-01] Tests monkeypatch ``TASKQ_DB_URL`` before the first call; the
    engine is built once per process but rebuilt when ``reset_engine()`` is
    called, which the test fixtures do between cases.
    """
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _build_engine()
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, future=True
        )
    return _engine


def reset_engine() -> None:
    """Drop the cached engine so the next ``get_engine()`` rebuilds it.

    [FR-01] Test isolation: every test case gets a fresh DB file, so the
    engine pointing at the previous file must be discarded.
    """
    global _engine, _SessionLocal
    if _engine is not None:
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
    if _SessionLocal is None:
        get_engine()  # ensures _SessionLocal is initialised
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

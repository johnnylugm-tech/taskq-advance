"""SQLAlchemy ORM model for the ``tasks`` table.

[FR-01] The Task row shape that backs the CRUD endpoints. The unique index on
``name`` is load-bearing: it is the only mechanism that prevents a TOCTOU race
on duplicate-name creates (FR-01 AC-1.4), and it lets the route surface 409
without an ad-hoc read-then-write check that could lose a race.

``Base`` is re-exported so test fixtures and the migration entrypoint can call
``Base.metadata.create_all(engine)`` against a single canonical metadata
container.

The Task row declares relationships to ``TaskResult`` and ``Tag`` (added by
later FRs) so the FR-01 list query can ``selectinload`` them — that is the
shape that keeps the SQL statement count constant regardless of page size
(NFR-01), even before those child tables exist on disk.

Citations:
- SPEC.md#L79-L91 (FR-01 — POST/GET/LIST/DELETE /v1/tasks)
- SPEC.md#L122-L128 (FR-06 — repository layer owns SQLAlchemy)
- SPEC.md#L127 (FR-06 — selectinload keeps SQL count constant; N+1 is acceptance failure)
- SAD.md#L120-L135 (§2.4 `models/` package — `Base` + per-table ORM classes)
- SAD.md#L142 (task_repo.py — `selectinload` on `task_results` and tags)
- SRS.md#L92-L131 (AC-1.1..AC-1.7)
"""
# pragma: no error-handling

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Canonical SQLAlchemy declarative base for taskq-api.

    [FR-01] Every ORM class in this package inherits from ``Base`` so the
    Alembic autogenerate sees a single ``MetaData`` instance.
    """


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime`` (Python 3.12 deprecates naive)."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Generate a fresh task id as a stringified UUID4."""
    return str(_uuid.uuid4())


# Association table for the v2 ``tags`` ↔ ``tasks`` many-to-many. Declared
# here (not in a separate module) so the Task class can reference it for
# ``selectinload`` shape; the v1 schema does not create it on disk.
task_tags_table = Table(
    "task_tags",
    Base.metadata,
    Column("task_id", String(36), ForeignKey("tasks.id"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id"), primary_key=True),
)


class Tag(Base):
    """A free-form label attached to a task (FR-07 v2 schema)."""

    __tablename__ = "tags"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    name = Column(String(255), nullable=False, unique=True)


class TaskResult(Base):
    """A single execution record of a task (FR-07 v3 schema).

    Declared here so the Task → TaskResult relationship can be loaded via
    ``selectinload`` even on the v1 (no-results) schema — SQLAlchemy emits a
    single extra SELECT that returns zero rows and does not violate NFR-01.
    """

    __tablename__ = "task_results"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=False)
    exit_code = Column(String(16), nullable=True)
    stdout_tail = Column(Text, nullable=True)
    stderr_tail = Column(Text, nullable=True)
    duration_ms = Column(String(32), nullable=True)
    finished_at = Column(String(64), nullable=True)


class ApiKey(Base):
    """An API key row used by FR-03 authentication.

    [FR-03] Only the SHA-256 ``key_hash`` is persisted (64-char lowercase
    hex). The plaintext key never touches the database — the service layer
    hashes before write and the lookup path compares hashes via
    ``hmac.compare_digest``. A non-null ``revoked_at`` disqualifies the key
    from authentication (AC-3.4).

    Citations:
    - SPEC.md#L101-L107 (FR-03 — api_keys, SHA-256, revoked)
    - SAD.md#L120-L135 (§2.4 `models/orm.py` — Base + per-table ORM classes)
    """

    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    scope = Column(String(32), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class RateBucket(Base):
    """The persisted token bucket of one API key (FR-05).

    [FR-05] The bucket lives in the database rather than in process memory
    so every worker shares a single counter — a per-process bucket would let
    N workers each grant the full burst. ``key_id`` is the primary key, which
    both enforces one bucket per key and gives the refill path a single row
    to lock (AC-5.2).

    Citations:
    - SPEC.md#L115-L120 (FR-05 — 令牌桶狀態存於資料庫, row-level lock)
    - SAD.md#L129 (§2.4 `models/` — RateBucket is part of the ORM surface)
    """

    __tablename__ = "rate_buckets"

    key_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Task(Base):
    """A scheduled command to be executed by the FR-02 runner.

    [FR-01] The table is created by ``Base.metadata.create_all`` in tests; the
    v1 migration script (FR-07) creates the same DDL against a real Postgres
    DB. ``name`` carries a unique index so that two concurrent POSTs cannot
    both insert a row with the same name — the second one fails with an
    ``IntegrityError`` that the route handler maps to 409 + problem+json.
    """

    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    name = Column(String(255), nullable=False)
    command = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("name", name="uq_tasks_name"),
    )

    # Relationships used by ``selectinload`` in the list query. The child
    # tables may not yet exist in v1; the loader emits a single extra SELECT
    # that returns 0 rows, which keeps the SQL count constant (NFR-01).
    results: Mapped[List["TaskResult"]] = relationship(
        "TaskResult",
        backref="task",
        lazy="selectin",
    )
    tags: Mapped[List["Tag"]] = relationship(
        "Tag",
        secondary=task_tags_table,
        backref="tasks",
        lazy="selectin",
    )


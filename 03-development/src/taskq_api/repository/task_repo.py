"""Repository functions for the ``tasks`` table.

[FR-01] Every read and write of a task row goes through this module — the
service layer never reaches into ``Session`` directly, and the API layer
never writes SQL. Pagination is cursor-based: ``list_tasks`` accepts an
opaque ``cursor`` payload and is forbidden from accepting ``offset`` (NFR-06
layering + SPEC.md §3 FR-01).

The list query uses ``selectinload`` on the relationships so the SQL count
stays constant regardless of how many rows the page returns (NFR-01). There
are no relationships on the v1 ``Task`` row, so ``selectinload`` is wired to
``Task.__table__`` only for the future-proof shape; the count-constant
property is verified by the integration test that fires 10 000 seeded rows.

Citations:
- SPEC.md#L79-L91 (FR-01 — POST/GET/LIST/DELETE /v1/tasks)
- SPEC.md#L122-L128 (FR-06 — repository owns Session, no raw SQL)
- SPEC.md#L127 (FR-06 — ``selectinload`` / ``joinedload``; N+1 is acceptance failure)
- SPEC.md#L177-L182 (NFR-01 — constant SQL statement count)
- SAD.md#L131-L145 (§2.4 `task_repo.py` — cursor pagination, selectinload)
- SRS.md#L92-L131 (AC-1.1..AC-1.7)
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from taskq_api.errors import ConflictError
from taskq_api.models.orm import Task

__all__ = ["create_task", "get_task", "list_tasks", "delete_task"]


def create_task(session: Session, *, name: str, command: str) -> Task:
    """Insert a new task row; commit/rollback is the caller's responsibility.

    [FR-01] The unique index on ``name`` (defined in ``models.orm``) is the
    only thing that makes ``IntegrityError`` -> 409 a race-free operation.
    On duplicate-name the repository translates the DB-level violation to
    ``ConflictError`` (a domain exception in ``errors.py``) so the service
    layer never needs to know about SQLAlchemy exception types (NFR-06).
    """
    row = Task(name=name, command=command)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        msg = str(exc.orig).lower() if exc.orig else ""
        if "unique" in msg or "uq_tasks_name" in msg:
            raise ConflictError("task name already exists") from exc
        raise
    return row


def get_task(session: Session, task_id: str) -> Task | None:
    """Return a single task by id, or ``None`` if no row matches.

    [FR-01] AC-1.3: the route maps ``None`` to 404 + problem+json.
    """
    return session.get(Task, task_id)


def list_tasks(
    session: Session,
    *,
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[dict[str, Any]] = None,
) -> tuple[Sequence[Task], Optional[str]]:
    """Return a page of tasks plus the next-cursor handle.

    [FR-01] AC-1.5: cursor-only pagination. ``cursor`` is the decoded payload
    from the previous page; we use it as a keyset filter (``id > last_id``).
    No ``.OFFSET(`` call may appear in this file — the integration test
    grep-tests the module source for that token.

    The two ``selectinload`` calls eagerly load the (v2/v3) child rowsets;
    they emit at most one extra SELECT each, so the per-request statement
    count is constant regardless of how many rows the page returns (NFR-01).
    """
    stmt = select(Task).options(
        selectinload(Task.results),
        selectinload(Task.tags),
    )
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if cursor is not None:
        last_id = cursor.get("task_id")
        if last_id is not None:
            stmt = stmt.where(Task.id > last_id)
    stmt = stmt.order_by(Task.id.asc()).limit(limit)

    rows = session.execute(stmt).scalars().all()

    next_cursor: Optional[str] = None
    if len(rows) == limit:
        next_cursor = cast(str, rows[-1].id)
    return rows, next_cursor


def delete_task(session: Session, task: Task) -> None:
    """Delete a task row; commit/rollback is the caller's responsibility.

    [FR-01] AC-1.7: deletion of a task also removes its results rows, but in
    v1 schema the ``task_results`` table does not exist yet; the
    same-transaction rule is enforced by ``session_scope()`` instead.
    """
    session.delete(task)
    session.flush()

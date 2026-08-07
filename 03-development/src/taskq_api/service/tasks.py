"""Task service layer — business rules on top of the repository.

[FR-01] The service layer is the only place where the FR-01 business rules
are enforced:

  * validation already happens in ``TaskCreate`` (pydantic) — the service
    catches the ``ValidationError`` and re-raises as ``ValidationProblem``
    (so the handler can return 422 + problem+json with the right media type);
  * name uniqueness is enforced via the database unique index; on
    ``IntegrityError`` the service raises ``ConflictError`` (→ 409);
  * cursor pagination helpers (``encode_cursor`` / ``decode_cursor``) are
    opaque to the HTTP edge: the route passes the cursor string in, gets the
    next-cursor string out, and nothing else.
  * limit cap is enforced at the boundary so a 201 from the client cannot
    silently get clamped to 200.

The service depends on the repository and the pydantic schemas; it never
imports ``fastapi`` (NFR-06 layering).

Citations:
- SPEC.md#L79-L91 (FR-01 — CRUD behaviour; cursor-based pagination)
- SPEC.md#L90-L91 (FR-01 — 預設 limit 50, 上限 200; 超過上限 → 422)
- SAD.md#L146-L165 (§2.4 `service/tasks.py` — validation, conflict, cursor)
- SRS.md#L92-L131 (AC-1.1..AC-1.7)
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from taskq_api.errors import ConflictError, NotFoundError, ValidationProblem
from taskq_api.models import schemas
from taskq_api.repository import task_repo
from taskq_api.repository.session import session_scope

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "encode_cursor",
    "decode_cursor",
    "create_task",
    "get_task",
    "list_tasks",
    "delete_task",
]

# SPEC.md §3 FR-01 — 預設 limit 50, 上限 200.
DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200


# --------------------------------------------------------------------------
# Cursor helpers — opaque to the HTTP edge.
# --------------------------------------------------------------------------


def encode_cursor(payload: dict[str, Any]) -> str:
    """Return the urlsafe-base64 JSON encoding of ``payload``.

    [FR-01] AC-1.5: the cursor is opaque to the client; we store it as
    base64-encoded JSON because the only field today is ``task_id`` and
    JSON is the cheapest way to grow the payload later (e.g. ``status``,
    ``created_at``) without breaking the format.
    """
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Inverse of :func:`encode_cursor`; raises ``ValidationProblem`` on bad input.

    [FR-01] Malformed cursors must not crash the handler with a 500; the
    service translates them to 422.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        decoded = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationProblem("cursor is malformed") from exc
    if not isinstance(decoded, dict):
        raise ValidationProblem("cursor must decode to an object")
    return decoded


# --------------------------------------------------------------------------
# Service entry points.
# --------------------------------------------------------------------------


def create_task(*, name: str, command: str) -> schemas.TaskRead:
    """Create a new task; returns the persisted row.

    [FR-01] Pydantic validation runs first; an ``IntegrityError`` on
    duplicate name is mapped to ``ConflictError`` (→ 409).
    """
    try:
        payload = schemas.TaskCreate(name=name, command=command)
    except ValidationError as exc:
        raise ValidationProblem(f"validation failed: {exc.errors()}") from exc

    try:
        with session_scope() as session:
            row = task_repo.create_task(
                session, name=payload.name, command=payload.command
            )
            session.flush()
            session.expire(row)  # ensure fresh __dict__ after commit
            return schemas.TaskRead.model_validate(row)
    except IntegrityError as exc:
        # Name uniqueness violation is the only IntegrityError we expect
        # from this insert path; anything else is a real bug and propagates.
        msg = str(exc.orig).lower() if exc.orig else ""
        if "unique" in msg or "uq_tasks_name" in msg:
            raise ConflictError("task name already exists") from exc
        raise


def get_task(task_id: str) -> schemas.TaskRead:
    """Return the task with id ``task_id`` or raise 404."""
    with session_scope() as session:
        row = task_repo.get_task(session, task_id)
        if row is None:
            raise NotFoundError("task not found")
        return schemas.TaskRead.model_validate(row)


def list_tasks(
    *, status: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None
) -> schemas.TaskList:
    """Return a page of tasks plus the opaque next-cursor handle.

    [FR-01] AC-1.6: ``limit`` over ``MAX_LIMIT`` is rejected as 422 rather
    than silently clamped, so the documented contract is preserved.
    """
    effective_limit = limit if limit is not None else DEFAULT_LIMIT
    if effective_limit < 1 or effective_limit > MAX_LIMIT:
        raise ValidationProblem(
            f"limit must be between 1 and {MAX_LIMIT}, got {effective_limit}"
        )

    decoded_cursor: Optional[dict[str, Any]] = None
    if cursor is not None:
        decoded_cursor = decode_cursor(cursor)

    with session_scope() as session:
        rows, next_id = task_repo.list_tasks(
            session,
            status=status,
            limit=effective_limit,
            cursor=decoded_cursor,
        )
        items = [schemas.TaskRead.model_validate(r) for r in rows]

    next_cursor = encode_cursor({"task_id": next_id}) if next_id else None
    return schemas.TaskList(items=items, next_cursor=next_cursor)


def delete_task(task_id: str) -> None:
    """Delete the task with id ``task_id``; raise 404 if no such task."""
    with session_scope() as session:
        row = task_repo.get_task(session, task_id)
        if row is None:
            raise NotFoundError("task not found")
        task_repo.delete_task(session, row)

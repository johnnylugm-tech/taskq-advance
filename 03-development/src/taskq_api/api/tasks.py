"""HTTP routes for ``/v1/tasks`` (FR-01).

[FR-01] Each handler is thin — it parses the request, asks the service
layer, and serialises the answer. The 422/404/409 contract is owned by the
service layer (``ValidationProblem``/``NotFoundError``/``ConflictError``);
the exception handlers in ``taskq_api.app`` translate them into
``application/problem+json`` responses.

Citations:
- SPEC.md#L79-L91 (FR-01 — POST/GET/LIST/DELETE /v1/tasks; scopes)
- SPEC.md#L162-L168 (FR-10 — problem+json contract)
- SAD.md#L166-L175 (§2.4 `api/tasks.py` — route handlers, ≤ 40 lines each)
- SRS.md#L92-L131 (AC-1.1..AC-1.7)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status

from taskq_api.api.deps import Principal, auth_dep, check_scope
from taskq_api.errors import ProblemError
from taskq_api.service import tasks as tasks_service

__all__ = ["router"]

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=None)
def create_task(
    body: dict,
    response: Response,
    principal: Principal = Depends(auth_dep),
) -> dict:
    """``POST /v1/tasks`` — create a new task (scope: write)."""
    check_scope(principal, "write")
    created = tasks_service.create_task(
        name=body.get("name", ""),
        command=body.get("command", ""),
    )
    response.status_code = status.HTTP_201_CREATED
    return created.model_dump()


@router.get("/{task_id}", response_model=None)
def get_task(
    task_id: str = Path(...),
    principal: Principal = Depends(auth_dep),
) -> dict:
    """``GET /v1/tasks/{id}`` — fetch a single task (scope: read)."""
    check_scope(principal, "read")
    found = tasks_service.get_task(task_id)
    return found.model_dump()


@router.get("", response_model=None)
def list_tasks(
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1),
    cursor: Optional[str] = Query(default=None),
    principal: Principal = Depends(auth_dep),
) -> dict:
    """``GET /v1/tasks`` — paged list (scope: read)."""
    check_scope(principal, "read")
    page = tasks_service.list_tasks(status=status, limit=limit, cursor=cursor)
    return {
        "items": [item.model_dump() for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str = Path(...),
    principal: Principal = Depends(auth_dep),
) -> Response:
    """``DELETE /v1/tasks/{id}`` — delete a task (scope: admin)."""
    check_scope(principal, "admin")
    tasks_service.delete_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _reraise_problem(exc: ProblemError) -> None:
    """Re-raise so the app-level exception handlers can render it.

    Kept as a no-op shim so the import stays meaningful even when FastAPI's
    dependency system has already promoted the exception to a response.
    """
    raise exc

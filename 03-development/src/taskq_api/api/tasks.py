"""HTTP routes for task CRUD and execution.

[FR-02] Adds asynchronous execution and newest-first run history.

Citations:
- SPEC.md#L79-L91 (FR-02 endpoints and scopes)
- SAD.md#L166-L175 (API route ownership)
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status

from taskq_api.api.deps import Principal, auth_dep, check_scope
from taskq_api.models.orm import TaskResult
from taskq_api.service import runner
from taskq_api.service import tasks as tasks_service

__all__ = ["router"]
router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _serialize_result(result: TaskResult) -> dict[str, Any]:
    """Convert a persisted execution result into its HTTP representation."""
    return {
        "id": result.id,
        "task_id": result.task_id,
        "exit_code": result.exit_code,
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
        "duration_ms": result.duration_ms,
        "finished_at": result.finished_at,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=None)
def create_task(body: dict, principal: Principal = Depends(auth_dep)) -> dict:
    """Create a task. [FR-01] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "write")
    return tasks_service.create_task(
        name=body.get("name", ""), command=body.get("command", "")
    ).model_dump()


@router.get("/{task_id}", response_model=None)
def get_task(
    task_id: str = Path(...), principal: Principal = Depends(auth_dep)
) -> dict:
    """Fetch a task. [FR-01] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "read")
    return tasks_service.get_task(task_id).model_dump()


@router.get("", response_model=None)
def list_tasks(
    status: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1),
    cursor: Optional[str] = Query(default=None),
    principal: Principal = Depends(auth_dep),
) -> dict:
    """List tasks. [FR-01] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "read")
    page = tasks_service.list_tasks(status=status, limit=limit, cursor=cursor)
    return {
        "items": [item.model_dump() for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str = Path(...), principal: Principal = Depends(auth_dep)
) -> Response:
    """Delete a task. [FR-01] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "admin")
    tasks_service.delete_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_task(
    task_id: str = Path(...), principal: Principal = Depends(auth_dep)
) -> dict:
    """Start task execution. [FR-02] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "write")
    task = tasks_service.get_task(task_id)
    return {"run_id": runner.start_task(task_id, task.command)}


@router.get("/{task_id}/runs", response_model=None)
def task_runs(
    task_id: str = Path(...), principal: Principal = Depends(auth_dep)
) -> list[dict[str, Any]]:
    """Return newest-first execution history. [FR-02] Citations: SPEC.md#L79-L91."""
    check_scope(principal, "read")
    return [_serialize_result(result) for result in runner.list_results(task_id)]

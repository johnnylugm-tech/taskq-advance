"""HTTP route for the FR-04 / FR-09 metrics endpoint.

[FR-04] The metrics endpoint is enumerated alongside the /v1/tasks routes
in AC-4.2 so it MUST be registered on the app and MUST pass through the
single ``auth_dep`` dependency.

[FR-09] AC-9.5: the metrics endpoint MUST require ``admin`` scope and
expose at least the task-count signal so an operator can confirm the
endpoint actually returned data once they present an admin key. A
``read`` key — which is sufficient for ``GET /v1/tasks/{id}`` — MUST be
rejected with 403 so task data cannot be enumerated by a lower-privilege
caller (SPEC.md §3 FR-09, FR-09 case 5).

Citations:
- SPEC.md#L109-L113 (FR-04 — single auth dependency across /v1)
- SPEC.md#L151-L157 (FR-09 — /v1/metrics, admin scope, task counts)
- SAD.md#L166-L175 (API route ownership)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from taskq_api.api.deps import Principal, auth_dep, check_scope
from taskq_api.repository import session as db_session
from taskq_api.repository import task_repo

__all__ = ["router"]

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("", response_model=None)
def get_metrics(principal: Principal = Depends(auth_dep)) -> dict[str, Any]:
    """Return service metrics; admin scope only. [FR-09] Citations: SPEC.md#L151-L157."""
    # [FR-09] AC-9.5: a non-admin principal MUST be rejected as 403 so a
    # ``read`` key — which can enumerate ``GET /v1/tasks`` — cannot pull
    # the same task data via /v1/metrics. ``check_scope`` raises
    # ``ForbiddenError`` which the global handler renders as 403 +
    # problem+json (FR-04 AC-4.1).
    check_scope(principal, "admin")

    # [FR-09] AC-9.5: the body exposes task counts grouped by status so an
    # operator can confirm the endpoint actually returned data once an
    # admin key is used. The aggregate is computed in the repository
    # layer (NFR-06 / FR-06) — the API layer never imports SQLAlchemy.
    # The ``task_count`` key is the substring TEST_SPEC rule
    # ``FR09-metrics-403-for-non-admin`` keys off; ``tasks_by_status`` is
    # the FR-09 "task counts (by status)" requirement.
    with db_session.session_scope() as session:
        counts = task_repo.count_tasks_by_status(session)
    return {
        "task_count": counts["total"],
        "tasks_by_status": counts,
    }
"""Operator health probes — ``/healthz`` and ``/readyz`` (FR-09).

[FR-09] Two liveness / readiness endpoints aimed at orchestrators (k8s,
load balancers, the operator CLI). Both MUST stay reachable without
authentication and MUST NOT consult the rate-limiter — the
``auth_dep`` chain is wired only into ``/v1/*`` so these probes carry
no principal and consume no bucket.

* ``GET /healthz`` — liveness. Returns ``{"status": "ok"}`` with HTTP 200
  as long as the process is alive and the FastAPI event loop is serving
  requests. Used by the orchestrator's liveness probe to decide whether
  to restart the pod (SPEC.md §3 FR-09, AC-9.4).

* ``GET /readyz`` — readiness. Composes
  :func:`taskq_api.repository.session.check_db_ready` and
  :func:`taskq_api.repository.session.migration_at_head` so a pod whose
  dependency surface is degraded keeps itself out of the load balancer
  (SPEC.md §3 FR-09, AC-9.1 / AC-9.2 / AC-9.3). Returns 200 with
  ``{"status":"ready"}`` on success; 503 with a body that names the
  failed component on any failure so an operator can act on it
  (SPEC.md §8 #10 / #11).

The 503 body shape is ``{"status":"not ready","detail":...}`` — the test
contract pins ``"database"`` / ``"migration"`` substrings on the wire
(TEST_SPEC rules ``FR09-readyz-503-db-down`` /
``FR09-readyz-503-migration-lag`` / ``FR09-readyz-fails-closed``) so the
diagnostic substrings stay explicit.

Citations:
- SPEC.md#L151-L157 (FR-09 — /healthz, /readyz, /v1/metrics)
- SPEC.md#L210-L213 (§8 #10 / #11 — 503 with the failed component named)
- SAD.md#L166-L175 (§2.4 `api/health.py` — included by `create_app`)
- TEST_SPEC.md §FR-09 cases 1-4 and sub-assertion table
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from taskq_api.repository import session as db_session

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=None)
def healthz() -> dict:
    """Return the liveness envelope. [FR-09] Citations: SPEC.md#L151."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
def readyz() -> Response:
    """Return the readiness verdict — 200 ready or 503 with a detail body.

    [FR-09] AC-9.1 / AC-9.2 / AC-9.3: the readiness probe MUST report
    503 (not 200) whenever the database is unreachable, the migration
    revision is behind head, or no migration has been applied. The body
    names the failed component so an operator reading
    ``kubectl describe pod`` can act on the probe failure.

    The handler is deliberately tolerant: any exception from
    ``check_db_ready`` is treated as "database unavailable" — a router
    that propagated ``OperationalError`` would surface as a generic 500
    and stop the orchestrator from running the right probe.
    """
    # [FR-09] AC-9.1: a database that cannot answer ``SELECT 1`` fails
    # closed with a body that names the database as the failed
    # component. The handler catches every exception rather than
    # enumerating them so a future pool exhaustion / auth failure /
    # network partition all surface the same operator-readable string.
    try:
        db_ready = bool(db_session.check_db_ready())
    except Exception:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content='{"status":"not ready","detail":"database unreachable"}',
            media_type="application/json",
        )
    if not db_ready:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content='{"status":"not ready","detail":"database unreachable"}',
            media_type="application/json",
        )

    # [FR-09] AC-9.2 / AC-9.3: a migration that is not at the head
    # revision (or that has never been applied — ``current_revision``
    # is ``None``) MUST fail closed. ``migration_at_head`` returns the
    # ``(current, head)`` tuple the handler compares.
    current_revision, head_revision = db_session.migration_at_head()
    if current_revision != head_revision:
        detail = (
            f"migration not at head: current={current_revision!r} "
            f"head={head_revision!r}"
        )
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=f'{{"status":"not ready","detail":"{detail}"}}',
            media_type="application/json",
        )

    return Response(
        status_code=status.HTTP_200_OK,
        content='{"status":"ready"}',
        media_type="application/json",
    )
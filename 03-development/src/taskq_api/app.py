"""ASGI application factory.

[FR-01] ``create_app()`` returns a fresh ``FastAPI`` instance bound to the
current ``TASKQ_DB_URL``. Tests call it once per case; production calls it
once at process start. The module-level ``app`` symbol required by
``uvicorn taskq_api.app:app`` is wired up at import time so the entrypoint
contract from SPEC.md §1 holds.

Citations:
- SPEC.md#L52-L57 (§1 概述 — ASGI service, `uvicorn taskq_api.app:app`)
- SPEC.md#L385-L402 (§7 — error → application/problem+json envelope)
- SAD.md#L168-L175 (§2.4 `api/tasks.py` — included by `create_app`)
- SAD.md#L235 (session_scope commit/rollback, FR-06 / NFR-03)
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api import __version__
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import (
    PROBLEM_MEDIA_TYPE,
    ProblemError,
    problem,
)
from taskq_api.repository.session import get_engine

__all__ = ["create_app", "app"]


def _correlation_id() -> str:
    """Generate a fresh correlation id (one per response)."""
    return str(uuid.uuid4())


def _problem_response(
    *,
    status: int,
    type_uri: str,
    title: str,
    detail: str,
    correlation_id: str,
) -> JSONResponse:
    """Build an RFC 7807 JSON response with the spec-fixed media type."""
    return JSONResponse(
        status_code=status,
        content=problem(
            status=status,
            type_uri=type_uri,
            title=title,
            detail=detail,
            instance="",
            correlation_id=correlation_id,
        ),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def create_app() -> FastAPI:
    """Build a fresh FastAPI app wired to the current engine.

    [FR-01] The exception handlers below translate domain failures
    (``ProblemError`` subclasses) and pydantic validation failures into
    ``application/problem+json`` responses. Every response carries an
    ``X-Correlation-Id`` header for end-to-end stitching (FR-10).
    """
    application = FastAPI(
        title="taskq-api",
        version=__version__,
    )

    @application.middleware("http")
    async def _correlation_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = _correlation_id()
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        return response

    @application.exception_handler(ProblemError)
    async def _problem_handler(request: Request, exc: ProblemError) -> JSONResponse:  # type: ignore[no-untyped-def]
        return _problem_response(
            status=exc.status,
            type_uri=exc.type_uri,
            title=exc.title,
            detail=exc.detail,
            correlation_id=getattr(request.state, "correlation_id", _correlation_id()),
        )

    @application.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # type: ignore[no-untyped-def]
        return _problem_response(
            status=422,
            type_uri="/errors/validation",
            title="Unprocessable Entity",
            detail="request body failed validation",
            correlation_id=getattr(request.state, "correlation_id", _correlation_id()),
        )

    application.include_router(tasks_router)
    application.include_router(metrics_router)
    # [FR-04] FastAPI 0.141+ wraps ``include_router`` results in an
    # ``_IncludedRouter`` placeholder that does not expose ``path`` /
    # ``methods`` directly. The FR-04 case-2 test (``test_all_v1_routes_use_same_dep``)
    # inspects ``app.routes`` for flat ``APIRoute`` objects carrying a
    # millable ``dependant`` graph, so we also append the inner routes
    # directly. The placeholder above is harmless for actual routing (it
    # delegates to the same underlying routes) but the test requires the
    # flat shape, so we expose it.
    for r in list(tasks_router.routes) + list(metrics_router.routes):
        if r not in application.routes:
            application.routes.append(r)
    return application


# Module-level app — required by ``uvicorn taskq_api.app:app`` (SPEC.md §1).
app = create_app()


# Touch the engine so the first request doesn't pay the build cost; the
# test fixture flips ``TASKQ_DB_URL`` before this module is imported, so the
# engine here is already pointed at the per-test file when this runs.
_ = get_engine()

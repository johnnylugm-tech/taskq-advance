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
from fastapi.routing import APIRoute, APIRouter

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


def _register_router(application: FastAPI, router: APIRouter) -> None:
    """Register every route of ``router`` flat on ``application``.

    [FR-04] AC-4.2 requires that every ``/v1`` route be observably wired to
    the single ``auth_dep`` dependency. As of FastAPI 0.141, ``include_router``
    no longer copies the router's path operations into ``app.routes``; it
    appends one lazy ``_IncludedRouter`` placeholder that resolves the
    underlying routes at dispatch time and exposes neither ``path``,
    ``methods`` nor ``dependant``. That makes the app's authorisation wiring
    unauditable — neither the AC-4.2 test nor an operator can enumerate which
    routes actually sit behind ``auth_dep``.

    Re-adding each path operation through ``application.router.add_api_route``
    restores the flat, inspectable shape. Going through ``add_api_route``
    (rather than appending the router's own ``APIRoute`` objects) is what makes
    the routes usable: ``APIRoute`` captures its ``dependency_overrides_provider``
    at construction time, so a route built by a bare ``APIRouter`` carries
    ``None`` and silently ignores ``app.dependency_overrides``. Rebuilding the
    route against ``application.router`` binds the app as the provider, so
    dependency overrides resolve as documented.

    Each route is registered exactly once — mixing ``include_router`` with a
    flat copy would double-register every path operation and emit a duplicate
    operation id into the OpenAPI schema.

    Citations:
    - SPEC.md#L109-L113 (FR-04 — authorisation via a single dependency)
    - SAD.md#L168-L175 (§2.4 `api/tasks.py` — included by `create_app`)
    """
    for route in router.routes:
        # ``APIRouter.routes`` is typed ``list[BaseRoute]``; only ``APIRoute``
        # carries the ``endpoint`` / ``response_model`` attributes read below.
        if not isinstance(route, APIRoute):
            continue
        application.router.add_api_route(
            route.path,
            route.endpoint,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            methods=route.methods,
            operation_id=route.operation_id,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=route.include_in_schema,
            response_class=route.response_class,
            name=route.name,
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

    _register_router(application, tasks_router)
    _register_router(application, metrics_router)
    return application


# Module-level app — required by ``uvicorn taskq_api.app:app`` (SPEC.md §1).
app = create_app()


# Touch the engine so the first request doesn't pay the build cost; the
# test fixture flips ``TASKQ_DB_URL`` before this module is imported, so the
# engine here is already pointed at the per-test file when this runs.
_ = get_engine()

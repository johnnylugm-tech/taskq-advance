"""ASGI application factory.

[FR-01] ``create_app()`` returns a fresh ``FastAPI`` instance bound to the
current ``TASKQ_DB_URL``. Tests call it once per case; production calls it
once at process start. The module-level ``app`` symbol required by
``uvicorn taskq_api.app:app`` is wired up at import time so the entrypoint
contract from SPEC.md §1 holds.

[FR-10] The exception handlers below translate domain failures
(``ProblemError`` subclasses), pydantic validation failures, and any
un-modelled exception escaping a handler into ``application/problem+json``
responses. The catch-all ``Exception`` handler is registered AFTER the
``ProblemError`` / ``RequestValidationError`` handlers — Starlette matches
handlers by exception class, so the more specific ones fire first. The
catch-all explicitly re-raises ``asyncio.CancelledError`` so SPEC.md §7's
"NFR-03: cancellation propagates untouched" rule holds (the cancelled
request reaches the ASGI server's cancel handling, not a 500 envelope).

Every response carries an ``X-Correlation-Id`` header for end-to-end
stitching (AC-10.3): the middleware adopts an inbound value via
``api.deps.bind_correlation_id`` and writes one server log line per
request so a log-grep can find exactly that request.

Citations:
- SPEC.md#L52-L57 (§1 概述 — ASGI service, `uvicorn taskq_api.app:app`)
- SPEC.md#L162-L168 (FR-10 — problem+json + correlation_id)
- SPEC.md#L385-L402 (§7 — error → application/problem+json envelope, NFR-03)
- SAD.md#L168-L175 (§2.4 `api/tasks.py` — included by `create_app`)
- SAD.md#L235 (session_scope commit/rollback, FR-06 / NFR-03)
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, APIRouter

from taskq_api import __version__
from taskq_api.api.deps import bind_correlation_id, problem_instance
from taskq_api.api.health import router as health_router
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import (
    PROBLEM_MEDIA_TYPE,
    STATUS_TYPE_MAP,
    ProblemError,
    problem,
)
from taskq_api.repository.session import get_engine

__all__ = ["create_app", "app"]

# [FR-10] Server logger. One INFO line per request, carrying the correlation
# id, so a log-grep for an id taken from a client response yields exactly
# one server-side record (AC-10.3, TEST_SPEC FR10-correlation-id-matches
# match_count == '1').
_logger = logging.getLogger("taskq_api.access")


def _problem_response(
    *,
    status: int,
    type_uri: str,
    title: str,
    detail: str,
    correlation_id: str,
    instance: str = "",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an RFC 7807 JSON response with the spec-fixed media type.

    [FR-05] ``headers`` carries the per-status extras a problem may need —
    a 429 must ship ``Retry-After`` alongside the envelope (AC-5.1).

    [FR-10] ``instance`` is threaded through from the handler so it
    identifies the request that failed (RFC 7807 §3.1) — AC-10.1's
    "instance" field is empty by default and is filled with the request
    path by the registered handlers.
    """
    return JSONResponse(
        status_code=status,
        content=problem(
            status=status,
            type_uri=type_uri,
            title=title,
            detail=detail,
            instance=instance,
            correlation_id=correlation_id,
        ),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
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
        cid = bind_correlation_id(request)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        # [FR-10] AC-10.3 / TEST_SPEC FR10-correlation-id-matches: one
        # log line per request carrying the correlation id, so a
        # client-supplied id can be grep'd to find exactly this request.
        _logger.info(
            "%s %s -> %s correlation_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            cid,
        )
        return response

    @application.exception_handler(ProblemError)
    async def _problem_handler(request: Request, exc: ProblemError) -> JSONResponse:  # type: ignore[no-untyped-def]
        return _problem_response(
            status=exc.status,
            type_uri=exc.type_uri,
            title=exc.title,
            detail=exc.detail,
            correlation_id=getattr(request.state, "correlation_id", ""),
            instance=problem_instance(request, exc.status),
            headers=exc.headers or None,
        )

    @application.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # type: ignore[no-untyped-def]
        return _problem_response(
            status=422,
            type_uri=STATUS_TYPE_MAP[422],
            title="Unprocessable Entity",
            detail="request body failed validation",
            correlation_id=getattr(request.state, "correlation_id", ""),
            instance=problem_instance(request, 422),
        )

    @application.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[no-untyped-def]
        # [FR-10 / NFR-03] SPEC.md §7 requires ``asyncio.CancelledError`` to
        # propagate untouched so a cancelled request reaches the ASGI
        # server's cancel handling. Re-raising it from the catch-all
        # handler is what honours that contract — Starlette's default
        # ``ServerErrorMiddleware`` would otherwise answer plain-text 500.
        if isinstance(exc, asyncio.CancelledError):
            raise exc
        cid = getattr(request.state, "correlation_id", "")
        # [FR-10] AC-10.2 / §8 #19: the 500 body is a fixed,
        # caller-facing sentence — it MUST NOT echo the exception (no
        # stack, no SQL, no filesystem path) because that detail string
        # is what a downstream operator or attacker reads first.
        return _problem_response(
            status=500,
            type_uri=STATUS_TYPE_MAP[500],
            title="Internal Server Error",
            detail="an internal error occurred",
            correlation_id=cid,
            instance=problem_instance(request, 500),
        )

    _register_router(application, tasks_router)
    _register_router(application, metrics_router)
    _register_router(application, health_router)
    return application


# Module-level app — required by ``uvicorn taskq_api.app:app`` (SPEC.md §1).
app = create_app()


# Touch the engine so the first request doesn't pay the build cost; the
# test fixture flips ``TASKQ_DB_URL`` before this module is imported, so the
# engine here is already pointed at the per-test file when this runs.
_ = get_engine()

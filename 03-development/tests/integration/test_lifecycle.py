"""Live-ASGI integration tests for taskq_api (NFR-10).

Boots the FastAPI app via ``httpx.ASGITransport`` and exercises the public
HTTP surface end-to-end. Mirrors the test_fr05 async-driving pattern
(``asyncio.new_event_loop().run_until_complete``) so the suite works on a
pytest without an installed anyio plugin.
"""

from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from taskq_api.repository import session as db_session  # noqa: E402
from taskq_api.app import create_app  # noqa: E402
from taskq_api.models import orm  # noqa: E402


def _build_app():
    """Fresh app against the current ``TASKQ_DB_URL`` (mirrors test_fr05)."""
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


def test_healthz_endpoint_live() -> None:
    """``/healthz`` returns 200 with ``status: ok`` through the live ASGI stack."""
    application = _build_app()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get("/healthz")

    response = _run(_do())

    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok"


def test_metrics_route_wired() -> None:
    """``/v1/metrics`` is wired into the live ASGI stack (auth still required)."""
    application = _build_app()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get("/v1/metrics")

    response = _run(_do())

    # Without an API key the route is gated, so we accept 401/200/403. The
    # important property is that the path exists and is wired through the
    # full middleware stack — a 404 would mean the route was not registered.
    assert response.status_code in (200, 401, 403)


def test_openapi_schema_live() -> None:
    """``/openapi.json`` returns 200 and advertises the public v1 routes."""
    application = _build_app()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get("/openapi.json")

    response = _run(_do())

    assert response.status_code == 200
    schema = response.json()
    assert schema.get("openapi", "").startswith("3.")
    paths = schema.get("paths", {})
    assert any(path.startswith("/v1/tasks") for path in paths), paths

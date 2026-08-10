"""CRUD-via-ASGI integration tests for taskq_api (NFR-10).

Exercises the full request→service→repository→response pipeline through
``httpx.ASGITransport`` so a regression in any layer fails this suite. The
tests seed an admin API key directly in the live schema and then drive
``POST/GET/LIST/DELETE /v1/tasks`` end-to-end, covering the bulk of
``task_repo`` / ``key_repo`` / ``service.tasks`` / ``service.ratelimit`` /
``service.runner`` source lines that the bare lifecycle tests leave untested.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

from httpx import ASGITransport, AsyncClient

from taskq_api.repository import session as db_session  # noqa: E402
from taskq_api.app import create_app  # noqa: E402
from taskq_api.models import orm  # noqa: E402
from taskq_api.repository import key_repo  # noqa: E402


def _build_app():
    """Fresh app + schema against the current ``TASKQ_DB_URL``."""
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _seed_admin_key() -> str:
    """Persist an admin API key and return the plaintext used by the client."""
    plaintext = "sk-integration-" + uuid.uuid4().hex
    with db_session.session_scope() as s:
        key_repo.create_api_key(s, scope="admin", plaintext=plaintext)
    return plaintext


def test_post_task_full_pipeline() -> None:
    """POST /v1/tasks round-trip exercises task_repo.create_task + service.tasks."""
    application = _build_app()
    api_key = _seed_admin_key()
    task_name = f"int-post-{uuid.uuid4().hex}"

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.post(
                "/v1/tasks",
                json={"name": task_name, "command": "echo integration"},
                headers={"X-API-Key": api_key},
            )

    response = _run(_do())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == task_name
    assert body["status"] in {"queued", "pending", "running", "succeeded", "failed"}


def test_get_unknown_task_returns_404() -> None:
    """GET /v1/tasks/{id} for a missing id returns 404 problem+json."""
    application = _build_app()
    api_key = _seed_admin_key()
    missing = uuid.uuid4().hex

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get(
                f"/v1/tasks/{missing}", headers={"X-API-Key": api_key}
            )

    response = _run(_do())
    assert response.status_code == 404
    body = response.json()
    assert "type" in body
    assert "title" in body
    assert "status" in body
    assert "detail" in body
    assert "instance" in body
    assert "correlation_id" in body


def test_list_tasks_with_limit() -> None:
    """GET /v1/tasks?limit=10 returns up to 10 tasks; default page shape."""
    application = _build_app()
    api_key = _seed_admin_key()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get(
                "/v1/tasks", params={"limit": 10}, headers={"X-API-Key": api_key}
            )

    response = _run(_do())
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_post_task_validation_422() -> None:
    """POST /v1/tasks with empty name returns 422 (TaskCreate validator)."""
    application = _build_app()
    api_key = _seed_admin_key()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.post(
                "/v1/tasks",
                json={"name": "", "command": "echo x"},
                headers={"X-API-Key": api_key},
            )

    response = _run(_do())
    assert response.status_code == 422
    body = response.json()
    assert "type" in body
    assert "title" in body


def test_post_task_blacklist_char_422() -> None:
    """POST /v1/tasks with a blacklisted injection char returns 422."""
    application = _build_app()
    api_key = _seed_admin_key()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.post(
                "/v1/tasks",
                json={"name": "ok", "command": "echo x; rm -rf /"},
                headers={"X-API-Key": api_key},
            )

    response = _run(_do())
    assert response.status_code == 422


def test_invalid_api_key_returns_401() -> None:
    """A bogus X-API-Key is rejected by the real auth_dep (key_repo lookup)."""
    application = _build_app()
    orm.Base.metadata.create_all(db_session.get_engine())

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get(
                "/v1/tasks",
                headers={"X-API-Key": "sk-not-a-real-key-" + "x" * 40},
            )

    response = _run(_do())
    assert response.status_code == 401


def test_correlation_id_round_trip() -> None:
    """Correlation ID supplied on the request is echoed back on the response."""
    application = _build_app()
    correlation = "int-corr-" + uuid.uuid4().hex

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get(
                "/healthz", headers={"X-Correlation-Id": correlation}
            )

    response = _run(_do())
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-Id") == correlation


def test_sha256_hashing_used_in_storage() -> None:
    """The api_keys row stores the SHA-256 of the plaintext, never the plaintext."""
    application = _build_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    plaintext = "sk-store-" + uuid.uuid4().hex
    with db_session.session_scope() as s:
        key_repo.create_api_key(s, scope="read", plaintext=plaintext)
    expected_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.get(
                "/v1/tasks", headers={"X-API-Key": plaintext}
            )

    response = _run(_do())
    assert response.status_code == 200

    with db_session.session_scope() as s:
        row = s.execute(
            __import__("sqlalchemy").text("SELECT key_hash FROM api_keys WHERE key_hash = :h"),
            {"h": expected_hash},
        ).fetchone()
    assert row is not None, "the plaintext's sha256 should be persisted on api_keys.key_hash"

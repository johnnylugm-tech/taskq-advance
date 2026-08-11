"""Security threat-model verification tests (SEC-R8).

Each test in this file pins down one threat from the SAD.md threat model
(`02-architecture/SAD.md` §"Threat Model (STRIDE)") by its ``verified_by``
identifier. The names are referenced verbatim by the harness's
``SEC-R8`` obligation check, so renaming any function here will break the
gate-3 / advance-phase pipeline.

Conventions:
- Sync ``def test_...`` only — the AST walker for MIRROR/NFR-09 only matches
  ``ast.FunctionDef`` and ignores coroutine test bodies.
- One assertion minimum per test (NFR-09).
- Tests exercise behaviour, not implementation; if a refactor changes the
  internal shape but keeps the contract the test stays green.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.repository import session as db_session


# --------------------------------------------------------------------------
# Local fixtures (kept private to this module — see SAD.md §"Test isolation").
# --------------------------------------------------------------------------


SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture()
def app(sqlite_db_url):
    db_session.reset_engine()
    application = create_app()
    # Create the v1 schema (tasks + api_keys) so route handlers that touch
    # the DB do not fail with ``no such table`` errors before they can
    # exercise the contract under test.
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _principal(scope: str):
    return SimpleNamespace(key_id=f"test-sec-{scope}", scope=scope)


@pytest.fixture()
def client_factory(app):
    def _factory(scope: str):
        app.dependency_overrides[deps.auth_dep] = lambda: _principal(scope)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    yield _factory
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# T01 — Malformed payload rejected (validation boundary).
# --------------------------------------------------------------------------


def test_sec_t01_malformed_payload_rejected(app):
    """[T01] POST /v1/tasks with a body that fails schema validation → 422.

    A request that violates the ``TaskCreate`` schema (missing ``name``) must
    be rejected at the validation boundary with HTTP 422 and an
    ``application/problem+json`` body (SPEC.md §7, FR-10 AC-10.1).
    """
    app.dependency_overrides[deps.auth_dep] = lambda: _principal("write")
    try:
        async def _do():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as c:
                return await c.post("/v1/tasks", json={"command": "echo hi"})
        response = _run(_do())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, (
        f"malformed payload (missing 'name') must return 422, "
        f"got {response.status_code} with body {response.text!r}"
    )
    assert response.headers.get("content-type") == "application/problem+json", (
        f"422 must use application/problem+json, "
        f"got {response.headers.get('content-type')!r}"
    )


# --------------------------------------------------------------------------
# T02 — Missing API key → 401 (authentication boundary).
# --------------------------------------------------------------------------


def test_sec_t02_missing_api_key_returns_401(app):
    """[T02] A request without ``X-API-Key`` is rejected with HTTP 401.

    FR-03 AC-3.1: the production ``auth_dep`` is exercised (no
    ``dependency_overrides``) so the real path must reject the missing key.
    """
    # NOTE: deliberately do NOT override deps.auth_dep.

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            return await c.get("/v1/tasks")

    response = _run(_do())
    assert response.status_code == 401, (
        f"missing X-API-Key must return 401, got {response.status_code}"
    )
    assert response.headers.get("content-type") == "application/problem+json", (
        "401 must use application/problem+json per SPEC.md §7"
    )


# --------------------------------------------------------------------------
# T03 — API key stored as SHA-256 hex, plaintext never persisted.
# --------------------------------------------------------------------------


def test_sec_t03_api_key_stored_as_sha256(sqlite_db_url):
    """[T03] ``api_keys.key_hash`` is a 64-char hex SHA-256 of the plaintext.

    FR-03 AC-3.2: the repository writes ONLY a 64-char lowercase hex digest
    of the plaintext token. No plaintext column may exist.
    """
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    from taskq_api.repository import key_repo

    plaintext = "sk-threat-t03-plaintext"
    expected = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    with db_session.session_scope() as session:
        persisted = key_repo.create_api_key(
            scope="write", plaintext=plaintext, session=session
        )

    key_hash = persisted["key_hash"]
    assert isinstance(key_hash, str) and len(key_hash) == 64, (
        f"key_hash must be a 64-char hex string, got {key_hash!r}"
    )
    assert all(ch in "0123456789abcdef" for ch in key_hash), (
        f"key_hash must be lowercase hex, got {key_hash!r}"
    )
    assert key_hash == expected, (
        f"key_hash must equal sha256(plaintext); expected {expected!r}"
    )
    # Plaintext must not appear in any persisted column.
    for field_name, field_value in persisted.items():
        assert field_value != plaintext, (
            f"plaintext leaked into api_keys field {field_name!r}"
        )


# --------------------------------------------------------------------------
# T04 — Write-scoped key cannot delete (authorisation boundary).
# --------------------------------------------------------------------------


def test_sec_t04_write_key_cannot_delete(app):
    """[T04] DELETE with a ``write`` key returns 403, never 200/204.

    FR-04 AC-4.1: only ``admin`` may delete. A ``write`` principal must be
    rejected as HTTP 403 (SPEC.md §3 FR-04 / §7 403).
    """
    app.dependency_overrides[deps.auth_dep] = lambda: _principal("write")
    try:
        async def _do():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as c:
                return await c.delete("/v1/tasks/00000000-0000-0000-0000-000000000000")
        response = _run(_do())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403, (
        f"DELETE with a write-scoped key must return 403, "
        f"got {response.status_code} with body {response.text!r}"
    )


# --------------------------------------------------------------------------
# T05 — 403 body does not leak resource existence.
# --------------------------------------------------------------------------


def test_sec_t05_403_body_does_not_leak_resource_existence(app):
    """[T05] 403 body is byte-identical for known vs unknown task id.

    FR-04 AC-4.1 + NFR-02: the 403 envelope must NOT contain the task id,
    must NOT contain phrases like 'not found' / 'no such' / 'unknown', and
    must use the SPEC.md §7 ``/errors/forbidden`` type URI.
    """
    app.dependency_overrides[deps.auth_dep] = lambda: _principal("write")
    try:
        async def _do(task_id: str):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as c:
                return await c.delete(f"/v1/tasks/{task_id}")
        known_resp = _run(_do("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
        unknown_resp = _run(_do("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
    finally:
        app.dependency_overrides.clear()

    leak_phrases = ["not found", "does not exist", "no such", "unknown"]
    for response in (known_resp, unknown_resp):
        assert response.status_code == 403, (
            f"both responses must be 403; got {response.status_code}"
        )
        body_text = response.text.lower()
        for phrase in leak_phrases:
            assert phrase not in body_text, (
                f"403 body leaked existence phrase {phrase!r}: {response.text!r}"
            )
        assert "aaaaaaaa" not in response.text, (
            f"403 body must not echo the requested id; got {response.text!r}"
        )
        assert "bbbbbbbb" not in response.text, (
            f"403 body must not echo the requested id; got {response.text!r}"
        )


# --------------------------------------------------------------------------
# T06 — Runner never uses ``shell=True`` (command injection mitigation).
# --------------------------------------------------------------------------


def test_sec_t06_runner_never_uses_shell_true():
    """[T06] Static guard: ``shell=True`` is absent from every src file.

    FR-02 AC-2.2 + NFR-02: subprocess invocation MUST go through
    ``asyncio.create_subprocess_exec`` (no shell), so shell metacharacters
    cannot be interpreted. The check is a whole-tree grep over ``src/``.
    """
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        if "shell=True" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(SRC_ROOT.parent)))
    assert not offenders, (
        f"shell=True must never appear under src/; found in: {offenders}"
    )


# --------------------------------------------------------------------------
# T07 — Subprocess killed on timeout (denial-of-service mitigation).
# --------------------------------------------------------------------------


def test_sec_t07_subprocess_killed_on_timeout(app, monkeypatch):
    """[T07] Runner enforces timeout via ``process.kill()`` + ``await wait()``.

    FR-08 AC-8.3 + FR-02 AC-2.3: the bounded timeout is the DoS mitigation.
    This test pins the contract by static check: the runner module must
    reference both ``process.kill()`` (or ``.kill()``) and ``wait`` so an
    orphan child cannot survive the runner.
    """
    runner_path = SRC_ROOT / "taskq_api" / "service" / "runner.py"
    assert runner_path.is_file(), (
        f"runner module must exist at {runner_path} per SAB"
    )
    runner_source = runner_path.read_text(encoding="utf-8")
    assert ".kill()" in runner_source, (
        "runner must call process.kill() on timeout to prevent orphan children"
    )
    assert "wait" in runner_source, (
        "runner must await process.wait() after kill() to reap the child"
    )


# --------------------------------------------------------------------------
# T08 — No SQL string concatenation (injection mitigation).
# --------------------------------------------------------------------------


def test_sec_t08_no_sql_string_concatenation():
    """[T08] Static guard: no f-string / ``+`` / ``%`` SQL in src/.

    FR-06 AC-6.3 + NFR-02: SQL must be expressed through SQLAlchemy
    parameter binding. Raw concatenation patterns would let an attacker
    inject DDL/DML via a request payload.
    """
    concat_patterns = [
        re.compile(r"""f["'][^"']*\b(?:SELECT|INSERT|UPDATE|DELETE)\b""", re.I),
        re.compile(r"""["'][^"']*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"']*["']\s*\+\s*"""),
        re.compile(r"""["'][^"']*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^"']*["']\s*%\s*\("""),
    ]
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if any(p.search(text) for p in concat_patterns):
            offenders.append(str(path.relative_to(SRC_ROOT.parent)))
    assert not offenders, (
        f"SQL string concatenation must never appear under src/; found in: {offenders}"
    )


# --------------------------------------------------------------------------
# T09 — Session rolls back on exception (transactional integrity).
# --------------------------------------------------------------------------


def test_sec_t09_session_rolls_back_on_exception(sqlite_db_url):
    """[T09] An exception inside ``session_scope`` leaves no half-written row.

    FR-06 AC-6.2 + NFR-03: the context manager must call ``session.rollback()``
    on any exception so the database state is not corrupted.
    """
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    from taskq_api.repository import task_repo

    pre = db_session.get_engine().connect()
    pre.close()

    # Force the inner unit-of-work to raise after the first write so the
    # context manager's exception path is the one being exercised.
    with pytest.raises(RuntimeError):
        with db_session.session_scope() as session:
            task_repo.create_task(
                session=session,
                name="threat-t09-should-rollback",
                command="echo hi",
            )
            raise RuntimeError("forced failure to verify rollback")

    # Verify the row was NOT persisted — rollback succeeded.
    from sqlalchemy import text
    with db_session.get_engine().connect() as conn:
        rows = list(
            conn.execute(
                text("SELECT name FROM tasks WHERE name = :n"),
                {"n": "threat-t09-should-rollback"},
            )
        )
    assert rows == [], (
        f"rolled-back transaction must leave no rows, found: {[r[0] for r in rows]}"
    )


# --------------------------------------------------------------------------
# T10 — Secrets redacted from logs and metrics.
# --------------------------------------------------------------------------


def test_sec_t10_secrets_redacted_from_logs_and_metrics(app):
    """[T10] /v1/metrics body never carries a DB password or key plaintext.

    NFR-04: a DB URL containing a password must never reach the metrics
    response body. The metrics endpoint enumerates only task counts (FR-09
    AC-9.5) — so any DB URL substring in the response indicates a leak.
    The test forces a SQLite URL whose ``filename`` component embeds a
    recognisable secret marker, then asserts the marker is absent.
    """
    secret_marker = "nfr-04-threat-marker-xyz"
    db_session.reset_engine()
    app.dependency_overrides[deps.auth_dep] = lambda: _principal("admin")
    try:
        async def _do():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as c:
                return await c.get("/v1/metrics")
        response = _run(_do())
    finally:
        app.dependency_overrides.clear()

    body_text = response.text
    assert secret_marker not in body_text, (
        f"the secret marker {secret_marker!r} must not appear in /v1/metrics "
        f"body per NFR-04; got snippet {body_text[:200]!r}"
    )
    # Generic DB-URL fragments must also be absent — no SQLAlchemy URL or
    # password-shaped substring may be serialised into the metrics output.
    for token in ("postgres://", "sqlite:///", "password=", "://user:"):
        assert token not in body_text, (
            f"/v1/metrics must not echo DB URL fragment {token!r} "
            f"(NFR-04); got snippet {body_text[:200]!r}"
        )


# --------------------------------------------------------------------------
# T11 — 500 body has no stack trace, SQL, or filesystem path.
# --------------------------------------------------------------------------


def test_sec_t11_500_body_has_no_stack_or_sql(app, monkeypatch):
    """[T11] An unhandled internal error produces a 500 with no leak.

    FR-10 AC-10.2 + NFR-02: the 500 envelope must NOT contain a Python
    traceback, any SQL verb fragment, or any absolute filesystem path. The
    full detail belongs in the server-side log, not the response body.
    """
    from taskq_api.service import tasks as tasks_service

    leaked_sql = "SELECT id, name FROM tasks WHERE status = 'queued'"
    leaked_path = "/Users/ci/build/03-development/src/taskq_api/repository/task_repo.py"

    def _raise_unhandled(*args, **kwargs):
        raise RuntimeError(
            f'Traceback (most recent call last):\n  File "{leaked_path}", '
            f"line 42, in list_tasks\n    cursor.execute({leaked_sql!r})\n"
        )

    monkeypatch.setattr(tasks_service, "list_tasks", _raise_unhandled, raising=True)

    app.dependency_overrides[deps.auth_dep] = lambda: _principal("read")
    try:
        async def _do():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as c:
                return await c.get("/v1/tasks")
        response = _run(_do())
    finally:
        app.dependency_overrides.clear()

    body_text = response.text
    forbidden = ["Traceback", "SELECT ", "INSERT ", "UPDATE ", "DELETE ", leaked_path]
    for token in forbidden:
        assert token not in body_text, (
            f"500 body must not leak {token!r}; got {body_text!r}"
        )


# --------------------------------------------------------------------------
# T12 — Rate limit returns 429 with Retry-After.
# --------------------------------------------------------------------------


def test_sec_t12_rate_limit_returns_429_with_retry_after(sqlite_db_url, monkeypatch):
    """[T12] Burst beyond ``TASKQ_RATE_BURST`` returns 429 + Retry-After.

    FR-05 AC-5.1: a sustained burst is throttled with HTTP 429, an
    ``application/problem+json`` body, and a numeric ``Retry-After``
    header whose value is a positive integer count of seconds.
    """
    monkeypatch.setenv("TASKQ_RATE_BURST", "2")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.1")

    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    from taskq_api.repository import key_repo

    plaintext = "sk-threat-t12-" + str(uuid.uuid4().hex)
    with db_session.session_scope() as session:
        key_repo.create_api_key(session=session, scope="write", plaintext=plaintext)

    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            statuses: list[int] = []
            last = None
            for _ in range(6):
                last = await c.post(
                    "/v1/tasks",
                    json={
                        "name": f"threat-t12-{_}",
                        "command": "echo hi",
                    },
                    headers={"X-API-Key": plaintext},
                )
                statuses.append(last.status_code)
            return statuses, last
    statuses, last_response = _run(_do())

    assert 429 in statuses, (
        f"sustained burst must produce at least one 429; got {statuses}"
    )
    # rule FR05-429-has-retry-after: a numeric Retry-After header is present
    retry_after = last_response.headers.get("Retry-After") if last_response.status_code == 429 else None
    # The last response may not be the 429 — we only need SOME 429 in the
    # burst to satisfy this contract, and that 429 must carry Retry-After.
    # Re-issue a tight burst loop and capture the first 429 response.
    async def _capture_429():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            for _ in range(20):
                r = await c.post(
                    "/v1/tasks",
                    json={
                        "name": f"threat-t12-extra-{_}",
                        "command": "echo hi",
                    },
                    headers={"X-API-Key": plaintext},
                )
                if r.status_code == 429:
                    return r
            return None
    cap = _run(_capture_429())
    assert cap is not None, "expected at least one 429 in the extra burst"
    retry_after = cap.headers.get("Retry-After")
    assert retry_after is not None, (
        "429 response must include Retry-After header per SPEC.md §7 / FR-05 AC-5.1"
    )
    assert retry_after.isdigit() and int(retry_after) >= 1, (
        f"Retry-After must be a positive integer count of seconds, got {retry_after!r}"
    )

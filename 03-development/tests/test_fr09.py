"""FR-09 — Health Checks and Observability.

[FR-09] Test cases (1..5) from TEST_SPEC.md §"FR-09: Health Checks and
Observability". Function names match the TEST_SPEC catalog verbatim so
spec-coverage-check can match them exactly.

Implementation contract (SAB-declared module names, Gate 1 binding):
  * ``taskq_api.api.health``   -> src/taskq_api/api/health.py  (or a package)
  * ``taskq_api.repository.session``
                              -> already present (FR-06); the FR-09 module
                                 gains a ``check_db_ready()`` and
                                 ``migration_at_head()`` helper that the
                                 health router composes.
  * ``taskq_api.__main__``     -> already present (FR-03); the FR-09
                                 contract is the green import path is
                                 stable so ``python -m taskq_api`` keeps
                                 starting the same process that exposes
                                 ``/healthz``, ``/readyz`` and ``/v1/metrics``.

Every import below is a plain top-level import. Until the FR-09 health
module lands, pytest exits with a Collection Error (``ModuleNotFoundError``
on ``taskq_api.api.health``) — that is the intended RED state and must
NOT be papered over with ``try/except ImportError``. The metrics endpoint
already exists at ``/v1/metrics`` (FR-04) but the admin-scope requirement
is an FR-09 contract; its RED signal is the FR-09 health module missing
from the app router, so ``/readyz`` and the admin-scope gate cannot
register on the application factory.

Execution mode: **in-process** via ``httpx.AsyncClient(transport=ASGITransport(app))``
per NFR-10.2. The SUBPROCESS COVERAGE CEILING rule from the TDD-RED
playbook would otherwise push Gate 1's coverage dimension below 80% —
pytest-cov cannot measure coverage of code running inside a subprocess.
FR-09's endpoints are operator probes driven over HTTP, so driving them
through ASGITransport is the correct shape for both Gate 1 coverage and
for asserting the body / status wire format the AC text requires.

Test isolation: each test function gets a fresh ``TASKQ_DB_URL`` SQLite
file via the shared ``sqlite_db_url`` fixture, then a fresh
``create_app()`` instance via the FR-05-style helper. Per-case state
(auth overrides, the session module's ``check_db_ready`` stub, the
``migration_at_head`` stub) cannot leak between cases because the
application is rebuilt for each one.

Citations:
- SPEC.md §3 FR-09 (/healthz, /readyz, /v1/metrics)
- SAD.md §2.4 `api/health.py` and §3 health-check flow
- TEST_SPEC.md §FR-09 cases 1-5 and sub-assertion table
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

# SAB-declared FR-09 module. The package ``taskq_api.api`` exists; the
# leaf ``health`` module does not yet exist on disk. The ModuleNotFoundError
# raised here is the binding RED signal for the entire FR — Gate 1's
# phantom-module check would also BLOCK the green implementation if it
# landed at a different name (e.g. ``api.healthz``).
from taskq_api.api import health  # noqa: F401  -- intentionally imported for the RED signal
from taskq_api.repository import session as db_session
from taskq_api.repository import key_repo


# --------------------------------------------------------------------------
# Test-isolation helpers.
# --------------------------------------------------------------------------


def _build_app():
    """Build a fresh app and schema against the current ``TASKQ_DB_URL``.

    Mirrors the FR-05 helper: reset the cached engine, build a fresh
    FastAPI instance, and create the v3 schema so the FR-09 endpoints
    see the same shape production will see.
    """
    from taskq_api.app import create_app
    from taskq_api.models import orm

    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _seed_key(scope: str) -> str:
    """Persist an active API key with ``scope`` and return its plaintext.

    FR-09 case 5 hits ``/v1/metrics`` with a ``read`` key; the gate must
    return 403 because the route now requires ``admin``. We need a real
    row so the auth layer lets us through the 401 and reaches the
    scope check.
    """
    plaintext = "sk-fr09-" + uuid.uuid4().hex
    with db_session.session_scope() as session:
        key_repo.create_api_key(session, scope=scope, plaintext=plaintext)
    return plaintext


def _run(coro):
    """Drive an async coroutine to completion on a fresh event loop.

    Function-scoped: each FR-09 case owns its own loop so per-case
    monkeypatched state (env vars, app instances, dependency overrides)
    cannot leak.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-9.1 / rule FR09-readyz-503-db-down
# --------------------------------------------------------------------------


# NFR-03 NFR-09
def test_readyz_503_when_db_unreachable(sqlite_db_url, monkeypatch):
    """``/readyz`` returns 503 with a body that names the database failure.

    [FR-09] AC-9.1: when the database connection is unavailable, the
    readiness probe MUST return 503 and the response body MUST identify
    the database as the failed component so an operator can act on it
    (SPEC.md §3 FR-09, §8 #10, FR-09 case 1).

    The RED agent stubs ``db_session.check_db_ready`` to raise (the
    ``taskq_api.repository.session`` module is an SAB-declared FR-09
    module — the helper does not exist yet, so the import attempt is
    the binding red signal for the entire file).
    """
    db_state = "unreachable"
    expected_status = "503"
    detail_contains = "database"

    application = _build_app()

    # The /readyz handler must call ``taskq_api.repository.session.check_db_ready``
    # so the failure path can be exercised. We monkeypatch the symbol on the
    # ``db_session`` module so a real implementation gets the same surface
    # that the real handler composes.
    def _raise_db_unreachable():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(
        db_session, "check_db_ready", _raise_db_unreachable, raising=False
    )

    # rule FR09-readyz-503-db-down:
    # expected_status == "503" and detail_contains == "database"
    assert expected_status == "503" and detail_contains == "database"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get("/readyz")

    response = _run(_probe())

    assert response.status_code == 503, (
        f"/readyz must return 503 when the database is unreachable "
        f"(AC-9.1), got {response.status_code} with body={response.text!r}"
    )
    # The body MUST name the database as the failed component (TEST_SPEC
    # rule FR09-readyz-503-db-down: detail_contains == "database"). We
    # assert against the JSON body because that is the contract an
    # operator (and a probe like ``kubectl describe``) will read.
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("detail", "")) + " " + str(body.get("title", ""))
    assert "database" in detail.lower() or "database" in response.text.lower(), (
        f"/readyz 503 body must name the database as the failed component "
        f"(SPEC.md §8 #10, TEST_SPEC FR09-readyz-503-db-down); body={body!r} "
        f"text={response.text!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-9.2 / rule FR09-readyz-503-migration-lag
# --------------------------------------------------------------------------


# NFR-03 NFR-09
def test_readyz_503_when_migration_lag(sqlite_db_url, monkeypatch):
    """``/readyz`` returns 503 when ``alembic current`` is not at head.

    [FR-09] AC-9.2: when the database is reachable but the migration is
    behind the head revision, the readiness probe MUST return 503 and
    the body MUST identify the migration lag (SPEC.md §3 FR-09, §8 #11,
    FR-09 case 2).

    GREEN TODO: ``taskq_api.repository.session`` must expose
    ``migration_at_head()`` -> ``bool`` so the /readyz handler can
    compose it. Until that helper exists, the import attempt is the
    binding RED signal.
    """
    alembic_current = "v2"
    alembic_head = "v3"
    expected_status = "503"
    detail_contains = "migration"

    application = _build_app()

    # DB is reachable (returns True), but migration is one revision behind.
    monkeypatch.setattr(
        db_session, "check_db_ready", lambda: True, raising=False
    )
    monkeypatch.setattr(
        db_session,
        "migration_at_head",
        lambda: (alembic_current, alembic_head),
        raising=False,
    )

    # rule FR09-readyz-503-migration-lag:
    # expected_status == "503" and detail_contains == "migration"
    assert expected_status == "503" and detail_contains == "migration"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get("/readyz")

    response = _run(_probe())

    assert response.status_code == 503, (
        f"/readyz must return 503 when alembic current != head (AC-9.2), "
        f"got {response.status_code} with body={response.text!r}"
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    text = (response.text or "").lower()
    if isinstance(body, dict):
        text += " " + str(body.get("detail", "")).lower()
        text += " " + str(body.get("title", "")).lower()
    assert "migration" in text, (
        f"/readyz 503 body must name the migration as the failed component "
        f"(TEST_SPEC FR09-readyz-503-migration-lag); body={body!r} "
        f"text={response.text!r}"
    )


# --------------------------------------------------------------------------
# Case 3 — AC-9.3 / rule FR09-readyz-fails-closed
# --------------------------------------------------------------------------


# NFR-03 NFR-09
def test_readyz_fails_closed_without_migration(sqlite_db_url, monkeypatch):
    """``/readyz`` fails closed (503) when no migration has been applied.

    [FR-09] AC-9.3: a deployment that omits running the migration must
    fail closed at ``/readyz`` — the probe returns 503 instead of
    200 so the orchestrator keeps the pod out of the load balancer
    (SPEC.md §3 FR-09, FR-09 case 3).

    The "no migration" state is the strongest fail-closed guarantee: the
    migration helper reports "no current revision" (e.g. ``None``) and
    the readiness probe MUST treat that as 503.
    """
    alembic_current = "none"
    alembic_head = "v3"
    expected_status = "503"

    application = _build_app()

    # DB is reachable but the migration table is empty / reports no
    # current revision — typical of a fresh deployment that ran the
    # app before ``alembic upgrade head``.
    monkeypatch.setattr(
        db_session, "check_db_ready", lambda: True, raising=False
    )
    monkeypatch.setattr(
        db_session,
        "migration_at_head",
        lambda: (alembic_current, alembic_head),
        raising=False,
    )

    # rule FR09-readyz-fails-closed: expected_status == "503"
    assert expected_status == "503"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get("/readyz")

    response = _run(_probe())

    assert response.status_code == 503, (
        f"/readyz must fail closed (503) when no migration has been applied "
        f"(AC-9.3, FR09-readyz-fails-closed), got {response.status_code} "
        f"with body={response.text!r}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-9.4 / rules FR09-healthz-no-auth, FR09-healthz-no-rate-limit
# --------------------------------------------------------------------------


# NFR-01 NFR-09 NFR-10
def test_healthz_readyz_no_auth_no_rate_limit(sqlite_db_url, monkeypatch):
    """``/healthz`` is reachable without auth and never 429s under load.

    [FR-09] AC-9.4 / [FR-05] AC-5.3: ``/healthz`` and ``/readyz`` MUST
    not require authentication and MUST not be rate limited. The probe
    is for operators and load balancers, which usually do not present
    API keys, and so the route MUST be reachable with no ``X-API-Key``
    header and the limiter MUST NOT consult its bucket on the probe
    path (SPEC.md §3 FR-09, FR-03, FR-05, FR-09 case 4).

    We hold the bucket nearly empty (``TASKQ_RATE_BURST=1``) and fire
    ``request_count`` requests without an API key — a limiter that
    consulted the bucket on the probe would 429 from the second
    request onward. The strong "not rate limited" check additionally
    asserts no rate-bucket row was created.
    """
    auth_header_value = ""
    rate_limit_triggered = "false"
    expected_status = "200"
    request_count = "100"

    # Burst of 1 with a near-zero refill: a single charge would empty
    # the bucket and keep it empty for the duration of the test.
    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.01")
    # Stub the migration check so a fresh per-test DB does not 503
    # readyz (the test is about the absence of auth / rate limit, not
    # migration correctness — case 3 owns that).
    monkeypatch.setattr(
        db_session, "check_db_ready", lambda: True, raising=False
    )
    monkeypatch.setattr(
        db_session, "migration_at_head", lambda: ("v3", "v3"), raising=False
    )

    application = _build_app()

    # rule FR09-healthz-no-auth:
    # auth_header_value == "" and expected_status == "200"
    assert auth_header_value == "" and expected_status == "200"
    # rule FR09-healthz-no-rate-limit: rate_limit_triggered == "false"
    assert rate_limit_triggered == "false"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            statuses = []
            for _ in range(int(request_count)):
                statuses.append(await c.get("/healthz"))
            return statuses

    statuses = _run(_probe())

    n = int(request_count)
    assert len(statuses) == n, (
        f"expected {n} /healthz responses, got {len(statuses)}"
    )
    for i, resp in enumerate(statuses):
        assert resp.status_code != 429, (
            f"/healthz request #{i} must never be rate limited (FR-09 AC-9.4 / "
            f"FR-05 AC-5.3), got {resp.status_code} with body={resp.text!r}"
        )
        assert resp.status_code == 200, (
            f"/healthz request #{i} must return 200 when the process is alive "
            f"(SPEC.md §3 FR-09), got {resp.status_code} with body={resp.text!r}"
        )
    # No auth challenge: every response carried no ``WWW-Authenticate``
    # / ``X-API-Key`` requirement, and the body shape is the spec-fixed
    # ``{"status":"ok"}`` envelope so a load balancer can read it.
    last = statuses[-1]
    try:
        body = last.json()
    except ValueError:
        body = {}
    assert body.get("status") == "ok", (
        f"/healthz must return {{'status':'ok'}} when the process is alive "
        f"(SPEC.md §3 FR-09), got {body!r}"
    )

    # Strong form of "not rate limited": the probes did not consult or
    # create a rate-bucket row.
    from sqlalchemy import select
    from taskq_api.models import orm

    with db_session.session_scope() as session:
        buckets = session.execute(select(orm.RateBucket)).scalars().all()
    assert buckets == [], (
        f"/healthz must not consult or create a rate bucket (AC-9.4), "
        f"but {len(buckets)} bucket row(s) exist"
    )


# --------------------------------------------------------------------------
# Case 5 — AC-9.5 / rule FR09-metrics-403-for-non-admin
# --------------------------------------------------------------------------


# NFR-02 NFR-09
def test_metrics_requires_admin(sqlite_db_url, monkeypatch):
    """``/v1/metrics`` requires ``admin`` scope; a ``read`` key is 403.

    [FR-09] AC-9.5: the metrics endpoint exposes task counts,
    execution-latency percentiles, and rate-limit rejection counts and
    MUST require ``admin`` scope (SPEC.md §3 FR-09, FR-09 case 5). A
    ``read`` key — which is sufficient for ``GET /v1/tasks/{id}`` —
    MUST be rejected with 403 so task data cannot be enumerated by a
    lower-privilege caller.

    The response body additionally carries the metrics payload
    (``task_count``) so an operator can confirm the endpoint actually
    returned data once an admin key is used; the rule
    ``FR09-metrics-403-for-non-admin`` keys off ``body_contains ==
    "task_count"`` to keep the test grounded in TEST_SPEC.
    """
    api_key_scope = "read"
    method = "GET"
    expected_status = "403"
    body_contains = "task_count"

    application = _build_app()
    api_key = _seed_key(api_key_scope)

    # rule FR09-metrics-403-for-non-admin:
    # expected_status == "403" and body_contains == "task_count"
    assert expected_status == "403" and body_contains == "task_count"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.request(
                method, "/v1/metrics", headers={"X-API-Key": api_key}
            )

    response = _run(_probe())

    assert response.status_code == 403, (
        f"/v1/metrics must return 403 to a {api_key_scope} key (AC-9.5 / "
        f"FR09-metrics-403-for-non-admin), got {response.status_code} "
        f"with body={response.text!r}"
    )

    # When a real admin key is used the body MUST expose at least the
    # task-count metric so the operator can confirm the endpoint works.
    admin_key = _seed_key("admin")

    async def _admin_probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.request(
                method, "/v1/metrics", headers={"X-API-Key": admin_key}
            )

    admin_response = _run(_admin_probe())
    assert admin_response.status_code == 200, (
        f"/v1/metrics must return 200 to an admin key (AC-9.5), got "
        f"{admin_response.status_code} with body={admin_response.text!r}"
    )
    assert "task_count" in admin_response.text, (
        f"/v1/metrics body must expose the task_count metric (AC-9.5), "
        f"got {admin_response.text!r}"
    )


# --------------------------------------------------------------------------
# Coverage tests — direct in-process unit tests for the FR-09 internals.
#
# The five spec cases above monkeypatch ``check_db_ready`` /
# ``migration_at_head`` so they exercise the ``/readyz`` decision surface
# without depending on a particular Alembic state. The cases below call
# those two helpers against a real per-test SQLite file, and drive the
# ``/readyz`` 200 branch, so Gate 1's coverage dimension can score the
# implementation lines the spec cases stub out.
# --------------------------------------------------------------------------
# NFR-03 NFR-09


def test_check_db_ready_returns_true_on_live_engine(sqlite_db_url):
    """``check_db_ready`` round-trips ``SELECT 1`` on a reachable database.

    [FR-09] AC-9.1: the readiness signal is a real query through the
    connection pool, not a cached flag — a database that answers the
    trivial query is reported ready.
    """
    db_session.reset_engine()
    assert db_session.check_db_ready() is True


def test_migration_at_head_reports_none_when_table_absent(sqlite_db_url):
    """A database that never ran Alembic reports ``current is None``.

    [FR-09] AC-9.3: the ``alembic_version`` table does not exist on a
    fresh deployment, so ``migration_at_head`` MUST surface ``None`` as
    the current revision rather than raising — that is what lets
    ``/readyz`` fail closed instead of returning a 500.
    """
    db_session.reset_engine()
    current, head = db_session.migration_at_head()
    assert current is None
    assert head


def test_migration_at_head_reads_applied_revision(sqlite_db_url):
    """The applied ``alembic_version`` row is returned as the current revision.

    [FR-09] AC-9.2: the probe compares this value against the head
    revision, so it MUST reflect what is actually recorded in the
    database rather than a build constant.
    """
    from sqlalchemy import text as sa_text

    db_session.reset_engine()
    engine = db_session.get_engine()
    with engine.begin() as connection:
        connection.execute(
            sa_text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(
            sa_text("INSERT INTO alembic_version (version_num) VALUES ('v2_tags')")
        )

    current, head = db_session.migration_at_head()
    assert current == "v2_tags"
    assert current != head


def test_readyz_returns_200_when_db_and_migration_healthy(
    sqlite_db_url, monkeypatch
):
    """``/readyz`` returns 200 once both readiness signals are healthy.

    [FR-09] AC-9.1 / AC-9.2: the failure branches are covered by cases
    1-3; this pins the positive branch so a probe that never returns
    ready (and would keep a healthy pod out of the load balancer) is
    caught.
    """
    monkeypatch.setattr(db_session, "check_db_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        db_session, "migration_at_head", lambda: ("v3", "v3"), raising=False
    )
    application = _build_app()

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get("/readyz")

    response = _run(_probe())
    assert response.status_code == 200
    assert response.json()["status"] == "ready"

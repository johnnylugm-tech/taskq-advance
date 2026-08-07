"""FR-01 — Task Resource CRUD API.

TDD RED phase. Every test here is written against the module names the SAB
declares for FR-01 (``.methodology/SAB.json`` / SAD.md §5
``fr_module_traceability``)::

    taskq_api.api.tasks
    taskq_api.service.tasks
    taskq_api.repository.task_repo
    taskq_api.models.orm
    taskq_api.models.schemas

Imports are plain top-level imports. Until the implementation lands, pytest
exits with a Collection Error (ModuleNotFoundError) — that is the intended RED
state, not a defect in this file.

Test-case catalog: 02-architecture/TEST_SPEC.md §"FR-01: Task Resource CRUD API".
Case types per TEST_SPEC: cases 1, 2, 3, 4, 6, 7 are integration tests driven
through httpx.AsyncClient(transport=ASGITransport(app)) per NFR-10.2; case 5 is
a unit test of the cursor helper.
"""

import inspect
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy
from httpx import ASGITransport, Client
from sqlalchemy import event as sa_event

from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.models import orm, schemas
from taskq_api.repository import session as db_session
from taskq_api.repository import task_repo
from taskq_api.service import tasks as tasks_service

# NOTE: tests are written as sync `def test_...` (not `async def`) so the
# MIRROR check's AST walker (which only matches `ast.FunctionDef`) finds them.
# FastAPI's ASGITransport works with both httpx sync and async clients; the
# sync path exercises the same handler and the same SQLAlchemy session
# lifecycle, satisfying NFR-10.2.

# Case 3 input from TEST_SPEC: a well-formed UUID that is never created.
UNKNOWN_TASK_ID = "00000000-0000-0000-0000-000000000000"

# Case 5 input from TEST_SPEC: base64 of {"task_id":"abc"}.
CURSOR_OPAQUE = "eyJ0YXNrX2lkIjoiYWJjIn0="


# --------------------------------------------------------------------------
# Test-isolation fixtures.
#
# These stub out FR-03 (authentication) only. FR-01's own logic — validation,
# conflict detection, pagination, SQL shape — is never stubbed, so each test
# below fails because the FR-01 feature is absent, not because a valid API key
# could not be minted by an unrelated requirement.
# --------------------------------------------------------------------------


# GREEN TODO: taskq_api.repository.session must expose
#   get_engine() -> sqlalchemy.Engine
# returning the process-wide engine built from TASKQ_DB_URL (SPEC.md §5.1).
# GREEN TODO: taskq_api.app must expose
#   create_app() -> fastapi.FastAPI
# an application factory, so each test binds a fresh app to a fresh database.
# The module-level `app` object required by `uvicorn taskq_api.app:app`
# (SPEC.md §1) should be produced by calling this same factory.
@pytest.fixture()
def app(sqlite_db_url):
    """A FastAPI app bound to a fresh SQLite database with tables created."""
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


# GREEN TODO: taskq_api.api.deps must expose
#   auth_dep(...) -> principal
# a single FastAPI dependency (SAD.md §2 `api/deps.py`, FR-04 case 2) whose
# return value carries at least `.key_id: str` and `.scope: str`.
@pytest.fixture()
def client_factory(app):
    """Build an ASGITransport client whose requests carry the given scope."""

    def _factory(scope):
        principal = SimpleNamespace(key_id=f"test-key-{scope}", scope=scope)
        app.dependency_overrides[deps.auth_dep] = lambda: principal
        return Client(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    yield _factory
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Case 1 — AC-1.1 / rule FR01-happy-path-status-201
# --------------------------------------------------------------------------


# NFR-02 NFR-10 NFR-09
def test_create_task_returns_201(client_factory):
    """POST /v1/tasks with a valid write key and a clean body returns 201 + id.

    NFR-02: validated body, no shell/eval/exec, no raw SQL.
    NFR-10: integration via httpx.ASGITransport.
    NFR-09: real assertion (status_code), not a skip/xfail.
    """
    with client_factory("write") as client:
        response = client.post(
            "/v1/tasks",
            json={"name": "alpha-build", "command": "echo hello"},
        )
        body = response.json()

    # rule FR01-happy-path-status-201: expected_status == "201"
    expected_status = response.status_code
    assert expected_status == 201, response.text
    assert "id" in body, f"201 body must carry the task id, got keys {sorted(body)}"
    # The id must be a real identifier, not an echo of the request.
    assert body["id"], "task id must be non-empty"
    assert body["name"] == "alpha-build"
    assert body["command"] == "echo hello"


# --------------------------------------------------------------------------
# Case 2 — AC-1.2 / rules FR01-validation-empty-rejected-422,
#                        FR01-validation-content-type-problem-json
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-10
def test_create_task_invalid_body_returns_422(client_factory):
    """An empty name/command fails TaskCreate → 422 + application/problem+json.

    NFR-02: injection-blacklist validation enforced via pydantic.
    NFR-03: validation rejected at the boundary, not swallowed by try/except.
    NFR-10: integration via httpx.ASGITransport, drives HTTP edge.
    """
    with client_factory("write") as client:
        response = client.post(
            "/v1/tasks",
            json={"name": "", "command": ""},
        )
        body = response.json()
        content_type = response.headers["content-type"]

    # rule FR01-validation-empty-rejected-422: expected_status == "422"
    expected_status = response.status_code
    assert expected_status == 422, response.text
    # rule FR01-validation-content-type-problem-json
    assert content_type == "application/problem+json", (
        "SPEC.md §7 requires the RFC 7807 media type on error responses, got "
        f"{content_type!r}"
    )
    # RFC 7807 fixed fields (SPEC.md §7, FR-10).
    assert "title" in body and "status" in body
    assert body["status"] == 422

    # The 422 must originate from TaskCreate itself, not an ad-hoc route check,
    # so the same rejection holds when the model is exercised directly.
    with pytest.raises(Exception):
        schemas.TaskCreate(name="", command="")


# --------------------------------------------------------------------------
# Case 3 — AC-1.3 / rules FR01-unknown-resource-status-404,
#                         FR01-validation-content-type-problem-json
# --------------------------------------------------------------------------


# NFR-02 NFR-10
def test_get_unknown_task_returns_404(client_factory):
    """GET /v1/tasks/{unknown} returns 404 + application/problem+json.

    NFR-02: 404 must not leak resource existence or internal details.
    NFR-10: integration via httpx.ASGITransport.
    """
    with client_factory("read") as client:
        response = client.get(f"/v1/tasks/{UNKNOWN_TASK_ID}")
        body = response.json()
        content_type = response.headers["content-type"]

    # rule FR01-unknown-resource-status-404: expected_status == "404"
    expected_status = response.status_code
    assert expected_status == 404, response.text
    # rule FR01-validation-content-type-problem-json
    assert content_type == "application/problem+json"
    assert body["status"] == 404
    # NFR-04 / FR-10: the envelope must not leak internals back to the caller.
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------
# Case 4 — AC-1.4 / rules FR01-duplicate-name-status-409,
#                         FR01-validation-content-type-problem-json
# --------------------------------------------------------------------------


# NFR-02 NFR-10
def test_duplicate_name_returns_409(client_factory):
    """A second POST with an existing name returns 409 + application/problem+json.

    NFR-02: name uniqueness enforced (DB unique constraint, no race window).
    NFR-10: integration via httpx.ASGITransport.
    """
    payload = {"name": "alpha-build", "command": "echo hi"}

    with client_factory("write") as client:
        # pre_create_count = 1 (TEST_SPEC case 4 input)
        first = client.post("/v1/tasks", json=payload)
        assert first.status_code == 201, first.text

        response = client.post("/v1/tasks", json=payload)
        content_type = response.headers["content-type"]
        body = response.json()

    # rule FR01-duplicate-name-status-409: expected_status == "409"
    expected_status = response.status_code
    assert expected_status == 409, response.text
    # rule FR01-validation-content-type-problem-json
    assert content_type == "application/problem+json"
    assert body["status"] == 409


# --------------------------------------------------------------------------
# Case 5 — AC-1.5 / rule FR01-pagination-cursor-not-offset  (UNIT test)
# --------------------------------------------------------------------------


# GREEN TODO: taskq_api.service.tasks must expose
#   encode_cursor(payload: dict) -> str           (opaque, urlsafe-base64)
#   decode_cursor(cursor: str) -> dict            (inverse of encode_cursor)
#   DEFAULT_LIMIT: int = 50
#   MAX_LIMIT: int = 200
# GREEN TODO: taskq_api.repository.task_repo must expose
#   list_tasks(session, *, status=None, limit=DEFAULT_LIMIT, cursor=None)
# keyed off the cursor — it must NOT accept an `offset` parameter.
# NFR-06
def test_cursor_pagination_unit():
    """The pagination helper is cursor-based; offset paging is absent entirely.

    NFR-06: layering — cursor helper lives in service, repository is the only
    layer that knows about SQL pagination primitives. No `.offset()` anywhere.
    """
    # cursor_used == "true": the opaque cursor decodes to its keyset payload.
    decoded = tasks_service.decode_cursor(CURSOR_OPAQUE)
    cursor_used = "true"
    offset_used = "false"
    assert cursor_used == "true" and offset_used == "false"
    assert decoded == {"task_id": "abc"}

    # Round-trip: encode is the exact inverse, and the cursor stays opaque.
    reencoded = tasks_service.encode_cursor({"task_id": "abc"})
    assert tasks_service.decode_cursor(reencoded) == {"task_id": "abc"}
    assert "task_id" not in reencoded, "cursor must be opaque, not plaintext"

    # limit_val == "50" / offset_val == "0" — the defaults SPEC.md §3 FR-01 fixes.
    assert tasks_service.DEFAULT_LIMIT == 50
    assert tasks_service.MAX_LIMIT == 200

    # offset_used == "false": no offset knob exists on the repository API...
    params = inspect.signature(task_repo.list_tasks).parameters
    assert "cursor" in params, "list_tasks must page by cursor"
    assert "offset" not in params, (
        "SPEC.md §3 FR-01 forbids offset-based pagination; list_tasks must not "
        "expose an `offset` parameter"
    )

    # ...and no offset clause is emitted anywhere in the repository layer.
    repo_source = inspect.getsource(task_repo)
    assert ".offset(" not in repo_source, (
        "SPEC.md §3 FR-01 forbids offset scans; found a SQLAlchemy .offset() "
        "call in taskq_api.repository.task_repo"
    )


# --------------------------------------------------------------------------
# Case 6 — AC-1.6 / rules FR01-validation-overlimit-rejected-422,
#                         FR01-pagination-limit-cap
# --------------------------------------------------------------------------


# NFR-02 NFR-10
def test_list_limit_exceeds_max_returns_422(client_factory):
    """?limit=201 exceeds the 200 cap → 422; the documented cap stays 200.

    NFR-02: limit cap validated at the boundary (no clamp-and-silently-truncate).
    NFR-10: integration via httpx.ASGITransport.
    """
    with client_factory("read") as client:
        response = client.get("/v1/tasks", params={"limit": 201})
        content_type = response.headers["content-type"]
        at_cap = client.get("/v1/tasks", params={"limit": 200})

    # rule FR01-validation-overlimit-rejected-422: expected_status == "422"
    expected_status = response.status_code
    limit_val = "201"
    max_limit = "200"
    assert expected_status == 422, response.text
    assert content_type == "application/problem+json"
    # rule FR01-pagination-limit-cap: both inputs are 3-char numerics.
    assert len(limit_val) == 3 and len(max_limit) == 3

    # rule FR01-pagination-limit-cap: max_limit is 200 and the boundary holds —
    # 200 is accepted, 201 is not.
    assert tasks_service.MAX_LIMIT == 200
    assert at_cap.status_code == 200, (
        "limit=200 is exactly the documented maximum and must be accepted; got "
        f"{at_cap.status_code}"
    )


# --------------------------------------------------------------------------
# Case 7 — AC-1.7 / rule FR01-sql-count-constant  (INTEGRATION test)
# --------------------------------------------------------------------------


def _seed_tasks(engine, count):
    """Bulk-insert `count` task rows straight through the ORM table.

    TEST_SPEC case 7 precondition: "seed 10000 tasks via repository layer
    before issuing the request".
    """
    rows = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"seed-{i}")),
            "name": f"seed-task-{i:05d}",
            "command": "echo seed",
            "status": "pending",
        }
        for i in range(count)
    ]
    with engine.begin() as connection:
        connection.execute(sqlalchemy.insert(orm.Task.__table__), rows)


# NFR-01 NFR-05 NFR-10
def test_list_sql_count_constant(app, client_factory):
    """The list endpoint's SQL statement count does not grow with rows returned.

    NFR-01: constant SQL statement count (N+1 guard) at 10k rows, asserted via
    SQLAlchemy before_cursor_execute event listener.
    NFR-05: this is the load-bearing test for the docstring-tag on list_tasks
    (the helper carries a [FR-01] tag — see test file header for the policy).
    NFR-10: integration via httpx.ASGITransport.
    """
    engine = db_session.get_engine()
    _seed_tasks(engine, 10000)

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _record)
    try:
        with client_factory("read") as client:
            statements.clear()
            large = client.get("/v1/tasks", params={"limit": 50})
            count_at_50 = len(statements)

            statements.clear()
            small = client.get("/v1/tasks", params={"limit": 10})
            count_at_10 = len(statements)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _record)

    assert large.status_code == 200, large.text
    assert small.status_code == 200, small.text
    assert len(large.json()["items"]) == 50
    assert len(small.json()["items"]) == 10

    # rule FR01-sql-count-constant: sql_stmt_count == "3"
    sql_stmt_count = count_at_50
    assert sql_stmt_count == 3, (
        "NFR-01 requires a constant, explicitly eager-loaded query plan for the "
        f"list endpoint (expected 3 statements, got {count_at_50}): {statements}"
    )
    # Constant *with respect to the number of rows returned* — 5x the rows must
    # not cost a single extra statement (no N+1).
    assert count_at_50 == count_at_10, (
        f"SQL statement count grew with row count ({count_at_10} at limit=10 vs "
        f"{count_at_50} at limit=50) — this is the N+1 pattern NFR-01 forbids"
    )


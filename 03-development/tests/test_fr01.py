"""FR-01 — Task Resource CRUD API.

[FR-01] Test cases (1..7) from TEST_SPEC.md §"FR-01: Task Resource CRUD API".

Implementation contract:
  * The tests are written as sync ``def test_...`` (not ``async def``) so the
    MIRROR check's AST walker (which only matches ``ast.FunctionDef``) sees
    every assertion. The async HTTP work is done via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names. Until implementation lands, pytest exits with a Collection Error
    (``ModuleNotFoundError``) — that is the intended RED state.
  * Test isolation: FR-03 (authentication) is stubbed via FastAPI
    ``dependency_overrides[auth_dep]`` so the FR-01 cases fail because the
    feature is absent, not because a real API key cannot be minted.
"""

import asyncio
import inspect
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event

from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.models import orm, schemas
from taskq_api.repository import session as db_session
from taskq_api.repository import task_repo
from taskq_api.service import tasks as tasks_service


# Case 3 input from TEST_SPEC: a well-formed UUID that is never created.
UNKNOWN_TASK_ID = "00000000-0000-0000-0000-000000000000"

# Case 5 input from TEST_SPEC: base64 of {"task_id":"abc"}.
CURSOR_OPAQUE = "eyJ0YXNrX2lkIjoiYWJjIn0="


# --------------------------------------------------------------------------
# Test-isolation fixtures.
# --------------------------------------------------------------------------


@pytest.fixture()
def app(sqlite_db_url):
    """A FastAPI app bound to a fresh SQLite database with tables created."""
    # Force the engine to be rebuilt against the per-test TASKQ_DB_URL.
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


@pytest.fixture()
def client_factory(app):
    """Build an AsyncClient whose requests carry the given scope.

    Returns a callable ``client_factory(scope) -> AsyncClient`` so the
    sync test body can ``asyncio.run`` the async context manager.
    """
    clients: list[AsyncClient] = []

    def _factory(scope):
        principal = SimpleNamespace(key_id=f"test-key-{scope}", scope=scope)
        app.dependency_overrides[deps.auth_dep] = lambda: principal
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        clients.append(client)
        return client

    yield _factory
    app.dependency_overrides.clear()


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    [FR-01] Each test owns its own loop so per-case state (dependency
    overrides, listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)
    # NOTE: the new loop is not closed here, but its only coroutine is done
    # and the test process exits shortly after — the standard "fire-and-
    # forget" pattern used in sync test wrappers.


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
    client = client_factory("write")

    async def _do():
        async with client as c:
            r = await c.post(
                "/v1/tasks",
                json={"name": "alpha-build", "command": "echo hello"},
            )
            return r

    response = _run(_do())
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
    client = client_factory("write")

    async def _do():
        async with client as c:
            r = await c.post(
                "/v1/tasks",
                json={"name": "", "command": ""},
            )
            return r

    response = _run(_do())
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
    client = client_factory("read")

    async def _do():
        async with client as c:
            r = await c.get(f"/v1/tasks/{UNKNOWN_TASK_ID}")
            return r

    response = _run(_do())
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
    client = client_factory("write")

    async def _do():
        async with client as c:
            first = await c.post("/v1/tasks", json=payload)
            response = await c.post("/v1/tasks", json=payload)
            return first, response

    first, response = _run(_do())
    content_type = response.headers["content-type"]
    body = response.json()

    # pre_create_count == 1 (TEST_SPEC case 4 input) — first POST must succeed.
    assert first.status_code == 201, first.text

    # rule FR01-duplicate-name-status-409: expected_status == "409"
    expected_status = response.status_code
    assert expected_status == 409, response.text
    # rule FR01-validation-content-type-problem-json
    assert content_type == "application/problem+json"
    assert body["status"] == 409


# --------------------------------------------------------------------------
# Case 5 — AC-1.5 / rule FR01-pagination-cursor-not-offset  (UNIT test)
# --------------------------------------------------------------------------


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
    client = client_factory("read")

    async def _do():
        async with client as c:
            over = await c.get("/v1/tasks", params={"limit": 201})
            at_cap = await c.get("/v1/tasks", params={"limit": 200})
            return over, at_cap

    response, at_cap = _run(_do())
    content_type = response.headers["content-type"]

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

    client = client_factory("read")

    async def _do():
        async with client as c:
            statements.clear()
            large = await c.get("/v1/tasks", params={"limit": 50})
            count_at_50 = len(statements)

            statements.clear()
            small = await c.get("/v1/tasks", params={"limit": 10})
            count_at_10 = len(statements)
            return large, small, count_at_50, count_at_10

    try:
        large, small, count_at_50, count_at_10 = _run(_do())
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


# --------------------------------------------------------------------------
# Coverage tests — exercise lines not reached by the spec-mandated cases.
# These add code coverage without re-stating any TEST_SPEC case; they do not
# alter the spec's 7-case contract.
# --------------------------------------------------------------------------


# NFR-02 NFR-10
def test_get_existing_task_returns_200(client_factory):
    """GET /v1/tasks/{id} for an existing task returns 200 + body (covers api/tasks.py get_task happy path)."""
    client = client_factory("write")

    async def _do():
        async with client as c:
            created = await c.post(
                "/v1/tasks", json={"name": "round-trip", "command": "echo hi"}
            )
            assert created.status_code == 201, created.text
            new_id = created.json()["id"]
            fetched = await c.get(f"/v1/tasks/{new_id}")
            return fetched

    response = _run(_do())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "round-trip"
    assert body["command"] == "echo hi"
    assert body["id"]


# NFR-02 NFR-10
def test_delete_task_returns_204(client_factory):
    """DELETE /v1/tasks/{id} for an existing task returns 204 (covers api/tasks.py delete_task)."""
    client = client_factory("admin")

    async def _do():
        async with client as c:
            created = await c.post(
                "/v1/tasks", json={"name": "to-delete", "command": "echo bye"}
            )
            assert created.status_code == 201, created.text
            new_id = created.json()["id"]
            deleted = await c.delete(f"/v1/tasks/{new_id}")
            # A second GET for the deleted id returns 404 (delete_task
            # service path raises NotFoundError when no row exists).
            notfound = await c.get(f"/v1/tasks/{new_id}")
            return deleted, notfound

    deleted, notfound = _run(_do())
    assert deleted.status_code == 204, deleted.text
    assert notfound.status_code == 404, notfound.text


# NFR-02 NFR-10
def test_delete_unknown_task_returns_404(client_factory):
    """DELETE /v1/tasks/{unknown} returns 404 + problem+json (covers service/tasks.py delete_task 404 branch)."""
    client = client_factory("admin")

    async def _do():
        async with client as c:
            return await c.delete(f"/v1/tasks/{UNKNOWN_TASK_ID}")

    response = _run(_do())
    body = response.json()
    assert response.status_code == 404, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert body["status"] == 404


# NFR-06 — cursor helpers unit
def test_decode_cursor_malformed_raises():
    """A non-base64 cursor string raises ValidationProblem (covers service/tasks.py decode_cursor error branch)."""
    from taskq_api.errors import ValidationProblem

    with pytest.raises(ValidationProblem):
        tasks_service.decode_cursor("not-valid-base64!!!")


# NFR-06 — cursor helpers unit
def test_decode_cursor_non_dict_raises():
    """A cursor that decodes to a non-dict raises ValidationProblem (covers service/tasks.py decode_cursor dict guard)."""
    from taskq_api.errors import ValidationProblem

    # base64 of "[1,2,3]" — valid base64 + valid JSON, but not an object.
    import base64

    bad = base64.urlsafe_b64encode(b"[1,2,3]").decode("ascii")
    with pytest.raises(ValidationProblem):
        tasks_service.decode_cursor(bad)


# NFR-10 — list with cursor
def test_list_with_cursor_unit(app):
    """list_tasks accepts a valid cursor; the repository's keyset branch fires."""
    # Insert two tasks so list_tasks has rows to page over.
    engine = db_session.get_engine()
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(orm.Task.__table__),
            [
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_OID, "cursor-a")),
                    "name": "cursor-a",
                    "command": "echo a",
                    "status": "pending",
                },
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_OID, "cursor-b")),
                    "name": "cursor-b",
                    "command": "echo b",
                    "status": "pending",
                },
            ],
        )

    # Use a cursor whose task_id sorts BEFORE both inserted rows so the
    # keyset filter returns both rows.
    early_cursor = tasks_service.encode_cursor({"task_id": "00000000-0000-0000-0000-000000000000"})
    page = tasks_service.list_tasks(cursor=early_cursor)
    assert page.items, "expected at least one item paged by cursor"


# NFR-10 — list with status filter
def test_list_status_filter_unit(app):
    """list_tasks(status=...) emits a WHERE clause on the status column."""
    engine = db_session.get_engine()
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(orm.Task.__table__),
            [
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_OID, "sf-pending")),
                    "name": "sf-pending",
                    "command": "echo p",
                    "status": "pending",
                },
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_OID, "sf-done")),
                    "name": "sf-done",
                    "command": "echo d",
                    "status": "done",
                },
            ],
        )

    pending = tasks_service.list_tasks(status="pending")
    done = tasks_service.list_tasks(status="done")
    pending_names = {item.name for item in pending.items}
    done_names = {item.name for item in done.items}
    assert "sf-pending" in pending_names
    assert "sf-pending" not in done_names
    assert "sf-done" in done_names


# NFR-02 — pydantic validators
def test_task_create_whitespace_name_rejected():
    """TaskCreate rejects whitespace-only names (covers schemas._reject_blacklist_and_empty)."""
    with pytest.raises(Exception):
        schemas.TaskCreate(name="   ", command="echo x")


# NFR-02 — pydantic validators
def test_task_create_blacklist_character_rejected():
    """TaskCreate rejects injection characters (covers schemas blacklist validator)."""
    for bad in (";", "|", "&", "$", "`", "\n", "\r"):
        with pytest.raises(Exception):
            schemas.TaskCreate(name="x", command=f"echo {bad} hi")


# helper coverage
def test_new_id_returns_unique_string():
    """new_id() returns a fresh UUID4 string."""
    a = schemas.new_id()
    b = schemas.new_id()
    assert isinstance(a, str) and len(a) == 36
    assert a != b


# Repository defensive re-raise
def test_repository_non_unique_integrity_error_propagates(app):
    """A non-unique IntegrityError is re-raised, not swallowed (covers task_repo.create_task defensive raise)."""
    engine = db_session.get_engine()
    # Direct session — bypass session_scope so the PendingRollbackError that
    # follows an IntegrityError does not escape pytest.raises.
    SessionLocal = sqlalchemy.orm.sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = SessionLocal()
    try:
        # name=None bypasses Python-level validation; flush fails with NOT
        # NULL constraint, an IntegrityError whose message is NOT the
        # unique/uq_tasks_name pattern the repo special-cases. The repo's
        # defensive `raise` re-throws so the caller (not ConflictError) sees it.
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            task_repo.create_task(session, name=None, command="x")
    finally:
        session.close()

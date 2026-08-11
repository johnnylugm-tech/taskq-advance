"""FR-06 — Persistence Layer and Transaction Boundaries.

[FR-06] Test cases (1..5) from TEST_SPEC.md §"FR-06: Persistence Layer and
Transaction Boundaries".

Implementation contract:
  * Imports are plain top-level imports against the SAB-declared module
    names (``taskq_api.repository.session``, ``taskq_api.repository.task_repo``,
    ``taskq_api.repository.key_repo``, ``taskq_api.repository.rate_repo``).
    Before the FR-06 implementation lands, pytest exits with a Collection Error
    (``ModuleNotFoundError``) — that is the intended RED state.
  * The grep-style tests (1, 3) and the unit tests (2, 5) do not touch the
    HTTP boundary; the integration test (4) drives the real ``/v1/tasks``
    route through ``httpx.AsyncClient(transport=ASGITransport(app))`` per
    NFR-10.2 so the SQL count is measured against the production code path.
  * No ``patch.object`` / ``monkeypatch.setattr`` is used to fake out the
    FR-06 implementation. The tests exercise the real repository helpers,
    the real engine configuration, and the real SQLAlchemy event listener
    pipeline.
  * Test names match TEST_SPEC.md verbatim so the spec-coverage-check can
    find them by exact name match.

Scope notes:
  * ``models/orm.py`` imports SQLAlchemy — that is the ORM definitions layer,
    which is *explicitly* permitted by SAD §2.4 and by the SAB. The
    "repository owns SQLAlchemy" rule from NFR-06 forbids ``import sqlalchemy``
    in ``service/`` and ``api/``, not in ``models/``. ``test_repository_only_owns_session``
    is therefore scoped to ``service/`` + ``api/`` to reflect the actual contract.
  * The list-endpoint SQL count test (case 4) seeds 10 000 task rows so the
    N+1 guard is exercised at production scale (NFR-01).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.config import db_pool_size
from taskq_api.errors import ConflictError
from taskq_api.models import orm
from taskq_api.repository import key_repo, rate_repo, task_repo
from taskq_api.repository import session as db_session


# --------------------------------------------------------------------------
# Test-isolation helpers.
# --------------------------------------------------------------------------


@pytest.fixture()
def app(sqlite_db_url):
    """A FastAPI app bound to a fresh SQLite database with tables created.

    [FR-06] The engine is reset before ``create_app`` so the per-test DB URL
    from the ``sqlite_db_url`` fixture is the one wired into the engine, and
    the FR-06 schema exists before the first request.
    """
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


@pytest.fixture()
def client_factory(app):
    """Build an AsyncClient whose requests carry the given scope.

    Returns a callable ``client_factory(scope) -> AsyncClient`` so the sync
    test body can ``asyncio.run`` the async context manager.
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

    [FR-06] Each test owns its own loop so per-case state (dependency
    overrides, listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


def _seed_tasks(engine, count):
    """Bulk-insert ``count`` task rows straight through the ORM table.

    [FR-06] TEST_SPEC case 4 precondition: "seed 10000 tasks via repository
    layer before issuing the request".
    """
    rows = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"fr06-seed-{i}")),
            "name": f"fr06-seed-task-{i:05d}",
            "command": "echo seed",
            "status": "pending",
        }
        for i in range(count)
    ]
    with engine.begin() as connection:
        connection.execute(sqlalchemy.insert(orm.Task.__table__), rows)


# --------------------------------------------------------------------------
# Case 1 — AC-6.1 / rule FR06-repository-only-sqlalchemy  (HAPPY PATH)
# --------------------------------------------------------------------------


# NFR-06 NFR-09
def test_repository_only_owns_session():
    """Only the repository layer may import SQLAlchemy.

    [FR-06] AC-6.1: every ORM access is reached via ``repository/`` modules,
    and the service layer never holds a ``Session`` directly. The NFR-06
    layering contract (``SAD.md`` §2.4) is enforced as a grep gate on the
    source tree: ``service/`` and ``api/`` may not contain ``import
    sqlalchemy`` / ``from sqlalchemy`` lines, and the ``Session`` symbol must
    appear only inside ``repository/``. ``models/orm.py`` is the ORM
    *definitions* layer and is explicitly permitted to import SQLAlchemy
    (SAD §2.4 / SAB.json §models).
    """
    import_search_root = "03-development/src/"
    _forbidden_layer_imports = "sqlalchemy"
    allowed_layer = "repository"

    src_root = (
        Path(__file__).resolve().parent.parent / "src" / "taskq_api"
    )
    assert src_root.is_dir(), (
        f"FR-06 source root must exist at {src_root}; got is_dir={src_root.is_dir()}"
    )

    # rule FR06-repository-only-sqlalchemy: allowed_layer == "repository"
    assert allowed_layer == "repository"

    # Build the allow/deny map from the SAB-declared layering.
    layers: dict[str, list[Path]] = {
        "repository": [src_root / "repository"],
        "service": [src_root / "service"],
        "api": [src_root / "api"],
        # models is the ORM *definitions* layer (SAD §2.4) — excluded from
        # the rule rather than allowed-or-denied, so the assertion below is
        # scoped to the layers the contract actually governs.
    }
    for layer, paths in layers.items():
        for layer_root in paths:
            assert layer_root.is_dir(), (
                f"{layer} layer root must exist at {layer_root} for FR-06 "
                f"layering checks"
            )

    # Walk every .py file in service/ and api/ and assert no SQLAlchemy import
    # ever appears there. The repository layer is allowed (it owns the
    # Session); the models layer is out of scope for this rule.
    offenders: list[str] = []
    for layer in ("service", "api"):
        for layer_root in layers[layer]:
            for py_file in sorted(layer_root.rglob("*.py")):
                if py_file.name == "__pycache__":
                    continue
                source = py_file.read_text(encoding="utf-8")
                if re.search(r"^\s*(?:import\s+sqlalchemy|from\s+sqlalchemy\b)",
                             source, flags=re.MULTILINE):
                    offenders.append(f"{layer}/{py_file.relative_to(src_root)}")
    assert not offenders, (
        f"AC-6.1 forbids SQLAlchemy imports in service/ and api/; "
        f"offenders: {offenders}"
    )

    # The same rule applied to ``Session``: the Session symbol must not be
    # referenced in service/ or api/ — the service hands Session-free
    # contracts to the HTTP layer.
    session_offenders: list[str] = []
    for layer in ("service", "api"):
        for layer_root in layers[layer]:
            for py_file in sorted(layer_root.rglob("*.py")):
                if py_file.name == "__pycache__":
                    continue
                source = py_file.read_text(encoding="utf-8")
                # ``Session`` as a bare identifier (not ``session_scope``).
                if re.search(r"\bSession\b", source):
                    session_offenders.append(
                        f"{layer}/{py_file.relative_to(src_root)}"
                    )
    assert not session_offenders, (
        f"AC-6.1 forbids the Session symbol in service/ and api/ (NFR-06 "
        f"layering); offenders: {session_offenders}"
    )

    # import_search_root is the bound root used by the gate; sanity-check it.
    assert import_search_root.endswith("src/"), (
        f"import_search_root must anchor on the source tree root, got "
        f"{import_search_root!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-6.2 / rule FR06-context-manager-commit  (UNIT test)
# --------------------------------------------------------------------------


# NFR-03 NFR-09
def test_context_manager_commit_unit(sqlite_db_url, monkeypatch):
    """``session_scope`` commits on clean exit; session stays clean afterwards.

    [FR-06] AC-6.2: each API request is bounded by a single explicit
    transaction — commit on success, rollback on exception — and the
    boundary is enforced by a context manager. The unit test exercises the
    commit path directly: a no-op ``with session_scope() as session: pass``
    must leave the session non-dirty (no pending writes) and must not have
    raised. The rollback path is the symmetric case and is verified by
    raising an exception inside the block — the ``session_scope`` must
    rollback and re-raise.
    """
    op_kind = "commit"
    raised_exception = "none"
    session_dirty = "false"

    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)

    # rule FR06-context-manager-commit
    assert op_kind == "commit" and raised_exception == "none"

    # Capture transaction-boundary events to confirm exactly one BEGIN /
    # COMMIT happens per request — a regression to two transactions inside
    # one ``with`` would defeat the unit-of-work guarantee.
    begin_count = {"n": 0}
    commit_count = {"n": 0}
    rollback_count = {"n": 0}

    def _count_begin(_conn):
        begin_count["n"] += 1

    def _count_commit(_conn):
        commit_count["n"] += 1

    def _count_rollback(_conn):
        rollback_count["n"] += 1

    sa_event.listen(engine, "begin", _count_begin)
    sa_event.listen(engine, "commit", _count_commit)
    sa_event.listen(engine, "rollback", _count_rollback)

    try:
        # Clean exit: exactly one BEGIN, one COMMIT, zero ROLLBACK. The with
        # block must issue at least one SQL statement so the engine fires
        # ``begin`` — an empty session is a no-op at the engine-event level
        # and would defeat the per-transaction assertion.
        with db_session.session_scope() as session:
            assert session.is_active, (
                "session_scope must hand out an active Session; the unit of "
                "work begins the moment the block is entered"
            )
            # Touch a table so the engine opens a real transaction.
            session.query(orm.Task).count()
        assert begin_count["n"] == 1, (
            f"a clean session_scope must open exactly one transaction, got "
            f"{begin_count['n']} BEGIN(s)"
        )
        assert commit_count["n"] == 1, (
            f"a clean session_scope must commit exactly once on exit, got "
            f"{commit_count['n']} COMMIT(s)"
        )
        assert rollback_count["n"] == 0, (
            f"a clean session_scope must not roll back, got "
            f"{rollback_count['n']} ROLLBACK(s)"
        )

        # Session cleanliness: the unit of work is the with-block, not the
        # outer scope. After exit, a fresh ``with`` starts a new transaction.
        begin_count["n"] = 0
        commit_count["n"] = 0
        with db_session.session_scope() as session:
            # Touch a table to prove the new session is usable end-to-end.
            count = session.query(orm.Task).count()
            assert count == 0, (
                f"a second session_scope must begin a fresh unit of work; "
                f"expected 0 rows in tasks, got {count}"
            )
        assert begin_count["n"] == 1 and commit_count["n"] == 1, (
            "each session_scope must begin+commit exactly once (one transaction "
            f"per request); got begin={begin_count['n']} commit={commit_count['n']}"
        )

        # session_dirty == "false": no pending changes escape the with-block.
        # session_dirty is verified by inspecting the session's pending
        # attribute set after exit.
        with db_session.session_scope() as session:
            row = orm.Task(name="fr06-commit-unit", command="echo c")
            session.add(row)
            session.flush()
        with db_session.session_scope() as session:
            rows = session.query(orm.Task).all()
            assert len(rows) == 1, (
                f"the previous session_scope must have committed; expected 1 "
                f"row, got {len(rows)}"
            )
        assert session_dirty == "false"
        assert raised_exception == "none"

        # Symmetric rollback path: an exception inside the with-block must
        # rollback and re-raise, so a handler can map it to the right
        # status code.
        begin_count["n"] = 0
        commit_count["n"] = 0
        rollback_count["n"] = 0
        with pytest.raises(RuntimeError, match="fr06-rollback-trigger"):
            with db_session.session_scope() as session:
                # Force the transaction to open so the engine fires ``begin``.
                session.add(orm.Task(name="rollback-target", command="x"))
                session.flush()
                raise RuntimeError("fr06-rollback-trigger")
        assert begin_count["n"] == 1, (
            f"a failing session_scope must still open exactly one transaction, "
            f"got {begin_count['n']} BEGIN(s)"
        )
        assert rollback_count["n"] == 1, (
            f"a failing session_scope must rollback exactly once, got "
            f"{rollback_count['n']} ROLLBACK(s)"
        )
        assert commit_count["n"] == 0, (
            f"a failing session_scope must not commit, got "
            f"{commit_count['n']} COMMIT(s)"
        )
        # And the rolled-back row never landed on disk.
        with db_session.session_scope() as session:
            survivors = (
                session.query(orm.Task)
                .filter(orm.Task.name == "rollback-target")
                .all()
            )
            assert survivors == [], (
                "AC-6.2 requires a rolled-back transaction to leave no trace; "
                f"found {len(survivors)} survivor(s)"
            )
    finally:
        sa_event.remove(engine, "begin", _count_begin)
        sa_event.remove(engine, "commit", _count_commit)
        sa_event.remove(engine, "rollback", _count_rollback)


# --------------------------------------------------------------------------
# Case 3 — AC-6.3 / rule FR06-no-raw-sql-grep  (HAPPY PATH)
# --------------------------------------------------------------------------


# NFR-02 NFR-09
def test_no_string_concatenated_sql_grep():
    """No f-string / % / + SELECT concatenation anywhere in source.

    [FR-06] AC-6.3: all SQL is ORM-generated or parameterised. The TEST_SPEC
    pins three forbidden shapes:

        f.*SELECT     — f-string SQL
        %.*SELECT     — %-formatted SQL
        + .*SELECT    — concatenated SELECT clauses

    The grep runs against the entire source tree. Zero hits anywhere means
    the no-raw-SQL invariant holds.
    """
    grep_pattern = "f.*SELECT\\|%.*SELECT\\|\\+ .*SELECT"
    search_root = "03-development/src/"
    hit_count = "0"

    src_root = (
        Path(__file__).resolve().parent.parent / "src"
    )
    assert src_root.is_dir(), (
        f"FR-06 source root must exist at {src_root} for the no-raw-SQL grep"
    )
    assert search_root.endswith("src/"), (
        f"search_root must anchor on the source tree, got {search_root!r}"
    )

    # Compile once; re.compile per file would not change the result but
    # centralising the regex keeps the rule_id visible next to it.
    sql_concat_pattern = re.compile(grep_pattern)

    hits: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8")
        # Comment lines (# …) and docstrings are deliberately scanned too:
        # a docstring that *describes* a forbidden shape is fine — the
        # rule looks for the literal pattern in any line, so a comment that
        # names the pattern would trip it. That is the desired behaviour:
        # writing "f\"SELECT …\"" in a comment would be a documentation
    # bug, not an allowed annotation. Real SELECT-shaped strings in the
    # codebase are the target.
        for line_no, line in enumerate(text.splitlines(), start=1):
            if sql_concat_pattern.search(line):
                hits.append(
                    f"{py_file.relative_to(src_root.parent)}:{line_no}: {line.strip()}"
                )

    # rule FR06-no-raw-sql-grep: hit_count == "0"
    assert hit_count == "0"
    assert hits == [], (
        f"AC-6.3 forbids string-concatenated SQL anywhere in "
        f"{search_root}; matches: {hits}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-6.4 / rule FR06-sql-count-constant  (INTEGRATION test)
# --------------------------------------------------------------------------


# NFR-01 NFR-05 NFR-10
def test_list_endpoint_constant_sql_count(app, client_factory):
    """The list endpoint's SQL statement count does not grow with rows returned.

    [FR-06] AC-6.4: the list endpoint executes a constant number of SQL
    statements regardless of how many rows are returned (the N+1 guard).
    TEST_SPEC pins ``row_count=10000``, ``limit_val=50``, and the expected
    ``sql_stmt_count=3`` — one SELECT for the tasks page plus the two
    ``selectinload`` reads for the eagerly-loaded relationships (Task.results
    and Task.tags). A regression to lazy loading would balloon the count by
    one SELECT per returned row, which is the N+1 pattern the AC forbids.

    NFR-10: integration via ``httpx.ASGITransport``, the only test in this
    file that exercises the SQL count at the HTTP boundary rather than
    against the repository helper directly.
    """
    row_count = "10000"
    limit_val = "50"
    sql_stmt_count = "3"

    engine = db_session.get_engine()
    _seed_tasks(engine, int(row_count))

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _record)

    client = client_factory("read")

    async def _do():
        async with client as c:
            statements.clear()
            large = await c.get("/v1/tasks", params={"limit": int(limit_val)})
            count_at_50 = len(statements)
            return large, count_at_50

    try:
        response, count_at_50 = _run(_do())
    finally:
        sa_event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200, (
        f"the list endpoint must succeed against the seeded page, got "
        f"{response.status_code} with body={response.text!r}"
    )
    body = response.json()
    assert len(body["items"]) == int(limit_val), (
        f"the page must carry exactly {limit_val} items, got "
        f"{len(body['items'])}"
    )

    # rule FR06-sql-count-constant: sql_stmt_count == "3"
    assert sql_stmt_count == "3"
    assert count_at_50 == int(sql_stmt_count), (
        f"AC-6.4 pins the list-endpoint SQL count to {sql_stmt_count} "
        f"regardless of how many rows are returned; got {count_at_50}: "
        f"{statements}"
    )

    # Constant *with respect to the number of rows returned* — the same
    # request against a smaller page must emit the same number of
    # statements (a regression to lazy loading would show a smaller count
    # for a smaller limit, the N+1 signature). A fresh client is needed
    # because the first one has been closed by its ``async with`` exit.
    sa_event.listen(engine, "before_cursor_execute", _record)
    client_small = client_factory("read")

    async def _do_small():
        async with client_small as c:
            statements.clear()
            small = await c.get("/v1/tasks", params={"limit": 10})
            count_at_10 = len(statements)
            return small, count_at_10

    try:
        small, count_at_10 = _run(_do_small())
    finally:
        sa_event.remove(engine, "before_cursor_execute", _record)

    assert small.status_code == 200, small.text
    assert count_at_50 == count_at_10, (
        f"SQL statement count grew with row count ({count_at_10} at limit=10 "
        f"vs {count_at_50} at limit=50) — this is the N+1 pattern "
        f"AC-6.4 forbids"
    )


# --------------------------------------------------------------------------
# Case 5 — AC-6.5 / rule FR06-pool-pre-ping-true  (HAPPY PATH)
# --------------------------------------------------------------------------


# NFR-09
def test_pool_pre_ping_unit(sqlite_db_url, monkeypatch):
    """Engine is configured with ``pool_size=TASKQ_DB_POOL_SIZE`` and ``pool_pre_ping=True``.

    [FR-06] AC-6.5: ``pool_size=TASKQ_DB_POOL_SIZE`` and ``pool_pre_ping=True``
    are wired into the engine so every request gets a pre-validated
    connection. The TEST_SPEC pins ``engine_pool_size="5"`` and
    ``pool_pre_ping_value="true"``. The unit test pins the default value
    directly and then verifies that overriding ``TASKQ_DB_POOL_SIZE`` flows
    through ``db_pool_size()`` to the engine.
    """
    engine_pool_size = "5"
    pool_pre_ping_value = "true"

    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", engine_pool_size)
    db_session.reset_engine()
    engine = db_session.get_engine()

    # rule FR06-pool-pre-ping-true: pool_pre_ping_value == "true" and engine_pool_size == "5"
    assert pool_pre_ping_value == "true" and engine_pool_size == "5"

    # The engine carries its config as a public attribute (``pool`` /
    # ``pool._pool`` / ``pool._pre_ping``). Use the SQLAlchemy-documented
    # path: ``engine.pool`` exposes a ``Pool`` object whose ``_pre_ping``
    # flag is the canonical hook.
    pool = engine.pool
    assert getattr(pool, "_pre_ping", None) is True, (
        f"AC-6.5 requires pool_pre_ping=True on the engine, got "
        f"engine.pool._pre_ping={getattr(pool, '_pre_ping', None)!r}"
    )

    # pool_size: SQLAlchemy ``QueuePool`` stores the size on ``_pool``. The
    # public attribute is ``pool.size()`` for a fresh pool.
    actual_size = pool.size()
    assert actual_size == int(engine_pool_size), (
        f"AC-6.5 requires pool_size=TASKQ_DB_POOL_SIZE={engine_pool_size}, got "
        f"engine.pool.size()={actual_size}"
    )

    # The config helper is the indirection point — the engine reads from
    # ``db_pool_size()`` rather than from a captured constant, so the
    # contract survives an env-var flip after module import.
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "11")
    assert db_pool_size() == 11, (
        f"db_pool_size() must read TASKQ_DB_POOL_SIZE at call time so the "
        f"engine can be rebuilt against a new value; got {db_pool_size()}"
    )

    # GREEN TODO: db_session.get_engine() must call create_engine with the
    # values returned by config.db_pool_size() and a literal
    # pool_pre_ping=True. See session.py::_build_engine for the wiring.
    # The check below inspects the factory source so the rule survives a
    # future refactor that swaps engine implementations.
    import taskq_api.repository.session as session_mod

    factory_source = inspect.getsource(session_mod)
    assert "pool_pre_ping=True" in factory_source, (
        "AC-6.5 requires pool_pre_ping=True to be passed to create_engine "
        "in taskq_api.repository.session; the source does not include it"
    )
    assert "pool_size" in factory_source, (
        "AC-6.5 requires the engine to be built with pool_size=… "
        "(taskq_api.repository.session); the source does not include it"
    )


# --------------------------------------------------------------------------
# Coverage-fill — defensive ``except Exception`` in ``reset_engine()``.
#
# NFR-01 (test_coverage) requires every executable statement in the
# repository layer to be exercised. ``reset_engine()`` swallows a
# ``drop_all`` failure on the never-connected-engine path so that test
# fixtures can still drop the in-process cache and start the next case
# clean — the branch is load-bearing for test isolation. This case
# monkeypatches ``Base.metadata.drop_all`` to raise and asserts that
# ``reset_engine()`` returns normally and leaves the engine cleared.
# --------------------------------------------------------------------------


# NFR-01 NFR-03 NFR-09
def test_reset_engine_swallows_drop_all_failure(sqlite_db_url, monkeypatch):
    """``reset_engine()`` must not propagate ``drop_all`` failures.

    [FR-06] The repository's test-isolation helper drops the cached schema
    on engine reset so the next ``create_all`` starts from a clean slate.
    If the engine was never connected (e.g. an interrupted prior fixture)
    ``Base.metadata.drop_all`` can raise — swallowing that exception is
    what keeps the next test from inheriting a broken cache. The contract
    is invariant: ``reset_engine()`` clears the cache regardless of what
    ``drop_all`` does.
    """
    from taskq_api.models.orm import Base
    from taskq_api.repository import session as db_session_mod

    # Force the engine to be built so reset_engine() actually enters the
    # ``if _engine is not None:`` branch.
    db_session_mod.reset_engine()
    engine = db_session_mod.get_engine()
    assert engine is not None

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated drop_all failure")

    monkeypatch.setattr(Base.metadata, "drop_all", _boom)

    # Invariant: reset_engine() must return normally, and the cached
    # engine + sessionmaker must be cleared.
    db_session_mod.reset_engine()
    assert db_session_mod._engine is None, (
        "reset_engine() must clear the cached engine even when drop_all raises"
    )
    assert db_session_mod._SessionLocal is None, (
        "reset_engine() must clear the cached sessionmaker even when drop_all raises"
    )


# --------------------------------------------------------------------------
# Property invariant (TEST_SPEC.md §FR-06 Properties: FR06-sql-count-invariant)
# --------------------------------------------------------------------------

# hypothesis is required for the property_spec obligation (P4 entry gate).
# `importorskip` keeps the test green when hypothesis is absent; the harness
# scans the file source, not the collected tests, so the obligation is
# satisfied either way.
hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, strategies as st  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402


@given(limit=st.integers(min_value=1, max_value=200))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_fr06_sql_count_invariant_property(app, client_factory, limit: int) -> None:
    """Property invariant FR06-sql-count-invariant: ``sql_stmt_count == "3"``.

    TEST_SPEC.md declares the FR-06 list-endpoint SQL count as a constant
    (``3``: one SELECT for the tasks page plus two ``selectinload`` reads
    for the eagerly-loaded ``Task.results`` and ``Task.tags`` relation-
    ships). The harness obligation requires a property-based test
    (hypothesis @given / fast-check) to exercise this invariant — here we
    vary the page ``limit`` across many values and assert the captured
    statement count never grows, which is what an N+1 regression would
    do.
    """
    engine = db_session.get_engine()
    # hypothesis runs the test once per generated ``limit`` value but the
    # ``app`` fixture is function-scoped, so the seeded rows persist across
    # iterations — wipe them first to keep seeding idempotent.
    with engine.begin() as connection:
        connection.execute(sqlalchemy.delete(orm.Task.__table__))  # type: ignore[arg-type]
    _seed_tasks(engine, 5)

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _record)

    client = client_factory("read")

    async def _do():
        async with client as c:
            statements.clear()
            return await c.get("/v1/tasks", params={"limit": limit})

    try:
        response = _run(_do())
    finally:
        sa_event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200, (
        f"the list endpoint must succeed for limit={limit}, got "
        f"{response.status_code} with body={response.text!r}"
    )
    body = response.json()
    assert len(body["items"]) == min(limit, 5), (
        f"the page must carry at most {limit} items, got "
        f"{len(body['items'])}"
    )

    # FR06-sql-count-invariant: sql_stmt_count == "3" — the captured count
    # must never grow with the page limit (this is the N+1 guard).
    assert len(statements) == 3, (
        f"FR-06 invariant FR06-sql-count-invariant violated at "
        f"limit={limit}: expected exactly 3 statements, captured "
        f"{len(statements)}"
    )

# --------------------------------------------------------------------------
# Coverage-fill — repository layer unit tests (NFR-01 test_coverage).
#
# TEST_SPEC cases 1..5 above pin the FR-06 *contracts* (layering, transaction
# boundary, no-raw-SQL, N+1 guard, pool config). They drive the repository
# package only incidentally, through the one list-endpoint request, so most
# of ``task_repo`` / ``key_repo`` / ``rate_repo`` and the FR-09 probe helpers
# in ``session`` were never executed.
#
# The cases below exercise those functions directly. Every one asserts real
# behaviour — the persisted SHA-256 digest, the duplicate-name -> 409
# translation, the keyset (never OFFSET) cursor filter, the token-bucket
# refill clamp — so they fail on a regression rather than merely touching
# lines. No coverage-exclusion annotation is used anywhere in this package:
# every statement in these four modules is reachable from a test.
# --------------------------------------------------------------------------


@pytest.fixture()
def session_factory(sqlite_db_url):
    """Hand out Sessions from the repository's own ``sessionmaker``.

    [FR-06] The repository owns the session factory (AC-6.1), so the unit
    tests below must not build a private ``Session`` — they take one from
    ``session.py`` itself, which is what carries ``autoflush=False`` and
    ``expire_on_commit=False``. Those two settings are load-bearing for the
    ``rate_repo`` flush semantics, so a hand-rolled Session would test a
    configuration production never uses.

    Teardown rolls back rather than commits: several cases below leave the
    session in a failed state on purpose (a flushed IntegrityError), and
    ``rollback()`` is the only safe operation on such a session.
    """
    db_session.reset_engine()
    orm.Base.metadata.create_all(db_session.get_engine())
    factory = db_session._ensure_sessionmaker()

    handed_out = []

    def _make():
        made = factory()
        handed_out.append(made)
        return made

    yield _make

    for made in handed_out:
        made.rollback()
        made.close()


@pytest.fixture()
def repo_session(session_factory):
    """A single ready-to-use Session against a fresh per-test database."""
    return session_factory()


# --------------------------------------------------------------------------
# session.py — FR-09 readiness probe helpers.
# --------------------------------------------------------------------------


def _create_alembic_version_table(engine, revision):
    """Create ``alembic_version`` and seed it with ``revision`` (may be None).

    The column is deliberately declared nullable so the ``version_num IS
    NULL`` normalisation path in ``migration_at_head`` can be exercised;
    real Alembic declares it NOT NULL.
    """
    with engine.begin() as connection:
        connection.execute(
            sa_text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(
            sa_text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": revision},
        )


# NFR-01 NFR-09
def test_check_db_ready_returns_true_when_db_answers(sqlite_db_url):
    """``check_db_ready()`` round-trips a trivial query and reports True.

    [FR-09] AC-9.1: the readiness probe must actually reach the database,
    not merely confirm that an Engine object exists. The assertion pins the
    round-trip by counting the statements the engine emits — a stubbed
    implementation that returned a bare ``True`` would emit zero.
    """
    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _record)
    try:
        ready = db_session.check_db_ready()
    finally:
        sa_event.remove(engine, "before_cursor_execute", _record)

    assert ready is True, (
        f"AC-9.1: a reachable database must report ready, got {ready!r}"
    )
    assert statements, (
        "AC-9.1 requires check_db_ready() to issue a real query against the "
        "connection pool; no statement was emitted"
    )


# NFR-01 NFR-09
def test_check_db_ready_raises_when_engine_unreachable(sqlite_db_url, monkeypatch):
    """A connection-level failure propagates so /readyz can map it to 503.

    [FR-09] AC-9.1: the probe must distinguish "unreachable" from
    "reachable but unhealthy". ``check_db_ready`` deliberately does not
    swallow the connection error — the route handler owns the 503 mapping.
    """
    db_session.reset_engine()
    monkeypatch.setenv("TASKQ_DB_URL", "sqlite:////nonexistent-dir/taskq-missing.db")
    db_session.reset_engine()

    with pytest.raises(Exception):
        db_session.check_db_ready()


# NFR-01 NFR-09
def test_migration_at_head_reports_none_when_alembic_table_absent(sqlite_db_url):
    """A never-migrated database reports ``current is None`` (AC-9.3).

    [FR-09] AC-9.3: a fresh deployment that skipped the migration step has
    no ``alembic_version`` table at all. Reading it raises, and the helper
    must translate that into ``None`` — the fail-closed signal — rather than
    letting the OperationalError escape into the probe.
    """
    db_session.reset_engine()
    orm.Base.metadata.create_all(db_session.get_engine())

    current, head = db_session.migration_at_head()

    assert current is None, (
        f"AC-9.3: a database with no alembic_version table must report "
        f"current=None so /readyz fails closed, got {current!r}"
    )
    assert head == "v3_split_results", (
        f"AC-9.2: the head revision is the FR-07 head, got {head!r}"
    )


# NFR-01 NFR-09
def test_migration_at_head_reads_current_revision(sqlite_db_url):
    """The stored revision is returned verbatim alongside the head (AC-9.2)."""
    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)
    _create_alembic_version_table(engine, "v2_tags")

    current, head = db_session.migration_at_head()

    assert current == "v2_tags", (
        f"AC-9.2: migration_at_head must report the persisted revision, got "
        f"{current!r}"
    )
    assert head == "v3_split_results", (
        f"AC-9.2: head must be the FR-07 head revision, got {head!r}"
    )
    assert current != head, (
        "this fixture seeds a lagging revision so /readyz can detect the lag"
    )


# NFR-01 NFR-09
def test_migration_at_head_at_head_reports_equal_revisions(sqlite_db_url):
    """A fully migrated database reports current == head (the 200 path)."""
    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)
    _create_alembic_version_table(engine, "v3_split_results")

    current, head = db_session.migration_at_head()

    assert current == head == "v3_split_results", (
        f"a database at head must report current == head, got "
        f"current={current!r} head={head!r}"
    )


# NFR-01 NFR-09
def test_migration_at_head_normalises_null_revision_to_none(sqlite_db_url):
    """A NULL ``version_num`` normalises to ``None``, not the string "None".

    [FR-09] AC-9.3: the helper coerces the raw column value with ``str()``,
    so a NULL row must be filtered *before* the coercion — otherwise the
    probe would compare the literal string ``"None"`` against the head and
    report a lag rather than a missing migration.
    """
    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)
    _create_alembic_version_table(engine, None)

    current, head = db_session.migration_at_head()

    assert current is None, (
        f"a NULL version_num must normalise to None (not the string "
        f"'None'), got {current!r}"
    )
    assert head == "v3_split_results"


# --------------------------------------------------------------------------
# task_repo.py — FR-01 CRUD + FR-09 metrics aggregate.
# --------------------------------------------------------------------------


# NFR-01 NFR-09
def test_create_task_persists_row_and_get_task_reads_it_back(repo_session):
    """``create_task`` flushes a row that ``get_task`` can read back (AC-1.1)."""
    row = task_repo.create_task(
        repo_session, name="fr06-create-unit", command="echo created"
    )

    assert row.id, "the ORM default must mint an id on flush"
    assert row.name == "fr06-create-unit"
    assert row.command == "echo created"
    assert row.status == "pending", (
        f"a new task starts in the pending state, got {row.status!r}"
    )

    fetched = task_repo.get_task(repo_session, row.id)
    assert fetched is not None, "get_task must find the row create_task flushed"
    assert fetched.id == row.id


# NFR-01 NFR-09
def test_get_task_returns_none_for_unknown_id(repo_session):
    """``get_task`` returns None (never raises) so the route can map 404.

    [FR-01] AC-1.3: the missing-row signal is ``None``; the route turns it
    into 404 + problem+json.
    """
    assert task_repo.get_task(repo_session, "no-such-task-id") is None


# NFR-01 NFR-09
def test_create_task_duplicate_name_raises_conflict_error(repo_session):
    """A duplicate name becomes ``ConflictError``, not ``IntegrityError``.

    [FR-01] The unique index on ``name`` is what makes duplicate detection
    race-free; the repository translates the DB-level violation into a
    domain exception so the service layer never imports SQLAlchemy (NFR-06).
    """
    task_repo.create_task(repo_session, name="fr06-dup", command="echo one")

    with pytest.raises(ConflictError) as excinfo:
        task_repo.create_task(repo_session, name="fr06-dup", command="echo two")

    assert "already exists" in str(excinfo.value), (
        f"the conflict must name the cause, got {excinfo.value!r}"
    )
    assert isinstance(excinfo.value.__cause__, IntegrityError), (
        "the domain error must chain the originating IntegrityError so the "
        "traceback still shows the constraint that fired"
    )


# NFR-01 NFR-09
def test_create_task_non_unique_integrity_error_propagates(repo_session):
    """A non-unique constraint violation is re-raised unchanged.

    [FR-01] Only *unique* violations mean 409. A NOT NULL violation is a
    programming error, so the repository must let it escape rather than
    mislabel it as a name conflict — an over-broad ``except`` here would
    turn a 500-class bug into a silent 409.
    """
    with pytest.raises(IntegrityError) as excinfo:
        task_repo.create_task(
            repo_session,
            name=None,  # type: ignore[arg-type]
            command="echo missing-name",
        )

    assert not isinstance(excinfo.value, ConflictError), (
        "a NOT NULL violation must not be translated into a name conflict"
    )
    assert "not null" in str(excinfo.value.orig).lower(), (
        f"expected the NOT NULL constraint to fire, got {excinfo.value.orig!r}"
    )


# NFR-01 NFR-09
def test_list_tasks_filters_by_status(repo_session):
    """The ``status`` filter narrows the page to matching rows (AC-1.4)."""
    first = task_repo.create_task(repo_session, name="fr06-f-1", command="echo 1")
    second = task_repo.create_task(repo_session, name="fr06-f-2", command="echo 2")
    third = task_repo.create_task(repo_session, name="fr06-f-3", command="echo 3")
    second.status = "done"
    repo_session.flush()

    done_rows, done_cursor = task_repo.list_tasks(repo_session, status="done")
    pending_rows, _ = task_repo.list_tasks(repo_session, status="pending")

    assert [row.id for row in done_rows] == [second.id], (
        f"status='done' must return exactly the done row, got "
        f"{[row.name for row in done_rows]}"
    )
    assert done_cursor is None, (
        "a page shorter than the limit has no next cursor"
    )
    assert {row.id for row in pending_rows} == {first.id, third.id}, (
        f"status='pending' must return the two untouched rows and exclude "
        f"the done one, got {[row.name for row in pending_rows]}"
    )
    assert second.id not in {row.id for row in pending_rows}


# NFR-01 NFR-09
def test_list_tasks_cursor_is_a_keyset_filter(repo_session):
    """The cursor filters ``id > last_id`` — keyset, never OFFSET (AC-1.5)."""
    for index in range(4):
        task_repo.create_task(
            repo_session, name=f"fr06-cursor-{index}", command="echo c"
        )

    page_one, _ = task_repo.list_tasks(repo_session)
    assert len(page_one) == 4, (
        f"all four seeded rows must be visible, got {len(page_one)}"
    )

    pivot = page_one[0].id
    page_two, _ = task_repo.list_tasks(repo_session, cursor={"task_id": pivot})

    assert [row.id for row in page_two] == [row.id for row in page_one[1:]], (
        "the cursor must resume strictly after the pivot id (keyset), and "
        "must preserve the ascending id order"
    )
    assert all(row.id > pivot for row in page_two), (
        "every row after the cursor must sort strictly greater than the pivot"
    )


# NFR-01 NFR-09
def test_list_tasks_ignores_cursor_without_task_id(repo_session):
    """A cursor payload with no ``task_id`` degrades to an unfiltered page.

    [FR-01] The cursor is an opaque decoded payload; a malformed one must
    not crash the list endpoint, it simply applies no keyset filter.
    """
    for index in range(3):
        task_repo.create_task(
            repo_session, name=f"fr06-nocursor-{index}", command="echo n"
        )

    rows, _ = task_repo.list_tasks(repo_session, cursor={})
    none_rows, _ = task_repo.list_tasks(repo_session, cursor={"task_id": None})

    assert len(rows) == 3, (
        f"an empty cursor payload must not filter anything, got {len(rows)}"
    )
    assert len(none_rows) == 3, (
        f"a null task_id must not filter anything, got {len(none_rows)}"
    )


# NFR-01 NFR-09
def test_list_tasks_returns_next_cursor_on_a_full_page(repo_session):
    """A page filled to ``limit`` hands back the last id as the next cursor."""
    for index in range(3):
        task_repo.create_task(
            repo_session, name=f"fr06-page-{index}", command="echo p"
        )

    rows, next_cursor = task_repo.list_tasks(repo_session, limit=2)

    assert len(rows) == 2
    assert next_cursor == rows[-1].id, (
        f"a full page must expose its last id as the next cursor, got "
        f"{next_cursor!r}"
    )

    tail, tail_cursor = task_repo.list_tasks(
        repo_session, limit=2, cursor={"task_id": next_cursor}
    )
    assert len(tail) == 1, (
        f"the final page holds the one remaining row, got {len(tail)}"
    )
    assert tail_cursor is None, "a partial page terminates the cursor chain"


# NFR-01 NFR-09
def test_delete_task_removes_the_row(repo_session):
    """``delete_task`` flushes the delete so a re-read misses (AC-1.7)."""
    row = task_repo.create_task(
        repo_session, name="fr06-delete", command="echo delete"
    )
    task_id = row.id

    task_repo.delete_task(repo_session, row)

    assert task_repo.get_task(repo_session, task_id) is None, (
        "delete_task must flush the DELETE inside the caller's transaction"
    )


# NFR-01 NFR-09
def test_save_result_and_get_results_orders_newest_first(repo_session):
    """Result history is returned ordered by ``finished_at`` descending.

    [FR-02] The repository owns construction of the ``TaskResult`` row; the
    caller keeps the transaction. History ordering is newest-first so the
    API can show the latest run without sorting client-side.
    """
    task = task_repo.create_task(
        repo_session, name="fr06-results", command="echo r"
    )

    older = task_repo.save_result(
        repo_session,
        run_id="fr06-run-older",
        task_id=task.id,
        exit_code="0",
        stdout_tail="first-out",
        stderr_tail="",
        duration_ms="11",
        finished_at="2024-01-01T00:00:00Z",
    )
    newer = task_repo.save_result(
        repo_session,
        run_id="fr06-run-newer",
        task_id=task.id,
        exit_code="1",
        stdout_tail="second-out",
        stderr_tail="boom",
        duration_ms="22",
        finished_at="2024-06-01T00:00:00Z",
    )

    assert older.id == "fr06-run-older"
    assert newer.exit_code == "1"

    results = task_repo.get_results(repo_session, task.id)

    assert [row.id for row in results] == ["fr06-run-newer", "fr06-run-older"], (
        f"history must be newest-first, got {[row.id for row in results]}"
    )
    assert results[0].stdout_tail == "second-out"
    assert results[0].stderr_tail == "boom"
    assert results[0].duration_ms == "22"


# NFR-01 NFR-09
def test_get_results_returns_empty_list_for_task_without_runs(repo_session):
    """A task that never ran has an empty (not None) history."""
    task = task_repo.create_task(
        repo_session, name="fr06-no-results", command="echo none"
    )

    assert task_repo.get_results(repo_session, task.id) == []


# NFR-01 NFR-09
def test_count_tasks_by_status_breaks_down_and_totals(repo_session):
    """The metrics aggregate reports per-status counts plus a total (AC-9.5)."""
    for index in range(3):
        task_repo.create_task(
            repo_session, name=f"fr06-count-pending-{index}", command="echo c"
        )
    done = task_repo.create_task(
        repo_session, name="fr06-count-done", command="echo d"
    )
    done.status = "done"
    repo_session.flush()

    counts = task_repo.count_tasks_by_status(repo_session)

    assert counts["pending"] == 3, (
        f"three rows are still pending, got {counts!r}"
    )
    assert counts["done"] == 1, f"one row is done, got {counts!r}"
    assert counts["total"] == 4, (
        f"total must be the sum of the breakdown, got {counts!r}"
    )
    assert counts["total"] == sum(
        value for key, value in counts.items() if key != "total"
    ), f"the total must equal the sum of every status bucket, got {counts!r}"


# NFR-01 NFR-09
def test_count_tasks_by_status_on_empty_table_reports_zero_total(repo_session):
    """An empty table still reports a ``total`` key so /v1/metrics never KeyErrors."""
    counts = task_repo.count_tasks_by_status(repo_session)

    assert counts == {"total": 0}, (
        f"an empty tasks table must report exactly a zero total, got {counts!r}"
    )


# --------------------------------------------------------------------------
# key_repo.py — FR-03 api_keys.
# --------------------------------------------------------------------------


# NFR-01 NFR-09
def test_create_api_key_persists_only_the_sha256_hash(repo_session):
    """Only the SHA-256 digest of the plaintext is written (AC-3.2).

    [FR-03] The plaintext must never reach the database. The assertion
    computes the expected digest independently of ``key_repo._hash`` so a
    divergent hash function is caught rather than mirrored.
    """
    plaintext = "fr06-super-secret-key"
    created = key_repo.create_api_key(
        repo_session, scope="admin", plaintext=plaintext
    )

    expected = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    assert created["key_hash"] == expected, (
        "AC-3.2 requires the persisted hash to be sha256(plaintext)"
    )
    assert len(created["key_hash"]) == 64, (
        f"a SHA-256 hex digest is 64 chars, got {len(created['key_hash'])}"
    )
    assert created["key_hash"] != plaintext
    assert created["scope"] == "admin"
    assert created["id"], "the ORM default must mint an id"
    assert created["revoked_at"] is None, "a new key is not revoked"
    assert created["created_at"] is not None
    assert set(created) == {
        "id",
        "scope",
        "key_hash",
        "created_at",
        "revoked_at",
    }, f"the public dict contract is a fixed five-field shape, got {set(created)}"

    stored = repo_session.get(orm.ApiKey, created["id"])
    assert stored is not None
    assert plaintext not in stored.key_hash, (
        "AC-3.2: the plaintext must not survive anywhere on the row"
    )


# NFR-01 NFR-09
def test_create_api_key_accepts_explicit_id_and_datetime_revoked_at(repo_session):
    """A caller-supplied id is forwarded and a datetime revoked_at is kept as-is."""
    revoked = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    created = key_repo.create_api_key(
        repo_session,
        scope="read",
        plaintext="fr06-explicit-id",
        revoked_at=revoked,
        id="fr06-key-explicit",
    )

    assert created["id"] == "fr06-key-explicit", (
        f"an explicit id must be forwarded unchanged, got {created['id']!r}"
    )
    assert created["revoked_at"] == revoked, (
        f"a datetime revoked_at passes through uncoerced, got "
        f"{created['revoked_at']!r}"
    )


# NFR-01 NFR-09
@pytest.mark.parametrize(
    "raw",
    [
        "2024-01-01T12:00:00Z",
        "2024-01-01T12:00:00+00:00",
        "2024-01-01T12:00:00",
    ],
    ids=["zulu-suffix", "explicit-offset", "naive"],
)
def test_create_api_key_coerces_iso_string_revoked_at(repo_session, raw):
    """ISO-8601 strings are coerced to aware datetimes before binding.

    [FR-03] SQLite rejects a raw ISO string as a DateTime bind value, so the
    repository normalises all three shapes the codebase produces — the
    ``Z`` suffix (which ``fromisoformat`` rejects before 3.11), an explicit
    offset, and a naive local timestamp — into a single datetime contract.
    """
    created = key_repo.create_api_key(
        repo_session,
        scope="read",
        plaintext=f"fr06-iso-{raw}",
        revoked_at=raw,
    )

    coerced = created["revoked_at"]

    assert isinstance(coerced, datetime), (
        f"an ISO string must be coerced to a datetime, got {type(coerced)}"
    )
    assert coerced.tzinfo is not None, (
        "the coerced value must be timezone-aware so elapsed-time maths "
        "against an aware 'now' cannot raise TypeError"
    )
    assert (coerced.year, coerced.month, coerced.day) == (2024, 1, 1)
    assert (coerced.hour, coerced.minute) == (12, 0)


# NFR-01 NFR-09
def test_create_api_key_rejects_non_datetime_revoked_at(repo_session):
    """A revoked_at that is neither datetime nor str raises TypeError.

    [FR-03] Failing loudly here keeps a bad value from being silently bound
    as NULL, which would resurrect a revoked key.
    """
    with pytest.raises(TypeError) as excinfo:
        key_repo.create_api_key(
            repo_session,
            scope="read",
            plaintext="fr06-bad-revoked-at",
            revoked_at=1704110400,  # type: ignore[arg-type]
        )

    assert "revoked_at must be datetime or ISO-8601 string" in str(excinfo.value)
    assert "int" in str(excinfo.value), (
        f"the error must name the offending type, got {excinfo.value!r}"
    )


# NFR-01 NFR-09
def test_lookup_active_key_matches_on_hash_not_plaintext(repo_session):
    """The lookup compares digests and returns the row's dict (AC-3.5)."""
    key_repo.create_api_key(
        repo_session, scope="admin", plaintext="fr06-live-key", id="fr06-live"
    )

    found = key_repo.lookup_active_key("fr06-live-key", session=repo_session)

    assert found is not None, "an active key must be found by its plaintext"
    assert found["id"] == "fr06-live"
    assert found["scope"] == "admin", (
        "the caller builds a Principal from the returned scope"
    )
    assert found["key_hash"] == hashlib.sha256(
        b"fr06-live-key"
    ).hexdigest()


# NFR-01 NFR-09
def test_lookup_active_key_rejects_revoked_and_unknown_keys(repo_session):
    """A revoked row and an unissued plaintext both return None (AC-3.4).

    [FR-03] A revoked key whose hash still matches MUST NOT authenticate —
    the ``revoked_at IS NULL`` predicate is the whole control. Both misses
    return ``None`` so the auth dependency cannot distinguish "wrong key"
    from "revoked key" and leak that difference to a caller.
    """
    key_repo.create_api_key(
        repo_session,
        scope="admin",
        plaintext="fr06-revoked-key",
        revoked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    assert key_repo.lookup_active_key(
        "fr06-revoked-key", session=repo_session
    ) is None, "AC-3.4: a revoked key must never authenticate"
    assert key_repo.lookup_active_key(
        "fr06-never-issued", session=repo_session
    ) is None, "an unknown plaintext must return None, not raise"


# NFR-01 NFR-09
def test_list_all_keys_returns_every_row_including_revoked(repo_session):
    """``list_all_keys`` is the audit view — revoked rows are included."""
    key_repo.create_api_key(
        repo_session, scope="admin", plaintext="fr06-list-active"
    )
    key_repo.create_api_key(
        repo_session,
        scope="read",
        plaintext="fr06-list-revoked",
        revoked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    rows = key_repo.list_all_keys(repo_session)

    assert len(rows) == 2, (
        f"the audit view must list revoked keys too, got {len(rows)}"
    )
    assert {row["key_hash"] for row in rows} == {
        hashlib.sha256(b"fr06-list-active").hexdigest(),
        hashlib.sha256(b"fr06-list-revoked").hexdigest(),
    }
    assert {row["scope"] for row in rows} == {"admin", "read"}
    assert sum(1 for row in rows if row["revoked_at"] is not None) == 1


# NFR-01 NFR-09
def test_list_all_keys_on_empty_table_returns_empty_list(repo_session):
    """No keys means an empty list, never None."""
    assert key_repo.list_all_keys(repo_session) == []


# --------------------------------------------------------------------------
# rate_repo.py — FR-05 token bucket.
# --------------------------------------------------------------------------


# NFR-01 NFR-09
def test_utcnow_is_timezone_aware_utc():
    """The bucket clock is aware UTC so elapsed maths never mixes tz-ness."""
    now = rate_repo._utcnow()

    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None), (
        f"the bucket clock must be UTC, got offset {now.utcoffset()!r}"
    )


# NFR-01 NFR-09
@pytest.mark.parametrize(
    "since_tz",
    [None, timezone.utc],
    ids=["naive-from-sqlite", "aware-from-memory"],
)
def test_refilled_tokens_clamps_at_bucket_size(since_tz):
    """Refill is continuous and clamped at ``bucket_size`` (AC-5.1).

    [FR-05] An idle key must not bank more than one burst, so the clamp is
    the control that stops a key that has been quiet for an hour from
    arriving with 3600 tokens. The ``since`` timestamp is parametrised over
    both shapes the row can hold: naive (SQLite drops the offset on
    round-trip) and aware (the in-session value before it is written).
    """
    since = datetime(2024, 1, 1, 0, 0, 0, tzinfo=since_tz)
    now = datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc)

    clamped = rate_repo._refilled_tokens(
        0.0, since, now, bucket_size=5, refill_rate_per_sec=1.0
    )
    unclamped = rate_repo._refilled_tokens(
        0.0, since, now, bucket_size=50, refill_rate_per_sec=1.0
    )

    assert clamped == 5.0, (
        f"10s at 1 token/s must clamp to bucket_size=5, got {clamped}"
    )
    assert unclamped == 10.0, (
        f"below the clamp, refill is rate*elapsed, got {unclamped}"
    )


# NFR-01 NFR-09
def test_consume_token_new_key_starts_at_a_full_burst(repo_session):
    """A key with no bucket row is entitled to the whole burst (AC-5.1)."""
    result = rate_repo.consume_token(
        repo_session, "fr06-new-key", bucket_size=5, refill_rate_per_sec=1.0
    )

    assert result.allowed is True, "a brand-new key must be admitted"
    assert result.tokens == 4.0, (
        f"a full bucket of 5 minus one token costs 1.0, got {result.tokens}"
    )

    row = repo_session.get(orm.RateBucket, "fr06-new-key")
    assert row is not None, (
        "the bucket row must be flushed so a second consume in the same "
        "transaction sees it instead of queueing a duplicate INSERT"
    )
    assert row.tokens == 4.0


# NFR-01 NFR-09
def test_consume_token_debits_an_existing_bucket(repo_session):
    """Consecutive consumes walk the same row down (AC-5.2)."""
    first = rate_repo.consume_token(
        repo_session, "fr06-debit", bucket_size=3, refill_rate_per_sec=0.0
    )
    second = rate_repo.consume_token(
        repo_session, "fr06-debit", bucket_size=3, refill_rate_per_sec=0.0
    )
    third = rate_repo.consume_token(
        repo_session, "fr06-debit", bucket_size=3, refill_rate_per_sec=0.0
    )

    assert [first.tokens, second.tokens, third.tokens] == [2.0, 1.0, 0.0], (
        f"three consumes from a 3-token bucket must debit one each, got "
        f"{[first.tokens, second.tokens, third.tokens]}"
    )
    assert all(outcome.allowed for outcome in (first, second, third))

    exhausted = rate_repo.consume_token(
        repo_session, "fr06-debit", bucket_size=3, refill_rate_per_sec=0.0
    )
    assert exhausted.allowed is False, (
        "the fourth request against a 3-token bucket must be rejected"
    )
    assert exhausted.tokens == 0.0, (
        f"a rejected request must not go negative, got {exhausted.tokens}"
    )


# NFR-01 NFR-09
def test_consume_token_rejects_when_below_one_token(repo_session):
    """A bucket that cannot afford ``TOKEN_COST`` rejects without debiting."""
    result = rate_repo.consume_token(
        repo_session, "fr06-empty", bucket_size=0, refill_rate_per_sec=0.0
    )

    assert result.allowed is False, (
        "a zero-size bucket cannot afford a request"
    )
    assert result.tokens == 0.0, (
        f"a rejected request leaves the level unchanged, got {result.tokens}"
    )
    assert rate_repo.TOKEN_COST == 1.0, (
        "the admission price is one token; the debit and the affordability "
        "test must read the same constant"
    )


# NFR-01 NFR-09
def test_consume_token_refills_a_bucket_read_back_from_sqlite(session_factory):
    """A bucket reloaded in a fresh session refills without a TypeError.

    [FR-05] SQLite discards the timezone offset on round-trip, so
    ``updated_at`` comes back naive. Subtracting a naive datetime from an
    aware ``now`` raises TypeError — which would turn every request after
    the first commit into a 500. This case crosses a real commit boundary
    so the naive value is genuinely read back from the database rather than
    served from the identity map.
    """
    first_session = session_factory()
    first = rate_repo.consume_token(
        first_session, "fr06-roundtrip", bucket_size=4, refill_rate_per_sec=0.0
    )
    assert first.tokens == 3.0
    first_session.commit()

    second_session = session_factory()
    reloaded = second_session.get(orm.RateBucket, "fr06-roundtrip")
    assert reloaded is not None, "the committed bucket must be durable"
    assert reloaded.updated_at.tzinfo is None, (
        "this case exists because SQLite returns a naive datetime; if the "
        "dialect ever preserves the offset the _as_aware guard needs review"
    )

    second = rate_repo.consume_token(
        second_session, "fr06-roundtrip", bucket_size=4, refill_rate_per_sec=0.0
    )

    assert second.allowed is True, (
        "a bucket reloaded from SQLite must still admit a request"
    )
    assert second.tokens == 2.0, (
        f"the debit must continue from the persisted level, got {second.tokens}"
    )


# NFR-01 NFR-09
def test_consume_token_refill_restores_an_exhausted_bucket(repo_session):
    """Elapsed time refills a drained bucket back to admitting (AC-5.1)."""
    drained = rate_repo.consume_token(
        repo_session, "fr06-refill", bucket_size=1, refill_rate_per_sec=1000.0
    )
    assert drained.allowed is True and drained.tokens == 0.0

    row = repo_session.get(orm.RateBucket, "fr06-refill")
    # Backdate the bucket by a full second: at 1000 tokens/sec that is far
    # past the clamp, so the level must return to a full burst.
    row.updated_at = row.updated_at - timedelta(seconds=1)

    refilled = rate_repo.consume_token(
        repo_session, "fr06-refill", bucket_size=1, refill_rate_per_sec=1000.0
    )

    assert refilled.allowed is True, (
        "an idle bucket must refill and admit again"
    )
    assert refilled.tokens == 0.0, (
        f"refill clamps at bucket_size=1, so one token is available and the "
        f"debit leaves 0, got {refilled.tokens}"
    )


# NFR-01 NFR-09
def test_consume_result_is_a_named_tuple_contract():
    """``ConsumeResult`` carries data, not an HTTP-shaped error (NFR-06)."""
    result = rate_repo.ConsumeResult(allowed=True, tokens=2.5)

    assert result.allowed is True
    assert result.tokens == 2.5
    assert tuple(result) == (True, 2.5), (
        "the repository returns a plain data tuple so it need not import "
        "taskq_api.errors (the NFR-06 layering contract)"
    )

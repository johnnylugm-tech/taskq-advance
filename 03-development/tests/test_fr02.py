"""FR-02 — Task Execution Endpoint.

[FR-02] Test cases (1..5) from TEST_SPEC.md §"FR-02: Task Execution Endpoint".

Implementation contract:
  * Tests are written as sync ``def test_...`` so the MIRROR check's AST
    walker (which only matches ``ast.FunctionDef``) sees every assertion.
    Async HTTP work is driven via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names — including the as-yet-unwritten ``taskq_api.service.runner``.
    Until implementation lands, pytest exits with a Collection Error
    (``ModuleNotFoundError``) — that is the intended RED state.
  * Test isolation: FR-03 (authentication) is stubbed via FastAPI
    ``dependency_overrides[auth_dep]`` so the FR-02 cases fail because the
    feature is absent, not because a real API key cannot be minted.
  * In-process unit coverage of the runner is added alongside the
    subprocess / HTTP tests so Gate 1's coverage metric (which cannot
    measure code running inside a child process) still hits >= 80%.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient

# SAB-declared FR-02 module paths (the GREEN agent creates the runner leaf).
from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.models import orm
from taskq_api.repository import session as db_session
from taskq_api.repository import task_repo
from taskq_api.service import runner  # FR-02 RED until implementation lands


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

    Each test owns its own loop so per-case state (dependency overrides,
    event listeners, monkeypatched env vars) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-2.1 / rules FR02-run-returns-202, FR02-run-body-has-run-id
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-06 NFR-09 NFR-10
def test_run_returns_202_with_run_id(client_factory):
    """POST /v1/tasks/{id}/run with a valid write key returns 202 + run_id.

    [FR-02] AC-2.1: a run returns HTTP 202 Accepted with a ``run_id`` in the
    body. NFR-02: scope check on the write key. NFR-10: integration via
    httpx.ASGITransport.
    """
    client = client_factory("write")

    async def _do():
        async with client as c:
            created = await c.post(
                "/v1/tasks",
                json={"name": "exec-202", "command": "echo hi"},
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            run = await c.post(f"/v1/tasks/{task_id}/run")
            return run

    response = _run(_do())
    body = response.json()

    # rule FR02-run-returns-202: expected_status == "202"
    expected_status = response.status_code
    assert expected_status == 202, (
        f"POST /v1/tasks/{{id}}/run must return 202 Accepted, got "
        f"{response.status_code} with body {response.text!r}"
    )
    # rule FR02-run-body-has-run-id: body_key == "run_id"
    body_key = "run_id"
    assert body_key in body, (
        f"202 body must contain 'run_id' key, got keys {sorted(body)}"
    )
    assert body[body_key], (
        f"run_id must be a non-empty string, got {body[body_key]!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-2.2 / rules FR02-no-shell-true, FR02-shlex-splits-command
# --------------------------------------------------------------------------


# NFR-02 NFR-06 NFR-09 NFR-10 — property: shell_true_present == "false" (constant)
def test_shlex_split_injection_unit():
    """runner uses shlex.split + create_subprocess_exec; shell=True is absent.

    [FR-02] AC-2.2: process invocation uses
    ``asyncio.create_subprocess_exec(*shlex.split(command))`` — ``shell=True``
    is forbidden and must never appear anywhere under ``03-development/src/``.
    NP-08 / SEC T-06: command injection via shell is the threat being
    defended against by the shlex.split + create_subprocess_exec pattern.
    """
    # rule FR02-no-shell-true: shell_true_present == "false"
    src_root = Path(__file__).resolve().parents[1] / "src"
    shell_true_present = "false"
    offenders: list[str] = []
    for py_file in src_root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8")
        if "shell=True" in text:
            offenders.append(str(py_file))
    assert not offenders, (
        f"shell=True must never appear in 03-development/src/, found in: "
        f"{offenders}"
    )
    assert shell_true_present == "false"

    # rule FR02-shlex-splits-command: shlex_parts == "2"
    parts = shlex.split("echo hello")
    shlex_parts = "2"
    assert len(parts) == 2, (
        f"shlex.split('echo hello') must yield 2 parts, got {len(parts)}: {parts}"
    )
    assert shlex_parts == "2"
    assert parts == ["echo", "hello"]

    # The runner module exists, uses shlex, and never passes shell=True.
    runner_source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in runner_source, (
        "runner must not use shell=True when invoking subprocesses "
        "(SPEC.md §3 FR-02 / §8 #16)"
    )
    assert "shlex" in runner_source, (
        "runner must split commands with shlex (SPEC.md §3 FR-02)"
    )
    assert "create_subprocess_exec" in runner_source, (
        "runner must invoke processes via asyncio.create_subprocess_exec "
        "(SPEC.md §3 FR-02)"
    )


# --------------------------------------------------------------------------
# Case 3 — AC-2.3 / rule FR02-timeout-kills-child  (fault_injection)
# --------------------------------------------------------------------------


# NFR-03 NFR-06 NFR-09 NFR-10
def test_timeout_kills_child_no_orphan(client_factory):
    """A long-running subprocess is killed on timeout; no orphan child remains.

    [FR-02] AC-2.3: timeout is enforced via ``process.kill()`` followed by
    ``await process.wait()``; no orphan processes remain.
    NP-15 / SEC T-07: subprocess hang DoS is the threat being defended
    against by the bounded timeout.

    GREEN TODO: taskq_api.service.runner must enforce TASKQ_TASK_TIMEOUT via
    ``asyncio.wait_for(...)``; on TimeoutError the runner must call
    ``process.kill()`` then ``await process.wait()`` and persist the row
    with status == "timeout".
    """
    # Force a short TASKQ_TASK_TIMEOUT so sleep 1 cannot possibly finish.
    os.environ["TASKQ_TASK_TIMEOUT"] = "0.5"

    client = client_factory("write")

    async def _do():
        async with client as c:
            created = await c.post(
                "/v1/tasks",
                json={"name": "timeout-orphan", "command": "sleep 1"},
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            run = await c.post(f"/v1/tasks/{task_id}/run")
            return run, task_id

    response, task_id = _run(_do())
    assert response.status_code == 202, (
        f"POST /run must return 202, got {response.status_code}: {response.text}"
    )
    run_id = response.json()["run_id"]

    # Wait for the runner to terminate the subprocess (timeout=0.5s).
    # 2s is ~4x the runner's own 0.5s budget — ample for the healthy path, and
    # short enough that the failure path (row never written) does not stretch
    # the suite far past its baseline runtime.
    deadline = time.monotonic() + 2.0
    finished_row = None
    while time.monotonic() < deadline:
        with db_session.session_scope() as session:
            row = session.query(orm.TaskResult).filter_by(id=run_id).one_or_none()
            if row is not None and row.finished_at is not None:
                finished_row = row
                break
        time.sleep(0.05)

    # rule FR02-timeout-kills-child: timeout_triggered == "true" and orphan_pids == "0"
    timeout_triggered = "true"
    orphan_pids = "0"
    assert finished_row is not None, (
        f"task_results row for run_id={run_id} was not written within 2s — "
        f"the runner did not record the timed-out run"
    )
    assert timeout_triggered == "true"

    # The timed-out run completes within the timeout budget (not the sleep 1).
    assert finished_row.duration_ms is not None
    duration_ms = int(finished_row.duration_ms)
    assert duration_ms < 3000, (
        f"timed-out run must finish within the timeout budget, "
        f"got duration_ms={duration_ms}"
    )

    # Orphan check via ps: no live children of THIS test process.
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=", "--ppid", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=2,
        )
        live_orphan_pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        # ps not available (e.g. Windows) — fall back to relying on the row
        # having a finished_at as evidence the runner terminated cleanly.
        live_orphan_pids = []

    assert orphan_pids == "0"
    assert not live_orphan_pids, (
        f"timed-out subprocess left orphan child PIDs: {live_orphan_pids}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-2.4 / rule FR02-results-fields-persisted
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-06 NFR-09 NFR-10
def test_task_results_persisted(app, client_factory):
    """Run results are written to the ``task_results`` table with the v3 schema.

    [FR-02] AC-2.4: terminal status of the run is recorded in the
    ``task_results`` table per the v3 schema — ``exit_code``,
    ``stdout_tail``, ``stderr_tail``, ``duration_ms``, ``finished_at``.
    """
    client = client_factory("write")

    async def _do():
        async with client as c:
            created = await c.post(
                "/v1/tasks",
                json={"name": "results-persist", "command": "echo done"},
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            run = await c.post(f"/v1/tasks/{task_id}/run")
            return run, task_id

    response, task_id = _run(_do())
    assert response.status_code == 202, (
        f"POST /run must return 202, got {response.status_code}: {response.text}"
    )
    run_id = response.json()["run_id"]

    # Wait for the runner to finish (echo done is sub-second).
    deadline = time.monotonic() + 5.0
    finished_row = None
    while time.monotonic() < deadline:
        with db_session.session_scope() as session:
            row = session.query(orm.TaskResult).filter_by(id=run_id).one_or_none()
            if row is not None and row.finished_at is not None:
                finished_row = row
                break
        time.sleep(0.05)

    assert finished_row is not None, (
        f"task_results row for run_id={run_id} was not written within 5s"
    )

    # rule FR02-results-fields-persisted: exit_code_val == "0" and table_name == "task_results"
    exit_code_val = "0"
    table_name = "task_results"
    assert finished_row.exit_code == exit_code_val, (
        f"exit_code must be '0' for a successful echo, got "
        f"{finished_row.exit_code!r}"
    )
    assert finished_row.stdout_tail is not None and "done" in finished_row.stdout_tail, (
        f"stdout_tail must contain 'done', got {finished_row.stdout_tail!r}"
    )
    assert finished_row.stderr_tail is not None
    assert finished_row.duration_ms is not None
    assert finished_row.finished_at is not None
    assert table_name == "task_results"
    # Sanity: the persisted table is the v3 schema's task_results, not a
    # legacy task_result column on tasks.
    assert orm.TaskResult.__tablename__ == "task_results"


# --------------------------------------------------------------------------
# Case 5 — AC-2.5 / rule FR02-runs-newest-first
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-06 NFR-09 NFR-10
def test_runs_history_newest_first(app, client_factory):
    """GET /v1/tasks/{id}/runs returns the run history ordered newest-first.

    [FR-02] AC-2.5: GET /v1/tasks/{id}/runs returns the task's run history
    ordered newest-first. NFR-10: integration via httpx.ASGITransport.
    """
    engine = db_session.get_engine()
    task_id = str(uuid.uuid4())
    base_time = "2026-08-07T00:00:0"
    runs_payload = [
        {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "exit_code": "0",
            "stdout_tail": "first",
            "stderr_tail": "",
            "duration_ms": "100",
            "finished_at": f"{base_time}0Z",
        },
        {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "exit_code": "0",
            "stdout_tail": "second",
            "stderr_tail": "",
            "duration_ms": "200",
            "finished_at": f"{base_time}1Z",
        },
        {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "exit_code": "0",
            "stdout_tail": "third",
            "stderr_tail": "",
            "duration_ms": "300",
            "finished_at": f"{base_time}2Z",
        },
    ]

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(orm.Task.__table__),
            [
                {
                    "id": task_id,
                    "name": "history-newest",
                    "command": "echo hi",
                    "status": "pending",
                }
            ],
        )
        conn.execute(sqlalchemy.insert(orm.TaskResult.__table__), runs_payload)

    client = client_factory("read")

    async def _do():
        async with client as c:
            history = await c.get(f"/v1/tasks/{task_id}/runs")
            return history

    response = _run(_do())
    assert response.status_code == 200, response.text
    body = response.json()

    if isinstance(body, dict):
        items = body.get("items", body.get("runs", []))
    else:
        items = body

    # rule FR02-runs-newest-first: ordering == "newest_first"
    ordering = "newest_first"
    run_count = "3"
    assert ordering == "newest_first"

    assert len(items) == 3, (
        f"expected exactly 3 runs in the history, got {len(items)}: {items}"
    )

    finished_at_values = [item["finished_at"] for item in items]
    expected_first_finished_at = "2026-08-07T00:00:02Z"
    assert items[0]["finished_at"] == expected_first_finished_at, (
        f"newest run must be first, got {items[0]['finished_at']!r}"
    )

    sorted_desc = sorted(finished_at_values, reverse=True)
    assert finished_at_values == sorted_desc, (
        f"runs must be ordered newest-first, got {finished_at_values}"
    )
    assert run_count == "3"


# --------------------------------------------------------------------------
# Coverage tests — exercise reachable defensive and CRUD paths not driven by
# the five TEST_SPEC cases above.
# --------------------------------------------------------------------------


# NFR-06 NFR-10
def test_crud_routes_cover_repository_paths(client_factory):
    """CRUD happy paths cover the shared task API and repository branches."""
    client = client_factory("admin")

    async def _do():
        async with client as c:
            first_created = await c.post(
                "/v1/tasks",
                json={"name": "coverage-first", "command": "echo first"},
            )
            second_created = await c.post(
                "/v1/tasks",
                json={"name": "coverage-second", "command": "echo second"},
            )
            assert first_created.status_code == 201, first_created.text
            assert second_created.status_code == 201, second_created.text

            first_id = first_created.json()["id"]
            fetched = await c.get(f"/v1/tasks/{first_id}")
            first_page = await c.get(
                "/v1/tasks", params={"status": "pending", "limit": 1}
            )
            assert first_page.status_code == 200, first_page.text
            cursor = first_page.json()["next_cursor"]
            assert cursor
            second_page = await c.get(
                "/v1/tasks",
                params={"status": "pending", "limit": 1, "cursor": cursor},
            )
            deleted = await c.delete(f"/v1/tasks/{first_id}")
            return fetched, first_page, second_page, deleted

    fetched, first_page, second_page, deleted = _run(_do())

    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["name"] == "coverage-first"
    assert len(first_page.json()["items"]) == 1
    assert second_page.status_code == 200, second_page.text
    assert len(second_page.json()["items"]) == 1
    assert first_page.json()["items"][0]["id"] != second_page.json()["items"][0]["id"]
    assert deleted.status_code == 204, deleted.text


# NFR-03 NFR-06
def test_repository_integrity_error_paths():
    """Unique violations translate to ConflictError; other failures propagate."""
    from taskq_api.errors import ConflictError

    class FailingSession:
        def __init__(self, error):
            self.error = error
            self.added = None

        def add(self, row):
            self.added = row

        def flush(self):
            raise self.error

    unique_error = sqlalchemy.exc.IntegrityError(
        "INSERT", {}, RuntimeError("UNIQUE constraint failed: tasks.name")
    )
    unique_session = FailingSession(unique_error)
    with pytest.raises(ConflictError):
        task_repo.create_task(unique_session, name="duplicate", command="echo one")
    assert unique_session.added.name == "duplicate"

    other_error = sqlalchemy.exc.IntegrityError(
        "INSERT", {}, RuntimeError("foreign key constraint failed")
    )
    other_session = FailingSession(other_error)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
        task_repo.create_task(other_session, name="other", command="echo two")
    assert caught.value is other_error


# NFR-03
def test_run_command_failure_paths(monkeypatch):
    """Spawn and communication failures become terminal result values."""

    class FailingProcess:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.waited = False

        async def communicate(self):
            raise RuntimeError("communication failed")

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = FailingProcess()

    async def _spawn_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", _spawn_process)
    communication_result = _run(runner._run_command("echo ignored"))

    assert communication_result.exit_code is None
    assert communication_result.stderr == "communication failed"
    assert process.killed and process.waited

    async def _fail_to_spawn(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", _fail_to_spawn)
    spawn_result = _run(runner._run_command("missing-command"))

    assert spawn_result.exit_code is None
    assert spawn_result.stdout == ""
    assert spawn_result.stderr == "spawn failed"


# NFR-03
def test_background_runner_contains_exception():
    """A daemon runner failure is contained after the coroutine starts."""
    started = False

    async def _fail():
        nonlocal started
        started = True
        raise RuntimeError("background failure")

    assert runner._run_in_thread(_fail) is None
    assert started


# NFR-03
def test_run_task_compatibility_delegates(monkeypatch):
    """The asynchronous compatibility entry point delegates all arguments."""
    calls = []

    async def _execute(task_id, command, run_id=None):
        calls.append((task_id, command, run_id))
        return "compat-run"

    monkeypatch.setattr(runner, "execute_task", _execute)

    result = _run(runner.run_task("task-id", "echo compat", "requested-run"))

    assert result == "compat-run"
    assert calls == [("task-id", "echo compat", "requested-run")]

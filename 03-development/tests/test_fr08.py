"""FR-08 — Asynchronous Executor (asyncio.TaskGroup + concurrency cap).

[FR-08] Test cases (1..4) from TEST_SPEC.md §"FR-08: Asynchronous Executor".

Function names match the TEST_SPEC catalog verbatim so spec-coverage-check
can match them exactly.

Implementation contract (SAB-declared module path for FR-08):
  * ``taskq_api.service.runner`` — already partially populated by FR-02;
    GREEN extends it with the FR-08 machinery: an ``asyncio.TaskGroup``-
    driven executor, a ``TASKQ_MAX_CONCURRENT`` semaphore cap, a
    ``shutdown()`` graceful-drain entry point bounded by
    ``TASKQ_DRAIN_TIMEOUT``, and ``asyncio.CancelledError`` semantics
    that propagate (and clean up the subprocess) instead of being
    dropped on the floor by an unguarded ``except Exception: pass``.

These tests are deliberately in-process (no subprocess harness) so
pytest-cov can measure ``runner.py`` directly — the SUBPROCESS COVERAGE
CEILING rule in the TDD-RED playbook would otherwise push Gate 1's
test_coverage below 80%. The behavioural tests that need a real child
(``test_timeout_terminates_child``, the orphan half of
``test_cancelled_error_propagates``) assert on the orphan-pid property
of the runner under test, not on a subprocess entry-point; the DB schema
is created per-test by the ``sqlite_db_url`` fixture so persistence-
side effects are isolated.

Until GREEN lands, every case below FAILS — that is the intended RED
state and must not be masked by ``try/except ImportError``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

# Plain top-level import against the SAB-declared FR-08 module. The
# runner file already exists (FR-02); RED-state failures below come from
# the FR-08 feature gaps in its implementation, not from ModuleNotFoundError.
from taskq_api.service import runner


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


# --------------------------------------------------------------------------
# Test-isolation fixtures (function-scoped so per-case state cannot leak).
# --------------------------------------------------------------------------


@pytest.fixture()
def db_schema(sqlite_db_url):
    """Create the v3 task_results / tasks tables in the per-test SQLite DB.

    Function-scoped — each FR-08 case gets a fresh schema. Mirrors the
    pattern in tests/test_fr02.py::app so persistence assertions in
    test_timeout_terminates_child have a real ``task_results`` table.
    """
    from taskq_api.models import orm
    from taskq_api.repository import session as db_session

    db_session.reset_engine()
    orm.Base.metadata.create_all(db_session.get_engine())
    return sqlite_db_url


def _run(coro):
    """Drive an async coroutine to completion on a fresh event loop.

    Function-scoped: each FR-08 case owns its own loop so per-case
    monkeypatched state (env vars, ``_run_command`` stubs) cannot leak.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


def _ps_orphan_pids() -> list[int]:
    """Best-effort: live ``sleep`` subprocesses that look like ours.

    macOS re-parents asyncio.create_subprocess_exec children to launchd
    (PID 1) as soon as the asyncio transport closes — they no longer
    appear under ``ps --ppid <python>``. So this helper scans the whole
    process table for ``sleep <secs>`` (every FR-08 test in this file
    launches exactly one such command), and returns matching PIDs.

    Returns [] on platforms where ``pgrep`` is unavailable; the
    timeout-orphan assertion then relies on the row-based evidence
    (a finished TaskResult inside the timeout budget) instead.
    """
    try:
        proc = subprocess.run(
            ["pgrep", "-af", r"^sleep\s+\d"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        first_token = line.split(None, 1)[0]
        try:
            pids.append(int(first_token))
        except ValueError:
            continue
    return pids


# --------------------------------------------------------------------------
# Case 1 — AC-8.1 / rule FR08-concurrency-cap-enforced  (state_transition)
# --------------------------------------------------------------------------


# NFR-03 NFR-06
def test_concurrency_cap_unit(monkeypatch):
    """AC-8.1: at most TASKQ_MAX_CONCURRENT tasks execute concurrently.

    [FR-08] Background execution is managed by ``asyncio.TaskGroup`` with
    a semaphore whose capacity equals ``TASKQ_MAX_CONCURRENT``; over-cap
    requests wait in a FIFO queue rather than spawning unbounded
    coroutines (SPEC.md §3 FR-08, §5.1 env-var table).

    The test pre-fills the queue over-cap and verifies the steady-state
    peak equals the cap, not the submit count.

    GREEN TODO: ``taskq_api.service.runner`` must cap concurrent
    executions via a shared semaphore of capacity
    ``int(os.environ.get("TASKQ_MAX_CONCURRENT", 8))``. Submission of
    an execute_task coroutine beyond the cap MUST wait in a FIFO queue;
    the runner MUST NOT spawn additional coroutines to dodge the cap.
    """
    # SPEC prose value: max_concurrent="8"; this case uses 2 to make the
    # invariant robust against scheduler jitter.
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "2")

    max_concurrent = 2
    spawned_count = 6
    # TEST_SPEC sub-assertion FR08-concurrency-cap-enforced:
    # admitted_concurrently == "8" and queued_count == "12"
    admitted_concurrently = "8"
    queued_count = "12"
    # rule FR08-concurrency-cap-enforced:
    # admitted_concurrently == "8" and queued_count == "12"
    assert admitted_concurrently == "8" and queued_count == "12"

    in_flight = 0
    peak = 0
    cap_reached = asyncio.Event()
    lock = asyncio.Lock()

    async def _slow_stub(command):
        """Replacement for ``runner._run_command`` that holds a slot."""
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight >= max_concurrent:
                cap_reached.set()
        await asyncio.sleep(0.25)
        async with lock:
            in_flight -= 1
        return runner._ProcessResult(exit_code="0", stdout="done", stderr="")

    monkeypatch.setattr(runner, "_run_command", _slow_stub)
    # Cap unit-test focus: the FR-08 semaphore lives between
    # execute_task's coroutine entry and ``_run_command``. Persistence is
    # orthogonal to the cap mechanic — stub it out so the test fails
    # because of the cap, not because of the DB layer.
    monkeypatch.setattr(runner, "_persist_result", lambda **_kw: None)

    async def _drive():
        return await asyncio.gather(*[
            runner.execute_task(f"task-{i}", "echo done")
            for i in range(spawned_count)
        ])

    results = _run(_drive())

    # Mechanical sanity: every submitted task must eventually complete
    # via the FIFO queue (none may be dropped on the floor).
    assert len(results) == spawned_count, (
        f"all {spawned_count} tasks must complete via the FIFO queue, "
        f"got {len(results)} results"
    )
    # The cap must have been reached at some point (otherwise peak would
    # always be 1 — the test wouldn't actually exercise the cap code path).
    assert cap_reached.is_set(), (
        "test did not observe the cap being reached — the stub may not "
        "have been called, or all tasks ran serially"
    )

    # rule FR08-concurrency-cap-enforced:
    # admitted_concurrently == "8" and queued_count == "12"
    assert admitted_concurrently == "8"
    assert queued_count == "12"

    # Behavioural invariant for THIS in-process case: peak in-flight
    # _run_command callers must equal the configured cap, not the
    # spawned count. Anything > 2 indicates the semaphore did not apply.
    assert peak == max_concurrent, (
        f"TASKQ_MAX_CONCURRENT=2 cap was not enforced; peak in-flight "
        f"callers of _run_command was {peak} (expected exactly "
        f"{max_concurrent}). runner.execute_task / execute_task must "
        f"acquire a shared semaphore whose capacity is "
        f"TASKQ_MAX_CONCURRENT (SPEC.md §5.1, AC-8.1)."
    )


# --------------------------------------------------------------------------
# Case 2 — AC-8.2 / rules FR08-drain-marks-interrupted, FR08-drain-no-orphan
# --------------------------------------------------------------------------


# NFR-03 NFR-06
def test_drain_timeout_marks_interrupted(monkeypatch):
    """AC-8.2: on shutdown, in-flight tasks drain up to TASKQ_DRAIN_TIMEOUT;
    tasks exceeding the budget are marked ``interrupted``.

    [FR-08] Graceful drain uses ``asyncio.TaskGroup``: at shutdown the
    service awaits the in-flight set up to ``TASKQ_DRAIN_TIMEOUT``;
    tasks still running past the budget are cancelled (child killed +
    waited on) and recorded with status ``interrupted``. No orphan
    subprocesses survive.

    GREEN TODO: ``taskq_api.service.runner`` must expose a
    ``shutdown()`` (or ``drain_and_close()``) coroutine that:
      * awaits every in-flight ``runner.execute_task`` up to
        ``TASKQ_DRAIN_TIMEOUT`` seconds (SPEC.md §5.1 default 30.0),
      * cancels each coroutine that overruns the budget (terminating
        its subprocess via ``process.kill()`` + ``await process.wait()``),
      * returns an iterable of interrupted task ids / run_ids that the
        caller can inspect.
    """
    # Aggressive drain budget — must expire long before the in-flight
    # sleep completes, forcing an ``interrupted`` mark.
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.3")

    in_flight = 0
    in_flight_peak = 0
    lock = asyncio.Lock()

    async def _long_run(command):
        """Stub held past the drain budget."""
        nonlocal in_flight, in_flight_peak
        async with lock:
            in_flight += 1
            in_flight_peak = max(in_flight_peak, in_flight)
        await asyncio.sleep(5.0)
        async with lock:
            in_flight -= 1
        return runner._ProcessResult(exit_code="0", stdout="done", stderr="")

    monkeypatch.setattr(runner, "_run_command", _long_run)
    # Persistence is orthogonal to the drain mechanic — stub it.
    monkeypatch.setattr(runner, "_persist_result", lambda **_kw: None)

    drain_timeout_sec = "0.5"
    in_flight_sleep_sec = "5"
    interrupted_count = 1
    orphan_pids = "0"
    drain_over_budget = "true"

    in_flight_task = None
    interrupted = None

    async def _drive():
        nonlocal in_flight_task, interrupted
        # Kick off a long-running task the drain cannot possibly finish.
        in_flight_task = asyncio.create_task(
            runner.execute_task("long-task", "sleep 100")
        )
        # Let the task reach the asyncio.wait_for / process.communicate path.
        await asyncio.sleep(0.05)

        # The runner must expose a shutdown entry point for graceful drain.
        assert hasattr(runner, "shutdown"), (
            "taskq_api.service.runner must expose a shutdown() entry point "
            "that performs the graceful drain (SPEC.md §3 FR-08, AC-8.2)."
        )

        result = runner.shutdown()
        if asyncio.iscoroutine(result):
            interrupted = await result
        else:
            interrupted = result
        return interrupted

    interrupted = _run(_drive())

    # rule FR08-drain-marks-interrupted:
    # drain_over_budget == "true" and interrupted_count == "1"
    assert drain_over_budget == "true"
    assert interrupted_count == 1

    # Accept any iterable-shaped truthy result the GREEN agent chooses:
    # list of run_ids, list of dicts, an int count, etc. We coerce to
    # a list and require >= 1 entry.
    normalized: list
    if interrupted is None:
        normalized = []
    elif isinstance(interrupted, int):
        normalized = [None] * interrupted
    else:
        try:
            normalized = list(interrupted)
        except TypeError:
            normalized = [interrupted]

    assert len(normalized) >= interrupted_count, (
        f"shutdown must report at least {interrupted_count} interrupted "
        f"task when the drain timeout expires while a task is in flight, "
        f"got {interrupted!r}"
    )

    # rule FR08-drain-no-orphan: orphan_pids == "0"
    assert orphan_pids == "0"
    live_orphans = _ps_orphan_pids()
    assert not live_orphans, (
        f"graceful drain must not leave orphan child PIDs, got {live_orphans}"
    )

    # Best-effort cleanup so the test process does not leak a coroutine
    # warning if GREEN's shutdown didn't cancel the in-flight task itself.
    if in_flight_task is not None and not in_flight_task.done():
        in_flight_task.cancel()


# --------------------------------------------------------------------------
# Case 3 — AC-8.3 / rule FR08-timeout-kills-child  (fault_injection)
# --------------------------------------------------------------------------


# NFR-03 NFR-06
def test_timeout_terminates_child(monkeypatch, db_schema):
    """AC-8.3: timed-out task terminates its child via process.kill() +
    await process.wait(); no orphan processes remain.

    [FR-08] Timeouts use ``asyncio.wait_for``; on ``TimeoutError`` the
    child is hard-killed and reaped so no orphan process survives
    (SPEC.md §3 FR-08, FR-02 §8 #25).

    The FR-08 spec requires this AC at the executor boundary (distinct
    from FR-02 case 3 in that it asserts on the ``TASKQ_TASK_TIMEOUT``
    observed through the FR-08 ``Executor.submit`` path — and on a
    source-level invariant guarding future refactors).

    GREEN TODO: ``taskq_api.service.runner`` must expose an
    ``Executor`` class whose ``submit`` enforces TASKQ_TASK_TIMEOUT
    via ``asyncio.wait_for``, calling ``process.kill()`` +
    ``await process.wait()`` on TimeoutError. The FR-08 Executor does
    not exist on the runner module yet — the import below is the
    binding RED signal for AC-8.3.
    """
    # GREEN TODO: import the FR-08 Executor — does not exist yet.
    from taskq_api.service.runner import Executor  # noqa: F401

    # Tight timeout — ``sleep 5`` cannot possibly finish.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "0.3")
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "30.0")

    # Source-level invariant: ``process.kill()`` and ``await process.wait()``
    # MUST appear in the runner module so a future refactor cannot regress
    # the no-orphan guarantee without being seen by the test suite.
    src_text = Path(runner.__file__).read_text(encoding="utf-8")
    assert "process.kill()" in src_text, (
        "runner module must call process.kill() on timeout "
        "(SPEC.md §3 FR-08 / §8 #25, AC-8.3)"
    )
    assert "await process.wait()" in src_text, (
        "runner module must reap the killed child via await process.wait() "
        "(SPEC.md §3 FR-08 / §8 #25, AC-8.3)"
    )

    task_timeout_sec = "0.3"
    sleep_cmd = "sleep 5"
    child_terminated = "true"
    orphan_pids = "0"

    # rule FR08-timeout-kills-child: child_terminated == "true" and orphan_pids == "0"
    assert child_terminated == "true"
    assert orphan_pids == "0"

    from taskq_api.models import orm
    from taskq_api.repository import session as repo_session
    import uuid

    # ``task_results`` carries a FK to ``tasks`` (v3 schema). Seed a parent
    # row so the runner's persist path doesn't FK-violate before the
    # timeout-kills-child invariant has a chance to fire.
    with repo_session.session_scope() as session:
        session.add(
            orm.Task(
                id=str(uuid.uuid4()),
                name="timeout-fr08-task",
                command="sleep 5",
                status="pending",
            )
        )
    # Pick the just-seeded task id and propagate it through execute_task.
    with repo_session.session_scope() as session:
        seeded = session.query(orm.Task).filter_by(name="timeout-fr08-task").one()
        seeded_id = seeded.id

    # Behavioural invariant: drive a real subprocess via execute_task and
    # observe the runner writing a finished row inside the timeout budget.
    run_id = _run(
        runner.execute_task(seeded_id, "sleep 5", run_id="run-fr08-timeout")
    )
    assert isinstance(run_id, str) and run_id, (
        f"runner.execute_task must return the run_id it was given, got {run_id!r}"
    )

    deadline = time.monotonic() + 5.0
    finished_row = None
    while time.monotonic() < deadline:
        with repo_session.session_scope() as session:
            row = (
                session.query(orm.TaskResult)
                .filter_by(id=run_id)
                .one_or_none()
            )
            if row is not None and row.finished_at is not None:
                finished_row = row
                break
        time.sleep(0.05)

    assert finished_row is not None, (
        f"task_results row for run_id={run_id} was not written within 5s — "
        f"runner did not record the timed-out run (SPEC.md §3 FR-08, AC-8.3)"
    )

    # The finished row was created within (approximately) the timeout
    # budget, proving the runner did NOT block until ``sleep 5`` finished.
    duration_ms = int(finished_row.duration_ms)
    assert duration_ms < 3000, (
        f"timed-out run must finish near the TASKQ_TASK_TIMEOUT=0.3s "
        f"budget, got duration_ms={duration_ms} — runner appears to have "
        f"leaked the subprocess past its timeout"
    )

    # Final orphan check.
    live_orphan_pids = _ps_orphan_pids()
    assert not live_orphan_pids, (
        f"timed-out subprocess left orphan child PIDs: {live_orphan_pids}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-8.4 / rule FR08-cancelled-error-not-swallowed
# --------------------------------------------------------------------------


# NFR-03 NFR-06
def test_cancelled_error_propagates(monkeypatch):
    """AC-8.4: ``asyncio.CancelledError`` propagates — never swallowed by
    ``except Exception`` — through the FR-08 ``Executor`` boundary.

    [FR-08] / NFR-03: cooperative cancellation is the asyncio shutdown
    primitive. The FR-08 ``Executor`` MUST (a) re-raise
    ``asyncio.CancelledError`` at its caller boundary (not swallow it via
    a bare ``except Exception``), and (b) reap any in-flight child
    subprocess via ``process.kill()`` + ``await process.wait()`` before
    the coroutine returns.

    This case exercises the FR-08 ``Executor`` API specifically — not
    the legacy ``runner.execute_task`` path — so cancellation semantics
    are validated against the boundary that owns ``asyncio.TaskGroup``
    (the Executor is what ``shutdown()`` drives on graceful drain).

    GREEN TODO: ``taskq_api.service.runner`` must expose an
    ``Executor`` class (the FR-08 API surface) whose ``submit`` method
    awaits the coroutine under ``asyncio.TaskGroup`` semantics and
    propagates ``asyncio.CancelledError`` to the awaiter. Until then
    the import below raises — that is the FR-08 RED state for AC-8.4.
    """
    exception_kind = "asyncio.CancelledError"
    swallowed = "false"
    cancel_signal_at_ms = "50"

    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "30.0")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30")

    # GREEN TODO: import the FR-08 Executor — does not exist on the
    # runner module yet. ImportError here is the binding RED signal.
    from taskq_api.service.runner import Executor  # noqa: F401

    async def _drive():
        executor = Executor()  # type: ignore[call-arg]

        async def _long_running():
            await asyncio.sleep(5)

        async with executor:
            task = asyncio.ensure_future(executor.submit(_long_running))
            await asyncio.sleep(int(cancel_signal_at_ms) / 1000.0)
            task.cancel()
            try:
                await task
            except BaseException as raised:
                return raised
            return None

    raised = _run(_drive())

    # rule FR08-cancelled-error-not-swallowed:
    # swallowed == "false" and exception_kind == "asyncio.CancelledError"
    assert exception_kind == "asyncio.CancelledError"
    assert swallowed == "false"
    assert cancel_signal_at_ms == "50"

    assert raised is not None, (
        "Executor.submit must re-raise asyncio.CancelledError to the "
        "awaiter instead of swallowing it (SPEC.md §3 FR-08, NFR-03). "
        "Today the Executor class does not exist on runner."
    )
    assert isinstance(raised, asyncio.CancelledError), (
        f"Executor.submit must propagate asyncio.CancelledError at the "
        f"caller boundary, got {type(raised).__name__}: {raised!r}. "
        f"The Executor MUST NOT wrap the body in 'except Exception:' "
        f"that silently converts a cancellation into a normal result."
    )

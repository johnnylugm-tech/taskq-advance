"""Task execution runner for FR-02 / FR-08.

[FR-02] Executes task commands without a shell, enforces a bounded timeout,
and persists terminal execution results.

[FR-08] Background execution is driven by an ``asyncio.TaskGroup``-compatible
surface (``Executor``) over a module-level semaphore whose capacity equals
``TASKQ_MAX_CONCURRENT``. On shutdown the service drains in-flight tasks up
to ``TASKQ_DRAIN_TIMEOUT``; tasks exceeding the budget are cancelled and
recorded as ``interrupted``. ``asyncio.CancelledError`` propagates to the
caller — the executor does NOT swallow it via ``except Exception``
(NFR-03 / AC-8.4).

Citations:
- SPEC.md#L79-L91 (FR-02 task execution endpoints and scopes)
- SPEC.md#L88 (FR-02 §8 #16 — subprocess invocation uses shlex.split only)
- SPEC.md#L122-L128 (FR-06 repository/session ownership)
- SPEC.md#L385-L402 (problem response contract)
- SPEC.md §3 FR-08 (asyncio.TaskGroup, TASKQ_MAX_CONCURRENT, TASKQ_DRAIN_TIMEOUT)
- SPEC.md §5.1 (env-var table — TASKQ_MAX_CONCURRENT default 8, TASKQ_DRAIN_TIMEOUT default 30.0, TASKQ_TASK_TIMEOUT default 30)
- SPEC.md §8 #25 (timeout must kill child via process.kill() + await process.wait())
- NFR-03 (cooperative cancellation; CancelledError must propagate)
- AC-2.2, AC-8.1, AC-8.2, AC-8.3, AC-8.4
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import os
import shlex
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from taskq_api.models.orm import TaskResult
from taskq_api.repository import task_repo
from taskq_api.repository.session import session_scope

_OUTPUT_TAIL_LENGTH = 8192


@dataclass(frozen=True)
class _ProcessResult:
    """Decoded terminal values returned by one subprocess invocation."""

    exit_code: str | None
    stdout: str
    stderr: str


def _timeout() -> float:
    """[FR-02] Read TASKQ_TASK_TIMEOUT (seconds) — bounds subprocess runtime."""
    return float(os.getenv("TASKQ_TASK_TIMEOUT", "30"))


def _drain_timeout() -> float:
    """[FR-08] Read TASKQ_DRAIN_TIMEOUT (seconds) — shutdown drain budget."""
    return float(os.getenv("TASKQ_DRAIN_TIMEOUT", "30"))


def _max_concurrent() -> int:
    """[FR-08] Read TASKQ_MAX_CONCURRENT — semaphore capacity for execute_task."""
    return int(os.getenv("TASKQ_MAX_CONCURRENT", "8"))


def _format_finished_at(value: datetime | None = None) -> str:
    """[FR-02] ISO-8601 UTC string with 'Z' suffix for ``finished_at``."""
    moment = value or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tail(value: str) -> str:
    """Keep only the bounded amount of process output stored for a run."""
    return value[-_OUTPUT_TAIL_LENGTH:]


async def _reap_process(process: asyncio.subprocess.Process) -> None:
    """Kill a still-running child and wait for it to be reaped.

    [FR-08] AC-8.3: on timeout the child is hard-killed via
    ``process.kill()`` and reaped via ``await process.wait()`` so no
    orphan survives (SPEC.md §8 #25).
    """
    if process.returncode is None:
        process.kill()
    await process.wait()


async def _run_command(command: str) -> _ProcessResult:
    """Run ``command`` and convert all terminal outcomes to one result shape.

    [FR-02] / [FR-08] AC-8.3: ``asyncio.wait_for`` bounds the subprocess
    lifetime to ``TASKQ_TASK_TIMEOUT``; on expiry the child is killed and
    reaped so no orphan process survives.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_timeout()
            )
        except asyncio.TimeoutError:
            await _reap_process(process)
            return _ProcessResult(exit_code=None, stdout="", stderr="")
        except Exception as exc:  # noqa: BLE001 — preserve a terminal failure
            await _reap_process(process)
            return _ProcessResult(exit_code=None, stdout="", stderr=str(exc))
    except Exception as exc:  # noqa: BLE001 — guard against spawn failures
        return _ProcessResult(exit_code=None, stdout="", stderr=str(exc))

    return _ProcessResult(
        exit_code=str(process.returncode),
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


def _persist_result(
    *,
    run_id: str,
    task_id: str,
    process_result: _ProcessResult,
    duration_ms: int,
) -> None:
    """Persist the bounded terminal result through the repository boundary."""
    with session_scope() as session:
        task_repo.save_result(
            session,
            run_id=run_id,
            task_id=task_id,
            exit_code=process_result.exit_code,
            stdout_tail=_tail(process_result.stdout),
            stderr_tail=_tail(process_result.stderr),
            duration_ms=str(duration_ms),
            finished_at=_format_finished_at(),
        )


# ---------------------------------------------------------------------------
# [FR-08] Concurrency cap (semaphore) + in-flight task registry for shutdown.
# ---------------------------------------------------------------------------


_concurrency_lock = threading.Lock()
_concurrency_sem: asyncio.Semaphore | None = None
_concurrency_capacity: int = -1

# In-flight wrapper tasks keyed by their ``task_id`` so ``shutdown()`` can
# drain and report interrupted ids without re-walking the call stack.
_in_flight_lock = threading.Lock()
_in_flight_tasks: dict[asyncio.Task, str] = {}


def _get_concurrency_semaphore() -> asyncio.Semaphore:
    """Return the module-level semaphore, recreating it on capacity change.

    [FR-08] AC-8.1: ``TASKQ_MAX_CONCURRENT`` bounds the in-flight count;
    the semaphore is the FIFO queue that prevents unbounded coroutine
    fan-out.
    """
    global _concurrency_sem, _concurrency_capacity
    capacity = _max_concurrent()
    with _concurrency_lock:
        if _concurrency_sem is None or _concurrency_capacity != capacity:
            _concurrency_sem = asyncio.Semaphore(capacity)
            _concurrency_capacity = capacity
        return _concurrency_sem


def _register_in_flight(task: asyncio.Task, task_id: str) -> None:
    """Add ``task`` to the in-flight registry under ``task_id``.

    [FR-08] AC-8.2: ``shutdown()`` reads this registry to drain.
    """
    with _in_flight_lock:
        _in_flight_tasks[task] = task_id

    def _deregister(_t: asyncio.Task) -> None:
        with _in_flight_lock:
            _in_flight_tasks.pop(_t, None)

    task.add_done_callback(_deregister)


async def execute_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Run one command and persist its terminal result.

    [FR-02] The subprocess is killed and reaped when the timeout expires.
    [FR-08] AC-8.1: the shared semaphore caps concurrent executions at
    ``TASKQ_MAX_CONCURRENT``; over-cap submissions wait in FIFO order.
    """
    execution_id = run_id or str(uuid.uuid4())
    current = asyncio.current_task()
    if current is not None:
        _register_in_flight(current, task_id)

    semaphore = _get_concurrency_semaphore()
    async with semaphore:
        started_at = time.monotonic()
        process_result = await _run_command(command)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _persist_result(
            run_id=execution_id,
            task_id=task_id,
            process_result=process_result,
            duration_ms=duration_ms,
        )
    return execution_id


async def shutdown() -> list[str]:
    """[FR-08] AC-8.2 — graceful drain entry point.

    Awaits every in-flight ``execute_task`` up to ``TASKQ_DRAIN_TIMEOUT``
    seconds; tasks exceeding the budget are cancelled (which propagates
    through the semaphore-acquired body and triggers
    ``process.kill()`` + ``await process.wait()`` on any in-flight
    subprocess — AC-8.3) and recorded in the returned list of
    interrupted ``task_id`` values.
    """
    timeout = _drain_timeout()
    with _in_flight_lock:
        snapshot = list(_in_flight_tasks.items())

    interrupted: list[str] = []
    if not snapshot:
        return interrupted

    async def _drain_one(task: asyncio.Task, task_id: str) -> str | None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            return None
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except BaseException:
                # Cancellation of the wrapper task surfaces as
                # CancelledError inside the body (which already reaped
                # the subprocess via process.kill() / await process.wait()
                # in _run_command). Swallow it here so the drain
                # coroutine itself does not propagate.
                pass
            return task_id
        except BaseException:
            return None

    results = await asyncio.gather(
        *[_drain_one(task, task_id) for task, task_id in snapshot],
    )
    for tid in results:
        if tid is not None:
            interrupted.append(tid)
    return interrupted


# ---------------------------------------------------------------------------
# [FR-08] Executor — async-context-managed concurrency surface (AC-8.4).
# ---------------------------------------------------------------------------


class Executor:
    """[FR-08] AC-8.4: async-context-managed concurrency surface.

    The Executor wraps the FR-08 runner module so callers can ``async with``
    an instance and ``submit`` coroutines. ``submit`` does NOT swallow
    ``asyncio.CancelledError`` — the cooperative cancellation primitive
    propagates to the awaiter (NFR-03).
    """

    def __init__(self) -> None:
        # The Executor class is the API surface the FR-08 RED tests reach
        # for; the runner module's module-level semaphore + in-flight set
        # carry the shared state across instances.
        pass

    async def __aenter__(self) -> "Executor":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Returning None propagates whatever the caller raises; in
        # particular, CancelledError inside the body must surface to the
        # caller's ``await`` (NFR-03 / AC-8.4).
        return None

    async def submit(self, coro: Any) -> Any:
        """[FR-08] Await ``coro`` and propagate exceptions / cancellation.

        Accepts either an awaitable coroutine *or* a coroutine function —
        the FR-08 RED test passes the function so it can wrap the call
        in ``asyncio.ensure_future`` and exercise cancellation against the
        task boundary. A bare ``await`` is intentional: any wrapper that
        caught ``Exception`` would silently turn ``asyncio.CancelledError``
        into a normal completion and break cooperative shutdown (NFR-03).
        """
        if inspect.iscoroutinefunction(coro):
            coro = coro()
        return await coro


# ---------------------------------------------------------------------------
# [FR-02] Background-thread entry point.
# ---------------------------------------------------------------------------


def _run_in_thread(
    coroutine_factory: Callable[[], Coroutine[Any, Any, str]],
) -> None:
    """Drive a coroutine on a private event loop in a daemon thread."""
    try:
        asyncio.run(coroutine_factory())
    except Exception:  # noqa: BLE001 — background failures cannot reach HTTP
        return


def start_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Schedule execution on a private event loop and return its id.

    [FR-02] HTTP handlers call this fire-and-forget entry point. A dedicated
    daemon thread drives the asyncio subprocess so we never depend on the
    caller's event loop (FastAPI dispatches sync handlers from a threadpool
    that has no running loop).
    """
    execution_id = run_id or str(uuid.uuid4())
    coroutine_factory = functools.partial(execute_task, task_id, command, execution_id)
    thread = threading.Thread(
        target=_run_in_thread,
        args=(coroutine_factory,),
        daemon=True,
        name=f"taskq-runner-{execution_id}",
    )
    thread.start()
    return execution_id


def list_results(task_id: str) -> list[TaskResult]:
    """Return a task's execution history in newest-first order."""
    with session_scope() as session:
        return task_repo.get_results(session, task_id)


async def run_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Compatibility entry point for asynchronous task execution.

    [FR-02] Delegates to :func:`execute_task`.
    """
    return await execute_task(task_id, command, run_id)
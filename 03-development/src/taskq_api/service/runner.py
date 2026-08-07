"""Task execution runner for FR-02.

[FR-02] Executes task commands without a shell, enforces a bounded timeout,
and persists terminal execution results.

Citations:
- SPEC.md#L79-L91 (FR-02 task execution endpoints and scopes)
- SPEC.md#L122-L128 (FR-06 repository/session ownership)
- SPEC.md#L385-L402 (problem response contract)
- SPEC.md#L88 (FR-02 §8 #16 — subprocess invocation uses shlex.split only)
- AC-2.2: process invocation uses create_subprocess_exec with shlex-split argv
"""
from __future__ import annotations

import asyncio
import functools
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


def _format_finished_at(value: datetime | None = None) -> str:
    """[FR-02] ISO-8601 UTC string with 'Z' suffix for ``finished_at``."""
    moment = value or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tail(value: str) -> str:
    """Keep only the bounded amount of process output stored for a run."""
    return value[-_OUTPUT_TAIL_LENGTH:]


async def _reap_process(process: asyncio.subprocess.Process) -> None:
    """Kill a still-running child and wait for it to be reaped."""
    if process.returncode is None:
        process.kill()
    await process.wait()


async def _run_command(command: str) -> _ProcessResult:
    """Run ``command`` and convert all terminal outcomes to one result shape."""
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


async def execute_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Run one command and persist its terminal result.

    [FR-02] The subprocess is killed and reaped when the timeout expires.
    """
    execution_id = run_id or str(uuid.uuid4())
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

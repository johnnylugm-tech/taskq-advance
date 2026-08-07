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
import os
import shlex
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from taskq_api.models.orm import TaskResult
from taskq_api.repository.session import session_scope


def _timeout() -> float:
    """[FR-02] Read TASKQ_TASK_TIMEOUT (seconds) — bounds subprocess runtime."""
    return float(os.getenv("TASKQ_TASK_TIMEOUT", "30"))


def _format_finished_at(value: datetime | None = None) -> str:
    """[FR-02] ISO-8601 UTC string with 'Z' suffix for the ``finished_at`` column."""
    moment = value or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist(result: dict[str, Any]) -> None:
    """[FR-02] Write a terminal run row to ``task_results``."""
    with session_scope() as session:
        session.add(TaskResult(**result))


async def execute_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Run one command and persist its terminal result.

    [FR-02] The subprocess is killed and reaped when the timeout expires.
    """
    run_id = run_id or str(uuid.uuid4())
    started = time.monotonic()
    exit_code: str | None = None
    stdout = ""
    stderr = ""
    try:
        process = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=_timeout())
            stdout = out.decode(errors="replace")
            stderr = err.decode(errors="replace")
            exit_code = str(process.returncode)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            exit_code = None
    except Exception as exc:  # noqa: BLE001 — guard against subprocess spawn errors
        stderr = str(exc)
        exit_code = None
    duration = int((time.monotonic() - started) * 1000)
    _persist(
        {
            "id": run_id,
            "task_id": task_id,
            "exit_code": exit_code,
            "stdout_tail": stdout[-8192:],
            "stderr_tail": stderr[-8192:],
            "duration_ms": str(duration),
            "finished_at": _format_finished_at(),
        }
    )
    return run_id


def _run_in_thread(coro_factory: Any) -> None:
    """[FR-02] Drive ``coro_factory()`` on a private event loop in a daemon thread."""

    async def _driver() -> None:
        try:
            await coro_factory()
        except Exception:  # noqa: BLE001 — never let the thread die noisily
            return

    try:
        asyncio.run(_driver())
    except Exception:  # noqa: BLE001
        return


def start_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Schedule execution on a private event loop and return its id.

    [FR-02] HTTP handlers call this fire-and-forget entry point. A dedicated
    daemon thread drives the asyncio subprocess so we never depend on the
    caller's event loop (FastAPI dispatches sync handlers from a threadpool
    that has no running loop).
    """
    run_id = run_id or str(uuid.uuid4())

    def _coro() -> Any:
        return execute_task(task_id, command, run_id)

    thread = threading.Thread(
        target=_run_in_thread,
        args=(_coro,),
        daemon=True,
        name=f"taskq-runner-{run_id}",
    )
    thread.start()
    return run_id


async def run_task(task_id: str, command: str, run_id: str | None = None) -> str:
    """Compatibility entry point for asynchronous task execution.

    [FR-02] Delegates to :func:`execute_task`.
    """
    return await execute_task(task_id, command, run_id)
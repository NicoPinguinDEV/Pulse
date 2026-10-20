"""P-07: fire-and-forget tasks that are neither garbage-collected nor fail silently.

`asyncio.create_task` only keeps a weak reference – a task nobody stores can disappear mid-run, and
its exception is only reported when the task object is destroyed. `spawn` keeps a strong reference
until the task is done and logs every error with an error ID.
"""
from __future__ import annotations

import asyncio
import secrets
import traceback

_RUNNING: set[asyncio.Task] = set()
MAX_RUNNING = 200  # bounded – a burst can never grow without limit (RAM budget)


def _done(task: asyncio.Task) -> None:
    _RUNNING.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        error_id = secrets.token_hex(3).upper()
        print(f"❌ Hintergrundaufgabe {task.get_name()} fehlgeschlagen [Fehler-ID {error_id}]: {type(exc).__name__}: {exc}")
        import pulse_logging
        pulse_logging.report_error(f"task.{task.get_name()}", f"{type(exc).__name__}: {exc}", error_id)
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def spawn(coro, *, name: str | None = None, loop: asyncio.AbstractEventLoop | None = None,
          critical: bool = False) -> asyncio.Task | None:
    """Schedule `coro` on the running (or given) loop and keep it alive until it finishes.

    `critical=True` (e.g. security alerts) is never dropped by the MAX_RUNNING limit."""
    if len(_RUNNING) >= MAX_RUNNING and not critical:
        coro.close()
        print(f"⚠️ Hintergrundaufgabe {name or '?'} verworfen: bereits {MAX_RUNNING} Aufgaben aktiv.")
        return None
    try:
        target = loop or asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # no loop: close instead of leaving a never-awaited coroutine
        raise
    task = target.create_task(coro, name=name)
    _RUNNING.add(task)
    task.add_done_callback(_done)
    return task


def running_count() -> int:
    return len(_RUNNING)

"""P-06 / P-01: registry of background jobs and event-loop lag – no extra loop of its own.

Existing loops wrap their work in `async with job("name", interval_seconds):`. The registry keeps
name, interval, last start, duration, runs, failures and the last error (bounded: one record per
job). The automation loop reports how late its 60-second sleep woke up (`record_loop_lag`), which
shows when synchronous work blocks Discord and the dashboard.
"""
from __future__ import annotations

import contextlib
import functools
import random
import time
import traceback
from datetime import datetime, timezone

LAG_WARN_SECONDS = 0.25
_JOBS: dict[str, dict] = {}
_LAG = {"last": 0.0, "max": 0.0, "max_at": "", "warnings": 0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.asynccontextmanager
async def job(name: str, interval_seconds: float | None = None):
    """Record one run of a background job. Errors are recorded and re-raised (the caller decides)."""
    entry = _JOBS.setdefault(name, {"name": name, "interval": interval_seconds, "runs": 0, "failures": 0,
                                    "last_start": "", "last_duration": 0.0, "last_error": "", "last_error_at": ""})
    entry["interval"] = interval_seconds
    entry["last_start"] = _now_iso()
    started = time.monotonic()
    try:
        yield entry
        entry["last_ok"] = _now_iso()
    except BaseException as exc:
        if not isinstance(exc, (GeneratorExit,)) and not exc.__class__.__name__ == "CancelledError":
            entry["failures"] += 1
            entry["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            entry["last_error_at"] = _now_iso()
            try:
                exc._pulse_job_counted = True  # record_error must not count/log it twice
            except AttributeError:
                pass
            import pulse_logging
            pulse_logging.report_error(f"job.{name}", entry["last_error"])
        raise
    finally:
        entry["runs"] += 1
        entry["last_duration"] = round(time.monotonic() - started, 3)


def tracked(name: str, interval_seconds: float | None = None, pausable: bool = True):
    """Decorator for `@tasks.loop` bodies: every run is recorded in the registry.
    pausable=False: live support (voice queue, music) keeps running in maintenance mode (F-04)."""
    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            import pulse_flags
            if pausable and pulse_flags.automations_paused():  # F-04: maintenance mode skips the run (recorded, not an error)
                _JOBS.setdefault(name, {"name": name, "interval": interval_seconds, "runs": 0, "failures": 0, "last_start": "",
                                        "last_duration": 0.0, "last_error": "", "last_error_at": ""})["paused_at"] = _now_iso()
                return None
            async with job(name, interval_seconds):
                return await fn(*args, **kwargs)
        return wrapper
    return decorate


def record_error(name: str, exc: BaseException, already_counted: bool = False) -> None:
    """For loops that catch errors themselves: log with traceback and keep the last error per job."""
    entry = _JOBS.setdefault(name, {"name": name, "interval": None, "runs": 0, "failures": 0, "last_start": "",
                                    "last_duration": 0.0, "last_error": "", "last_error_at": ""})
    if not already_counted:
        entry["failures"] += 1
    entry["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    entry["last_error_at"] = _now_iso()
    print(f"⚠️ Hintergrundjob {name} fehlgeschlagen: {entry['last_error']}")
    if not already_counted:  # job() has reported it already
        import pulse_logging
        pulse_logging.report_error(f"job.{name}", entry["last_error"])
    traceback.print_exception(type(exc), exc, exc.__traceback__)


def record_loop_lag(expected_seconds: float, actual_seconds: float) -> float:
    lag = max(0.0, actual_seconds - expected_seconds)
    _LAG["last"] = round(lag, 3)
    if lag > _LAG["max"]:
        _LAG["max"], _LAG["max_at"] = round(lag, 3), _now_iso()
    if lag >= LAG_WARN_SECONDS:
        _LAG["warnings"] += 1
        print(f"⚠️ Event-Loop verzögert: {lag * 1000:.0f} ms – synchrone Arbeit blockiert Discord und Dashboard.")
    return lag


def jitter(seconds: float, spread: float = 0.1) -> float:
    """Spread periodic work a little so jobs do not all wake at the same second."""
    return max(0.0, seconds * (1 + random.uniform(-spread, spread)))


def snapshot() -> dict:
    return {"jobs": sorted((dict(v) for v in _JOBS.values()), key=lambda j: j["name"]), "lag": dict(_LAG)}

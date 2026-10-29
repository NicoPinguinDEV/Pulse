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
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

LAG_WARN_SECONDS = 0.25
_JOBS: dict[str, dict] = {}
_LAG = {"last": 0.0, "max": 0.0, "max_at": "", "warnings": 0, "stalls": 0}


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
    failures_before = entry["failures"]
    try:
        yield entry
        # A loop that catches its own error and reports it with record_error() did not run OK – without this check
        # last_ok (same second) would hide the error from health_summary()/jobs_failing.
        if entry["failures"] == failures_before:
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
            _alert(name, entry["last_error"])
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
        _alert(name, entry["last_error"])
    traceback.print_exception(type(exc), exc, exc.__traceback__)


def _alert(name: str, error: str) -> None:
    """O-07: one throttled alert per job (30 min cooldown); posted by the automation loop."""
    try:
        import pulse_alerts
        pulse_alerts.raise_alert(f"job:{name}", f"⚠️ **Hintergrundjob fehlgeschlagen:** {name}\n`{error}`")
    except Exception:
        pass


def health_summary() -> dict:
    """O-07 for /healthz: newest successful run over all jobs and the jobs whose last run failed."""
    last_ok = max((j.get("last_ok", "") for j in _JOBS.values()), default="") or None
    failing = sorted(j["name"] for j in _JOBS.values() if j.get("last_error_at") and j["last_error_at"] > j.get("last_ok", ""))
    return {"last_job_ok": last_ok, "jobs_failing": failing}


def record_loop_lag(expected_seconds: float, actual_seconds: float) -> float:
    lag = max(0.0, actual_seconds - expected_seconds)
    _LAG["last"] = round(lag, 3)
    if lag > _LAG["max"]:
        _LAG["max"], _LAG["max_at"] = round(lag, 3), _now_iso()
    if lag >= LAG_WARN_SECONDS:
        _LAG["warnings"] += 1
        print(f"⚠️ Event-Loop verzögert: {lag * 1000:.0f} ms – synchrone Arbeit blockiert Discord und Dashboard.")
    return lag


STALL_WARN_SECONDS = 2.0
STALL_REPORT_COOLDOWN = 300.0
_WATCHDOG: dict = {"stop": None}


def _loop_stack(thread_id: int, limit: int = 12) -> str:
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return "(Stack nicht verfügbar)"
    lines = traceback.format_stack(frame)[-limit:]
    return "".join(lines).rstrip()


def start_stall_watchdog(loop, threshold: float = STALL_WARN_SECONDS, interval: float = 0.5,
                         report=print, cooldown: float = STALL_REPORT_COOLDOWN) -> threading.Event:
    """One daemon thread that names the code blocking the event loop.

    `record_loop_lag` only shows *that* the loop was blocked. This thread pings the loop and, when a
    ping stays unanswered for `threshold` seconds, prints the loop thread's current stack once per
    stall (at most every `cooldown` seconds). Started once per process; returns its stop event.
    """
    if _WATCHDOG["stop"] is not None and not _WATCHDOG["stop"].is_set():
        return _WATCHDOG["stop"]
    stop = threading.Event()
    _WATCHDOG["stop"] = stop
    loop_thread_id = threading.get_ident()
    state = {"last_seen": time.monotonic()}

    def _pong():
        state["last_seen"] = time.monotonic()

    def _watch():
        reported_for = None
        last_report = 0.0
        while not stop.wait(interval):
            try:
                loop.call_soon_threadsafe(_pong)
            except RuntimeError:  # loop closed
                return
            seen = state["last_seen"]
            blocked = time.monotonic() - seen
            if blocked < threshold or reported_for == seen:
                continue
            reported_for = seen
            _LAG["stalls"] = _LAG.get("stalls", 0) + 1
            if time.monotonic() - last_report < cooldown:
                continue
            last_report = time.monotonic()
            report(f"⚠️ Event-Loop blockiert seit {blocked:.1f} s – aktueller Stack:\n{_loop_stack(loop_thread_id)}")

    threading.Thread(target=_watch, name="pulse-loop-watchdog", daemon=True).start()
    return stop


def jitter(seconds: float, spread: float = 0.1) -> float:
    """Spread periodic work a little so jobs do not all wake at the same second."""
    return max(0.0, seconds * (1 + random.uniform(-spread, spread)))


def snapshot() -> dict:
    return {"jobs": sorted((dict(v) for v in _JOBS.values()), key=lambda j: j["name"]), "lag": dict(_LAG)}

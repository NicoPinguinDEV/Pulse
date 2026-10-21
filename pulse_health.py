"""O-07: read-only health diagnostics derived from existing runtime state."""
from __future__ import annotations

import math
from datetime import datetime, timezone


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def health_details(bot, job_snapshot: dict | None) -> dict:
    """Return bounded operational metrics; never includes secrets or member data."""
    latency = getattr(bot, "latency", None) if bot is not None else None
    latency_ms = None
    if isinstance(latency, (int, float)) and math.isfinite(latency) and latency >= 0:
        latency_ms = round(latency * 1000)

    state = job_snapshot if isinstance(job_snapshot, dict) else {}
    lag = state.get("lag") if isinstance(state.get("lag"), dict) else {}
    lag_seconds = lag.get("last")
    lag_ms = None
    if isinstance(lag_seconds, (int, float)) and math.isfinite(lag_seconds) and lag_seconds >= 0:
        lag_ms = round(lag_seconds * 1000)

    raw_jobs = state.get("jobs") if isinstance(state.get("jobs"), list) else []
    jobs = [item for item in raw_jobs if isinstance(item, dict)]
    failed_count = sum(1 for item in jobs if _integer(item.get("failures")) > 0)
    latest = []
    for item in jobs:
        at = _timestamp(item.get("last_ok"))
        if at is not None:
            latest.append((at, str(item.get("name") or "Unbenannter Job")[:100], str(item.get("last_ok"))))
    latest.sort(key=lambda row: row[0], reverse=True)

    return {
        "gateway_latency_ms": latency_ms,
        "event_loop_lag_ms": lag_ms,
        "jobs_total": len(jobs),
        "jobs_failed": failed_count,
        "last_successful_job": latest[0][1] if latest else None,
        "last_successful_job_at": latest[0][2] if latest else None,
    }

"""Pure helpers for automatic shift timeout handling."""
from __future__ import annotations

from datetime import datetime
import uuid

AUTO_SHIFT_END_HOURS = 24.0
AUTO_SHIFT_END_SECONDS = int(AUTO_SHIFT_END_HOURS * 3600)


def shift_age_seconds(started_at_iso: str | None, now: datetime) -> float | None:
    """Return elapsed wall-clock seconds, handling old naive ISO timestamps."""
    if not started_at_iso:
        return None
    try:
        started = datetime.fromisoformat(str(started_at_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None and now.tzinfo is not None:
        started = started.replace(tzinfo=now.tzinfo)
    elif started.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=started.tzinfo)
    try:
        return max(0.0, (now - started).total_seconds())
    except (TypeError, ValueError):
        return None


def shift_is_overdue(started_at_iso: str | None, now: datetime) -> bool:
    age = shift_age_seconds(started_at_iso, now)
    return age is not None and age >= AUTO_SHIFT_END_SECONDS


def shift_event_id(member_id: str | int, started_at_iso: str | None) -> str:
    """Stable identifier prevents duplicate automatic warnings across restarts."""
    value = f"pulse-auto-shift:{member_id}:{started_at_iso or 'missing-start-time'}"
    return "shift_" + uuid.uuid5(uuid.NAMESPACE_URL, value).hex

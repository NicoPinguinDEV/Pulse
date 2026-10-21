"""Pure helpers for automatic shift timeout handling."""
from __future__ import annotations

from datetime import datetime
import math
import uuid

AUTO_SHIFT_END_HOURS = 24.0
AUTO_SHIFT_END_SECONDS = int(AUTO_SHIFT_END_HOURS * 3600)
DEFAULT_MAX_SHIFT_HOURS = 12.0
MIN_MAX_SHIFT_HOURS = 0.5
ABSOLUTE_MAX_SHIFT_HOURS = 24.0


def parse_max_shift_hours(value, default: float = DEFAULT_MAX_SHIFT_HOURS) -> float:
    """Read MAX_SHIFT_HOURS as a credit-confirmation cap, not as auto-close threshold.

    Invalid, non-finite, or out-of-range values fall back safely. Automatic close
    always stays at 24 hours; an unconfirmed auto-closed shift earns zero hours.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed) or not MIN_MAX_SHIFT_HOURS <= parsed <= ABSOLUTE_MAX_SHIFT_HOURS:
        return float(default)
    return parsed


def credited_shift_seconds(shift_record: dict) -> int:
    """Return creditable time; auto-closed shifts count zero until a leader confirms them."""
    if not isinstance(shift_record, dict):
        return 0
    if shift_record.get("auto_closed") and not shift_record.get("duration_confirmed"):
        return 0
    try:
        return max(0, int(shift_record.get("duration_seconds") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


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

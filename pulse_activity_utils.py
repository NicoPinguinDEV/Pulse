"""Pure helpers for Activity Check deadlines and leave-date evaluation."""
from __future__ import annotations

from datetime import date, datetime, time
import uuid
from zoneinfo import ZoneInfo

BERLIN_TZ = ZoneInfo("Europe/Berlin")


def parse_activity_date(value: object) -> date | None:
    """Parse known date formats used by the Abmeldung module."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        pass
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def leave_covers_date(von: object, bis: object, target_date: object) -> bool:
    """Return whether an Abmeldung covers a check date.

    If a stored leave row exists but its date is malformed, fail safe and treat
    it as an active leave instead of issuing an unintended warning.
    """
    target = parse_activity_date(target_date)
    if target is None:
        return False
    start_text, end_text = str(von or "").strip(), str(bis or "").strip()
    if not start_text and not end_text:
        return False
    start = parse_activity_date(start_text) if start_text else date.min
    end = parse_activity_date(end_text) if end_text else date.max
    if (start_text and start is None) or (end_text and end is None):
        return True
    return bool(start and end and start <= target <= end)


def activity_warning_due(local_now: datetime) -> bool:
    """Activity warnings are due at 20:00 in the supplied local timezone."""
    return (local_now.hour, local_now.minute) >= (20, 0)


def confirmed_by_deadline(reacted_at: object, check_date: object) -> bool:
    """Whether a stored response timestamp is at or before 20:00 Berlin time."""
    target = parse_activity_date(check_date)
    if target is None or not reacted_at:
        return False
    try:
        reacted = datetime.fromisoformat(str(reacted_at).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if reacted.tzinfo is None:
        reacted = reacted.replace(tzinfo=BERLIN_TZ)
    else:
        reacted = reacted.astimezone(BERLIN_TZ)
    deadline = datetime.combine(target, time(hour=20, minute=0), tzinfo=BERLIN_TZ)
    return reacted <= deadline


def activity_warning_id(check_id: int, user_id: int) -> str:
    """Stable warning ID prevents duplicates after process restarts."""
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"pulse-activity-check:{int(check_id)}:{int(user_id)}")
    return "warn_" + token.hex[:10]


def activity_action_is_terminal(status: str | None) -> bool:
    """Statuses which should never be processed again by the retry loop."""
    return str(status or "") in {
        "issued",
        "confirmed",
        "on_leave",
        "not_team_member",
        "member_left_server",
        "max_warns",
        "empty_snapshot",
    }

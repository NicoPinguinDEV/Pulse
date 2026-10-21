"""Pure voice-support scheduling helpers; intentionally has no Discord dependency."""
from __future__ import annotations

import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DAY_NAMES = dict(zip(DAYS, ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")))
DEFAULT_HOURS = {
    "monday": {"enabled": True, "start": "10:00", "end": "22:00"},
    "tuesday": {"enabled": True, "start": "10:00", "end": "22:00"},
    "wednesday": {"enabled": True, "start": "10:00", "end": "22:00"},
    "thursday": {"enabled": True, "start": "10:00", "end": "22:00"},
    "friday": {"enabled": True, "start": "10:00", "end": "23:59"},
    "saturday": {"enabled": True, "start": "10:00", "end": "23:59"},
    "sunday": {"enabled": True, "start": "10:00", "end": "22:00"},
}


def shift_is_online(shifts: dict, user_id: int | str) -> bool:
    """Only an online Pulse dashboard shift counts as active support duty."""
    active = shifts.get("active_shifts", {}) if isinstance(shifts, dict) else {}
    state = active.get(str(user_id), {}) if isinstance(active, dict) else {}
    return isinstance(state, dict) and state.get("status") == "online"


def support_open_now(cfg: dict, now: datetime | None = None) -> bool:
    """Return whether a timestamp is inside today's configured Berlin support hours."""
    try:
        local = (now or datetime.now(ZoneInfo("Europe/Berlin"))).astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:
        local = now or datetime.now()

    hours = cfg.get("hours", {}) if isinstance(cfg, dict) else {}
    day = DAYS[local.weekday()]
    item = hours.get(day, DEFAULT_HOURS[day]) if isinstance(hours, dict) else DEFAULT_HOURS[day]
    if not isinstance(item, dict) or not item.get("enabled"):
        return False

    def parse(value: str) -> int:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value or "")):
            raise ValueError("Uhrzeit muss HH:MM sein")
        hour, minute = str(value).split(":")
        return int(hour) * 60 + int(minute)

    try:
        start = parse(item.get("start", "10:00"))
        end = parse(item.get("end", "22:00"))
    except ValueError:
        return False

    current = local.hour * 60 + local.minute
    if start == end:
        return False
    return start <= current < end if start < end else current >= start or current < end


def queue_position(cases: dict, case_id: str, guild_id=None) -> int | None:
    """Return the 1-based position of a queued case, ignoring assigned/closed cases.

    ISO timestamps are parsed into UTC rather than sorted as strings, so Berlin's
    daylight-saving offset changes do not reorder waiting users incorrectly.
    """
    from datetime import timezone

    if not isinstance(cases, dict):
        return None

    waiting = []
    for item in cases.values():
        if not isinstance(item, dict) or not item.get("waiting_in_shared_room"):
            continue
        try:
            assigned = int(item.get("assigned_staff_id") or 0) > 0
        except (TypeError, ValueError):
            assigned = bool(item.get("assigned_staff_id"))
        if assigned:
            continue
        if guild_id is not None and str(item.get("guild_id")) != str(guild_id):
            continue
        raw = str(item.get("created_at") or "")
        try:
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            created = created.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            created = datetime.max.replace(tzinfo=timezone.utc)
        waiting.append((created, str(item.get("case_id") or ""), item))

    waiting.sort(key=lambda row: (row[0], row[1]))
    needle = str(case_id or "")
    for index, (_, stored_id, _) in enumerate(waiting, start=1):
        if stored_id == needle:
            return index
    return None


def estimate_wait_seconds(position: int | None, average_case_seconds: float | None,
                          parallel_supporters: int = 1) -> int | None:
    """Estimate a queue wait using recent completed-case duration; return None without history."""
    try:
        place = int(position or 0)
        average = float(average_case_seconds or 0)
        parallel = max(1, int(parallel_supporters or 1))
    except (TypeError, ValueError, OverflowError):
        return None
    if place < 1 or average <= 0:
        return None
    import math
    return max(0, int(math.ceil(place * average / parallel)))


def format_wait_estimate(seconds: int | None) -> str:
    """Format an estimated wait duration for users, without implying a guarantee."""
    if seconds is None:
        return "Noch keine Vergleichsdaten"
    try:
        total_minutes = max(0, int(math.ceil(max(0, float(seconds)) / 60)))
    except (TypeError, ValueError, OverflowError):
        return "Noch keine Vergleichsdaten"
    if total_minutes < 60:
        return f"ca. {total_minutes} Min."
    hours, minutes = divmod(total_minutes, 60)
    return f"ca. {hours} Std. {minutes} Min." if minutes else f"ca. {hours} Std."

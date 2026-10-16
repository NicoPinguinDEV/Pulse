"""Pure voice-support scheduling helpers; intentionally has no Discord dependency."""
from __future__ import annotations

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
        if not re.fullmatch(r"(?:[01]\\d|2[0-3]):[0-5]\\d", str(value or "")):
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

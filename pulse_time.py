"""D-09: one tolerant timestamp parser for all formats Pulse has written over time.

Stored data mixes `%d.%m.%Y %H:%M` (warnings, applications), `%d.%m.%Y %H:%M:%S` (audit), `%Y-%m-%d`
(shifts), ISO with Berlin offset, ISO in UTC (`pulse_db._now`) and naive values. New code reads every
format through `parse_timestamp` and shows times in Europe/Berlin via `display`. Naive values are
interpreted as Berlin local time, which is how the German formats were written (`now_de()`).
Existing data is never rewritten here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y",
)


def parse_timestamp(value, default_tz=BERLIN) -> datetime | None:
    """Timezone-aware datetime for any known Pulse format, or None (never raises)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def now_iso() -> str:
    """Current time as ISO 8601 with Berlin offset – the format for new data."""
    return datetime.now(BERLIN).isoformat(timespec="seconds")


def display(value, with_time: bool = True, fallback: str | None = None) -> str:
    """'20.10.2026 14:30' in Berlin time; unknown values are returned unchanged."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return str(value or "") if fallback is None else fallback
    local = parsed.astimezone(BERLIN)
    return local.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def age_days(value, now: datetime | None = None) -> float | None:
    """Days since the timestamp (for retention rules), or None if unparseable."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BERLIN)
    return (current - parsed).total_seconds() / 86400

"""Pure helpers for the leadership cockpit; intentionally has no Discord dependency."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from pulse_shift_utils import credited_shift_seconds

LONG_SHIFT_SECONDS = 12 * 3600
INACTIVE_AFTER_DAYS = 8


def _entry_date(entry: dict) -> date | None:
    try:
        return datetime.strptime(str(entry.get("date") or ""), "%Y-%m-%d").date()
    except ValueError:
        return None


def seconds_per_member(history: list, start: date, end: date) -> dict[str, int]:
    """Total shift seconds per member for entries dated start..end (inclusive)."""
    totals: dict[str, int] = {}
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        day = _entry_date(entry)
        if day is None or not (start <= day <= end):
            continue
        key = str(entry.get("mod_id"))
        totals[key] = totals.get(key, 0) + credited_shift_seconds(entry)  # E-2
    return totals


def last_shift_dates(history: list) -> dict[str, date]:
    """Most recent shift date per member."""
    latest: dict[str, date] = {}
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        day = _entry_date(entry)
        if day is None:
            continue
        key = str(entry.get("mod_id"))
        if key not in latest or day > latest[key]:
            latest[key] = day
    return latest


def week_to_date_windows(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(this week Monday..today, same span one week earlier) for a fair comparison."""
    start = today - timedelta(days=today.weekday())
    return (start, today), (start - timedelta(days=7), today - timedelta(days=7))


def previous_full_weeks(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(last complete Mon–Sun week, the week before it) for a Monday-morning report."""
    this_monday = today - timedelta(days=today.weekday())
    last_start = this_monday - timedelta(days=7)
    return (last_start, this_monday - timedelta(days=1)), (last_start - timedelta(days=7), last_start - timedelta(days=1))


def trend(current: float, previous: float) -> tuple[str, str]:
    """Return (arrow, label) describing the change versus the previous period."""
    current = float(current or 0)
    previous = float(previous or 0)
    if previous <= 0:
        return ("↑", "neu") if current > 0 else ("→", "unverändert")
    change = round((current - previous) * 100 / previous)
    if change > 0:
        return "↑", f"+{change} %"
    if change < 0:
        return "↓", f"{change} %"
    return "→", "±0 %"


def inactive_days(last: date | None, today: date) -> int | None:
    """Days since the last shift; None if the member never had one."""
    if last is None:
        return None
    return max(0, (today - last).days)


def is_inactive(last: date | None, today: date, on_leave: bool, threshold: int = INACTIVE_AFTER_DAYS) -> bool:
    """Members on leave are never flagged; never-active members count as inactive."""
    if on_leave:
        return False
    days = inactive_days(last, today)
    return days is None or days >= threshold

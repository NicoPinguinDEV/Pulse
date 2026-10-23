"""Pure helpers for the personal "Mein Pulse" page; intentionally has no Discord dependency."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from pulse_shift_utils import credited_shift_seconds

WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def greeting(hour: int) -> str:
    """Time-of-day greeting in German."""
    hour = int(hour) % 24
    if 5 <= hour < 11:
        return "Guten Morgen"
    if 11 <= hour < 18:
        return "Guten Tag"
    if 18 <= hour < 23:
        return "Guten Abend"
    return "Gute Nacht"


def format_hm(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def goal_progress(weekly_seconds: float, goal_hours: float) -> tuple[int, int]:
    """Return (percent capped at 100, remaining seconds) for the weekly hour goal."""
    goal_seconds = max(0.0, float(goal_hours or 0)) * 3600
    done = max(0.0, float(weekly_seconds or 0))
    if goal_seconds <= 0:
        return 100, 0
    return min(100, int(done * 100 // goal_seconds)), int(max(0.0, goal_seconds - done))


def _entry_date(entry: dict) -> date | None:
    try:
        return datetime.strptime(str(entry.get("date") or ""), "%Y-%m-%d").date()
    except ValueError:
        return None


def daily_seconds(
    history: list,
    mod_id: str,
    today: date,
    days: int = 7,
    active_date: date | None = None,
    active_seconds: float = 0,
) -> list[tuple[date, int]]:
    """Shift seconds per day for the last `days` days (oldest first), including a running shift."""
    days = max(1, int(days))
    start = today - timedelta(days=days - 1)
    totals = {start + timedelta(days=i): 0 for i in range(days)}
    for entry in history or []:
        if not isinstance(entry, dict) or str(entry.get("mod_id")) != str(mod_id):
            continue
        day = _entry_date(entry)
        if day in totals:
            totals[day] += credited_shift_seconds(entry)  # E-2
    if active_date in totals:
        totals[active_date] += max(0, int(active_seconds or 0))
    return sorted(totals.items())


def shift_streak(history: list, mod_id: str, today: date, active_today: bool = False) -> int:
    """Consecutive days with a shift, ending today (or yesterday if today has none yet)."""
    worked = {
        _entry_date(entry)
        for entry in history or []
        if isinstance(entry, dict)
        and str(entry.get("mod_id")) == str(mod_id)
        and credited_shift_seconds(entry) > 0
    }
    worked.discard(None)
    if active_today:
        worked.add(today)
    cursor = today if today in worked else today - timedelta(days=1)
    streak = 0
    while cursor in worked:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def due_state(due: datetime | None, now: datetime) -> str | None:
    """Classify a task deadline: overdue, today, soon (within 3 days) or later."""
    if due is None:
        return None
    if due < now:
        return "overdue"
    if due.date() == now.date():
        return "today"
    if due - now <= timedelta(days=3):
        return "soon"
    return "later"

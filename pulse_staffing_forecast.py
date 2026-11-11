"""Transparent, bounded ticket-demand forecast for future team schedules."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
MIN_CAPACITY = 0.5
MAX_CAPACITY = 10.0
DEFAULT_CAPACITY = 2.0
MAX_HISTORY_DAYS = 112
MAX_DAYS_AHEAD = 14
MAX_TICKETS = 2000
MAX_SHIFTS = 1000
MAX_ROWS = 56


def _local_datetime(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BERLIN)
    except (TypeError, ValueError, OverflowError):
        return None


def _shift_interval(row) -> tuple[datetime, datetime] | None:
    try:
        day = date.fromisoformat(str(row.get("date") or ""))
        start_time = time.fromisoformat(str(row.get("start_time") or ""))
        end_time = time.fromisoformat(str(row.get("end_time") or ""))
        start = datetime.combine(day, start_time, tzinfo=BERLIN)
        end = datetime.combine(day, end_time, tzinfo=BERLIN)
        if end <= start:
            end += timedelta(days=1)
        return start, end
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_capacity(value) -> float:
    try:
        capacity = float(value)
    except (TypeError, ValueError):
        capacity = DEFAULT_CAPACITY
    if not (MIN_CAPACITY <= capacity <= MAX_CAPACITY):
        capacity = DEFAULT_CAPACITY
    return round(capacity, 1)


def build_forecast(
    tickets,
    shifts,
    *,
    now: datetime | None = None,
    history_days: int = 56,
    days_ahead: int = 7,
    tickets_per_person_hour: float = DEFAULT_CAPACITY,
    limit: int = MAX_ROWS,
) -> dict:
    """Average ticket arrivals by weekday/hour over completed days; compare with planned shifts.

    A required headcount is a configurable planning heuristic (expected arrivals divided by
    tickets/person/hour), not a staffing command or an automatic assignment.
    """
    current = now or datetime.now(BERLIN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BERLIN)
    current = current.astimezone(BERLIN)
    try:
        history_days = max(28, min(int(history_days), MAX_HISTORY_DAYS))
    except (TypeError, ValueError):
        history_days = 56
    try:
        days_ahead = max(1, min(int(days_ahead), MAX_DAYS_AHEAD))
    except (TypeError, ValueError):
        days_ahead = 7
    try:
        limit = max(1, min(int(limit), MAX_ROWS))
    except (TypeError, ValueError):
        limit = MAX_ROWS
    capacity = normalize_capacity(tickets_per_person_hour)

    first_day = current.date() - timedelta(days=history_days)
    last_day = current.date() - timedelta(days=1)
    observed = Counter()
    observations = Counter()
    day = first_day
    while day <= last_day:
        for hour in range(24):
            observations[(day.weekday(), hour)] += 1
        day += timedelta(days=1)

    history_tickets = 0
    for ticket in (tickets if isinstance(tickets, (list, tuple)) else ())[:MAX_TICKETS]:
        opened = _local_datetime(ticket.get("opened_at")) if isinstance(ticket, dict) else None
        if opened is None or not (first_day <= opened.date() <= last_day):
            continue
        observed[(opened.weekday(), opened.hour)] += 1
        history_tickets += 1

    intervals = []
    for shift in (shifts if isinstance(shifts, (list, tuple)) else ())[:MAX_SHIFTS]:
        if not isinstance(shift, dict):
            continue
        interval = _shift_interval(shift)
        assignee_id = str(shift.get("assignee_id") or "").strip()
        if interval is not None and assignee_id:
            intervals.append((assignee_id, interval[0], interval[1]))

    rows = []
    for offset in range(days_ahead):
        target_day = current.date() + timedelta(days=offset)
        for hour in range(24):
            sample_count = observations[(target_day.weekday(), hour)]
            expected = (
                observed[(target_day.weekday(), hour)] / sample_count
                if sample_count else 0.0
            )
            slot_start = datetime.combine(target_day, time(hour), tzinfo=BERLIN)
            slot_end = slot_start + timedelta(hours=1)
            covered_members = {
                assignee_id for assignee_id, start, end in intervals
                if start < slot_end and slot_start < end
            }
            coverage = len(covered_members)
            required = ceil(expected / capacity) if expected > 0 else 0
            if expected <= 0 and coverage <= 0:
                continue
            rows.append({
                "date": target_day.isoformat(),
                "weekday": target_day.strftime("%A"),
                "hour": hour,
                "expected_tickets": round(expected, 2),
                "required_staff": required,
                "planned_staff": coverage,
                "gap": max(0, required - coverage),
                "sample_occurrences": sample_count,
            })

    rows.sort(key=lambda row: (
        -row["gap"], -row["expected_tickets"], row["date"], row["hour"],
    ))
    return {
        "rows": rows[:limit],
        "history_days": history_days,
        "history_tickets": history_tickets,
        "days_ahead": days_ahead,
        "tickets_per_person_hour": capacity,
        "sample_occurrences_per_weekday": history_days // 7,
        "planned_shifts_count": len({(assignee_id, start, end) for assignee_id, start, end in intervals}),
        "truncated": len(rows) > limit,
    }

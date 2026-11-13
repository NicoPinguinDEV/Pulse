"""Audit #86: reminders before personal shifts (pure selection logic; delivery stays in the Suite loop).

``due_reminders`` decides which planned shifts need a reminder right now:
- start time in the wall-clock zone of the panel, so a 20:00 shift is 20:00 for the person, also across DST changes;
- reminder window = ``lead_minutes`` before the start; the loop runs every 5 minutes, so a late run still reminds
  as long as the shift has not started yet;
- the dedupe key contains the shift id, the start and the lead, so a shift that was moved is announced again while
  the same shift is never announced twice;
- only shifts of the given guild with a real person assigned; broken rows are skipped, never fatal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_LEAD_MINUTES = 30
MIN_LEAD_MINUTES = 5
MAX_LEAD_MINUTES = 720
DEDUPE_SECONDS = 86400


def normalize_lead(value) -> int:
    try:
        lead = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LEAD_MINUTES
    return lead if MIN_LEAD_MINUTES <= lead <= MAX_LEAD_MINUTES else DEFAULT_LEAD_MINUTES


def candidate_dates(now: datetime, lead_minutes: int) -> list[str]:
    """ISO dates a due shift can start on (today and, near midnight or with a long lead, the following days)."""
    last = (now + timedelta(minutes=normalize_lead(lead_minutes))).date()
    days, day = [], now.date()
    while day <= last:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def start_of(row: dict, tz) -> datetime | None:
    try:
        naive = datetime.strptime(f"{row['date']} {row['start_time']}", "%Y-%m-%d %H:%M")
    except (KeyError, TypeError, ValueError):
        return None
    return naive.replace(tzinfo=tz)


def due_reminders(rows: list[dict], now: datetime, lead_minutes: int = DEFAULT_LEAD_MINUTES) -> list[dict]:
    """Shifts starting within the next ``lead_minutes`` (but not yet started). ``now`` must be timezone-aware."""
    lead = normalize_lead(lead_minutes)
    tz = now.tzinfo or timezone.utc
    due = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        assignee = str(row.get("assignee_id") or "").strip()
        start = start_of(row, tz)
        if not assignee.isdigit() or start is None:
            continue
        if now <= start <= now + timedelta(minutes=lead):
            minutes = max(0, round((start - now).total_seconds() / 60))
            due.append({
                "shift_id": str(row.get("id")), "assignee_id": assignee, "minutes": minutes,
                "title": "⏰ Schicht beginnt bald",
                "text": f"{row.get('shift_name') or 'Dienst'} beginnt um {row['start_time']} (in {minutes} Min., bis {row.get('end_time') or '?'}).",
                "dedupe_key": f"shift{lead}:{row.get('id')}:{row['date']}T{row['start_time']}",
            })
    due.sort(key=lambda item: (item["minutes"], item["shift_id"]))
    return due

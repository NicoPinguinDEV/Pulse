"""Audit #83/#85: validation of planned shifts and minimum-staffing (coverage) checks.

Pure functions, no I/O. Times are wall-clock Europe/Berlin values as stored in ``suite_schedule``
(``date`` = ISO day, ``start_time``/``end_time`` = HH:MM; an end before the start means the shift ends the next day).

- ``validate_shift``: rejects malformed dates/times, empty names, zero-length and implausibly long shifts.
- ``find_conflicts``: overlapping shifts of the same person.
- ``coverage_gaps``: compares planned shifts with minimum-staffing rules per weekday and time window and returns
  contiguous gaps. A gap is a planning hint for leadership, never an automatic assignment.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

MAX_SHIFT_HOURS = 16
MAX_MIN_STAFF = 50
MAX_WINDOW_DAYS = 14


def _parse_time(value) -> time:
    try:
        parsed = time.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError("Uhrzeit muss im Format HH:MM angegeben werden.") from None
    return parsed.replace(second=0, microsecond=0)


def _parse_date(value) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError("Datum muss im Format JJJJ-MM-TT angegeben werden.") from None


def interval(day, start, end) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(_parse_date(day), _parse_time(start))
    end_dt = datetime.combine(_parse_date(day), _parse_time(end))
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def validate_shift(day, name, start, end) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Der Dienst braucht einen Namen.")
    if _parse_time(start) == _parse_time(end):
        raise ValueError("Beginn und Ende dürfen nicht identisch sein.")
    start_dt, end_dt = interval(day, start, end)
    if end_dt - start_dt > timedelta(hours=MAX_SHIFT_HOURS):
        raise ValueError(f"Ein Dienst darf höchstens {MAX_SHIFT_HOURS} Stunden dauern.")
    return {"date": start_dt.date().isoformat(), "shift_name": name[:80],
            "start_time": start_dt.strftime("%H:%M"), "end_time": end_dt.strftime("%H:%M")}


def find_conflicts(shift: dict, existing: list[dict]) -> list[dict]:
    """Existing shifts of the same person that overlap the new one (``existing`` is not filtered by person here)."""
    new_start, new_end = interval(shift["date"], shift["start_time"], shift["end_time"])
    hits = []
    for other in existing:
        if str(other.get("assignee_id")) != str(shift.get("assignee_id")) or other.get("id") == shift.get("id"):
            continue
        try:
            start, end = interval(other["date"], other["start_time"], other["end_time"])
        except (KeyError, ValueError):
            continue
        if start < new_end and new_start < end:
            hits.append(other)
    return hits


def validate_minimum(weekday, start, end, min_staff) -> dict:
    """``weekday`` 0-6 (Monday=0) or None/'' for every day."""
    if weekday in (None, "", "all"):
        day = None
    else:
        try:
            day = int(weekday)
        except (TypeError, ValueError):
            raise ValueError("Ungültiger Wochentag.") from None
        if not 0 <= day <= 6:
            raise ValueError("Ungültiger Wochentag.")
    start_t, end_t = _parse_time(start), _parse_time(end)
    if start_t >= end_t:
        raise ValueError("Das Zeitfenster muss am selben Tag enden (Beginn vor Ende).")
    try:
        count = int(min_staff)
    except (TypeError, ValueError):
        raise ValueError("Mindestbesetzung muss eine Zahl sein.") from None
    if not 1 <= count <= MAX_MIN_STAFF:
        raise ValueError(f"Mindestbesetzung muss zwischen 1 und {MAX_MIN_STAFF} liegen.")
    return {"weekday": day, "start_time": start_t.strftime("%H:%M"), "end_time": end_t.strftime("%H:%M"),
            "min_staff": count}


def coverage_gaps(shifts: list[dict], minimums: list[dict], start_day: date, days: int = 7) -> list[dict]:
    """Contiguous windows (15-minute resolution) where fewer distinct people are planned than a rule requires."""
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    intervals = []
    for shift in shifts:
        try:
            start, end = interval(shift["date"], shift["start_time"], shift["end_time"])
        except (KeyError, ValueError):
            continue
        intervals.append((str(shift.get("assignee_id")), start, end))
    gaps = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        for rule in minimums:
            if rule.get("weekday") is not None and int(rule["weekday"]) != day.weekday():
                continue
            slot = datetime.combine(day, _parse_time(rule["start_time"]))
            rule_end = datetime.combine(day, _parse_time(rule["end_time"]))
            required = int(rule["min_staff"])
            current = None
            while slot < rule_end:
                nxt = slot + timedelta(minutes=15)
                planned = len({who for who, start, end in intervals if start < nxt and slot < end})
                if planned < required:
                    if current and current["planned_min"] == planned and current["_end"] == slot:
                        current["_end"] = nxt
                    else:
                        current = {"date": day.isoformat(), "required": required, "planned_min": planned,
                                   "_start": slot, "_end": nxt, "rule_id": rule.get("id")}
                        gaps.append(current)
                else:
                    current = None
                slot = nxt
    for gap in gaps:
        gap["start_time"] = gap.pop("_start").strftime("%H:%M")
        gap["end_time"] = gap.pop("_end").strftime("%H:%M")
    gaps.sort(key=lambda g: (g["date"], g["start_time"]))
    return gaps

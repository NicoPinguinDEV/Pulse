"""Phase 7 evaluations as pure functions (no Discord, no I/O – callers pass the data).

F-05 capacity heatmap: tickets and voice cases per weekday/hour against staff on duty (Berlin time).
F-06 promotion readiness: configurable criteria per rank → a hint only. Decisions stay with people and the
     existing 4-eyes flow; nothing here changes roles or creates requests.
F-10 "Mein Monat": personal monthly review (hours, tickets, ratings, trainings, achievements).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from pulse_time import BERLIN, parse_timestamp

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
MAX_SHIFT_SECONDS = 24 * 3600


def _local(value) -> datetime | None:
    dt = parse_timestamp(value)
    return dt.astimezone(BERLIN) if dt else None


# --------------------------------------------------------------------------- F-05
def heatmap(tickets: list[dict], voice_cases: list[dict], shift_history: list[dict], *, now: datetime, days: int = 28) -> dict:
    """{"demand": 7×24, "staff": 7×24 (average people on duty), "weeks": n, "hints": [...]}.

    demand = average tickets + voice cases started in that hour per week; staff = average staff-hours.
    """
    days = max(7, min(int(days), 120))
    start = now - timedelta(days=days)
    weeks = days / 7
    demand = [[0.0] * 24 for _ in range(7)]
    staff = [[0.0] * 24 for _ in range(7)]
    for item in tickets:
        dt = _local(item.get("opened_at"))
        if dt and start <= dt <= now:
            demand[dt.weekday()][dt.hour] += 1
    for item in voice_cases:
        dt = _local(item.get("created_at") or item.get("support_started_at") or item.get("closed_at"))
        if dt and start <= dt <= now:
            demand[dt.weekday()][dt.hour] += 1
    for shift in shift_history:
        begin = _local(shift.get("started_at_iso"))
        if not begin:
            continue
        end = _local(shift.get("ended_at_iso"))
        if not end:
            end = begin + timedelta(seconds=min(int(shift.get("duration_seconds") or 0), MAX_SHIFT_SECONDS))
        end = min(end, begin + timedelta(seconds=MAX_SHIFT_SECONDS), now)
        cursor = max(begin, start)
        while cursor < end:  # spread the shift over the hours it covered
            slot_end = min(end, cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            staff[cursor.weekday()][cursor.hour] += (slot_end - cursor).total_seconds() / 3600
            cursor = slot_end
    demand = [[round(v / weeks, 2) for v in row] for row in demand]
    staff = [[round(v / weeks, 2) for v in row] for row in staff]
    hints = []
    for d in range(7):
        for h in range(24):
            if demand[d][h] >= 1 and staff[d][h] < 0.5:
                hints.append({"day": d, "hour": h, "kind": "unbesetzt", "demand": demand[d][h], "staff": staff[d][h]})
            elif staff[d][h] >= 0.5 and demand[d][h] / staff[d][h] >= 4:
                hints.append({"day": d, "hour": h, "kind": "knapp", "demand": demand[d][h], "staff": staff[d][h]})
    hints.sort(key=lambda x: (x["kind"] != "unbesetzt", -x["demand"]))
    return {"demand": demand, "staff": staff, "weeks": round(weeks, 1), "hints": hints[:12]}


# --------------------------------------------------------------------------- F-06
CRITERIA_KEY = "promotion_readiness_criteria"
CRITERIA_FIELDS = {
    "hours_30d": ("Dienststunden (30 Tage)", 10),
    "tickets_30d": ("Tickets bearbeitet (30 Tage)", 5),
    "rating_min": ("Ø Ticket-Bewertung mindestens", 4.0),
    "trainings": ("Bestandene Schulungen", 1),
    "warn_free_days": ("Tage ohne aktive Warnung", 30),
    "days_in_team": ("Tage im Team", 30),
}
DEFAULT_CRITERIA = {key: default for key, (_, default) in CRITERIA_FIELDS.items()}


def normalize_criteria(raw) -> dict:
    """{"default": {...}, "<role_id>": {...}} with numbers only; unknown keys dropped."""
    out = {}
    for rank, values in (raw or {}).items() if isinstance(raw, dict) else ():
        if not isinstance(values, dict):
            continue
        clean = {}
        for key, (_, default) in CRITERIA_FIELDS.items():
            try:
                value = float(values.get(key, default))
            except (TypeError, ValueError):
                value = float(default)
            clean[key] = max(0.0, min(value, 10000.0))
        out[str(rank)] = clean
    out.setdefault("default", {k: float(v) for k, v in DEFAULT_CRITERIA.items()})
    return out


def member_stats(user_id, *, now: datetime, shift_history: list[dict], tickets: list[dict], attempts: list[dict],
                 entry: dict | None, active_warns: list[dict]) -> dict:
    uid = str(user_id)
    since = now - timedelta(days=30)
    seconds = 0
    for shift in shift_history:
        if str(shift.get("mod_id")) != uid:
            continue
        dt = _local(shift.get("started_at_iso"))
        if dt and dt >= since:
            seconds += int(shift.get("duration_seconds") or 0)  # same crediting as the weekly totals
    mine = [t for t in tickets if str(t.get("claimed_by_id")) == uid and t.get("status") == "closed"]
    recent = [t for t in mine if (_local(t.get("closed_at")) or now - timedelta(days=999)) >= since]
    ratings = [int(t["rating"]) for t in mine if t.get("rating")]
    passed = {a.get("training_id") for a in attempts if str(a.get("user_id")) == uid and int(a.get("passed") or 0)}
    entry = entry or {}
    last_warn = None
    for warn in entry.get("warns_list", []) or []:
        dt = _local(warn.get("date_iso") or warn.get("created_at") or warn.get("date"))  # "%d.%m.%Y %H:%M" or "N/A"
        if dt and (last_warn is None or dt > last_warn):
            last_warn = dt
    since_team = _local(entry.get("team_since"))
    probation = entry.get("probation")
    return {
        "hours_30d": round(seconds / 3600, 1),
        "tickets_30d": len(recent),
        "rating_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "trainings": len(passed),
        "active_warns": len(active_warns),
        "warn_free_days": None if active_warns else ((now - last_warn).days if last_warn else None),
        "days_in_team": (now - since_team).days if since_team else None,
        "probation_active": bool(isinstance(probation, dict) and probation.get("active")),
    }


def readiness(stats: dict, criteria: dict) -> dict:
    """{"ready": bool, "checks": [(label, value_text, target_text, ok)]} – a hint, never an action."""
    checks = []

    def add(label, value, target, ok):
        checks.append((label, value, target, bool(ok)))
    add("Dienststunden (30 Tage)", f"{stats['hours_30d']:g} h", f"≥ {criteria['hours_30d']:g} h", stats["hours_30d"] >= criteria["hours_30d"])
    add("Tickets (30 Tage)", str(stats["tickets_30d"]), f"≥ {criteria['tickets_30d']:g}", stats["tickets_30d"] >= criteria["tickets_30d"])
    rating = stats["rating_avg"]
    add("Ø Bewertung", "–" if rating is None else f"{rating:g}", f"≥ {criteria['rating_min']:g}",
        criteria["rating_min"] <= 0 or (rating is not None and rating >= criteria["rating_min"]))
    add("Schulungen", str(stats["trainings"]), f"≥ {criteria['trainings']:g}", stats["trainings"] >= criteria["trainings"])
    free = stats["warn_free_days"]
    warn_ok = stats["active_warns"] == 0 and (free is None or free >= criteria["warn_free_days"])
    add("Warnfrei", "aktive Warnung" if stats["active_warns"] else ("nie verwarnt" if free is None else f"{free} Tage"),
        f"≥ {criteria['warn_free_days']:g} Tage", warn_ok)
    days = stats["days_in_team"]
    add("Im Team", "unbekannt" if days is None else f"{days} Tage", f"≥ {criteria['days_in_team']:g} Tage",
        criteria["days_in_team"] <= 0 or (days is not None and days >= criteria["days_in_team"]))
    add("Probezeit", "läuft" if stats["probation_active"] else "keine", "beendet", not stats["probation_active"])
    return {"ready": all(c[3] for c in checks), "checks": checks}


# --------------------------------------------------------------------------- F-10
def month_bounds(now: datetime, previous: bool = False) -> tuple[datetime, datetime]:
    local = now.astimezone(BERLIN)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if previous:
        end = start
        start = (start - timedelta(days=1)).replace(day=1)
        return start, end
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


def month_review(user_id, *, start: datetime, end: datetime, shift_history: list[dict], tickets: list[dict],
                 attempts: list[dict], unlocked: list[dict]) -> dict:
    uid = str(user_id)

    def inside(value):
        dt = _local(value)
        return dt is not None and start <= dt < end
    shifts = [s for s in shift_history if str(s.get("mod_id")) == uid and inside(s.get("started_at_iso"))]
    seconds = sum(int(s.get("duration_seconds") or 0) for s in shifts)  # same crediting as the weekly totals
    closed = [t for t in tickets if str(t.get("claimed_by_id")) == uid and t.get("status") == "closed" and inside(t.get("closed_at"))]
    ratings = [int(t["rating"]) for t in closed if t.get("rating")]
    passed = [a for a in attempts if str(a.get("user_id")) == uid and int(a.get("passed") or 0) and inside(a.get("created_at"))]
    achievements = [a for a in unlocked if inside(a.get("unlocked_at"))]
    return {
        "label": f"{MONTHS[start.month - 1]} {start.year}",
        "hours": round(seconds / 3600, 1), "shifts": len(shifts),
        "tickets": len(closed), "rating_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "ratings": len(ratings), "trainings": len(passed), "achievements": [a.get("name") or a.get("achievement_id") for a in achievements],
    }


def month_text(review: dict) -> str:
    rating = "–" if review["rating_avg"] is None else f"{review['rating_avg']:g} ★ ({review['ratings']})"
    lines = [f"📅 **Dein {review['label']} bei Pulse**",
             f"⏱ Dienstzeit: {review['hours']:g} h in {review['shifts']} Schichten",
             f"🎫 Tickets abgeschlossen: {review['tickets']} · Ø Bewertung {rating}",
             f"🎓 Schulungen bestanden: {review['trainings']}"]
    if review["achievements"]:
        lines.append("🏅 Neue Erfolge: " + ", ".join(str(a) for a in review["achievements"][:10]))
    lines.append("Danke für deinen Einsatz! (Abbestellen: Mein Pulse → Mein Monat)")
    return "\n".join(lines)[:1900]

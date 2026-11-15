"""Audit #71, #73, #75, #76, #81, #87, #88, #89, #92, #94: consolidated team file and team-planning analytics.

All analytics are *pure, read-only functions* on plain data (shift history, tasks, tickets, absences) - no storage
of their own except the development goals (``team_dev_goals``, #75). Every indicator names its definition and
thresholds, missing data is shown as missing (never guessed), and nothing here decides about a person: results
are hints for leadership (no automatic promotion, warning or removal).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from statistics import median

import pulse_db
from pulse_shift_utils import credited_shift_seconds, needs_duration_confirmation

# ----------------------------------------------------------------------------------------------- shared helpers
PRIORITY_WEIGHT = {"urgent": 3.0, "high": 2.0, "normal": 1.0, "low": 0.5}
TICKET_WEIGHT = 1.5
OVERDUE_EXTRA = 1.0
LOAD_HIGH = 6.0          # weighted points: from here "hoch"
LOAD_CRITICAL = 10.0     # from here "überlastet"
MIN_SAMPLES = 5          # anomaly detection needs at least this many shifts of a person
MIN_ABS_HOURS = 4.0      # a shift shorter than this is never flagged as "too long"
MAD_FACTOR = 3.5         # robust outlier limit: median + 3.5 * 1.4826 * MAD
MAX_SHIFTS_PER_DAY = 3
SHORT_SHIFT_SECONDS = 300


def _dt(value) -> datetime | None:
    """Aware UTC datetime from ISO text; naive values are treated as UTC (legacy), garbage as ``None``."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _day(value) -> date | None:
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _uid(value) -> str:
    return str(value or "").strip()


def _shifts_of(history, uid=None, since: datetime | None = None, until: datetime | None = None):
    for shift in history or ():
        if not isinstance(shift, dict) or (uid is not None and _uid(shift.get("mod_id")) != uid):
            continue
        start = _dt(shift.get("started_at_iso"))
        if start is None or (since and start < since) or (until and start >= until):
            continue
        yield shift, start


# -------------------------------------------------------------------------------------------- #81 Team-Auslastung
def workload(members, tasks, tickets, now: datetime | None = None) -> list[dict]:
    """Current load per team member. ``members`` = iterable of ``(id, name)``. Open tasks count by priority
    weight (+1 if overdue), open claimed tickets 1.5 each. Levels: ok < 6 <= hoch < 10 <= überlastet."""
    now = now or datetime.now(timezone.utc)
    rows = {_uid(i): {"id": _uid(i), "name": str(n), "tasks": 0, "overdue": 0, "tickets": 0, "points": 0.0}
            for i, n in members}
    for task in tasks or ():
        row = rows.get(_uid(task.get("assignee_id")))
        if row is None or str(task.get("status") or "open") in ("done", "archived", "closed"):
            continue
        row["tasks"] += 1
        row["points"] += PRIORITY_WEIGHT.get(str(task.get("priority") or "normal"), 1.0)
        due = _dt(task.get("due_at"))
        if due and due < now:
            row["overdue"] += 1
            row["points"] += OVERDUE_EXTRA
    for ticket in tickets or ():
        row = rows.get(_uid(ticket.get("claimed_by_id")))
        if row is None or str(ticket.get("status") or "").lower() == "closed":
            continue
        row["tickets"] += 1
        row["points"] += TICKET_WEIGHT
    for row in rows.values():
        row["points"] = round(row["points"], 1)
        row["level"] = "überlastet" if row["points"] >= LOAD_CRITICAL else "hoch" if row["points"] >= LOAD_HIGH else "ok"
    return sorted(rows.values(), key=lambda r: (-r["points"], r["name"].casefold()))


# ----------------------------------------------------------------------------------------- #89 Dienstzeit-Anomalien
def shift_anomalies(history, now: datetime | None = None, days: int = 90, max_shift_hours: float = 12.0) -> list[dict]:
    """Shifts worth a second look, each with the rule that flagged it. Rules (all explainable):
    ``limit`` longer than the configured maximum; ``outlier`` far above the person's own typical length
    (median + 3.5 robust deviations, at least 4 h, needs 5 shifts); ``unconfirmed`` auto-closed without confirmed
    duration; ``short`` under 5 minutes; ``many`` more than 3 shifts on one day. A flag is a prompt to check, not a finding."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, int(days)))
    by_person: dict[str, list[tuple[dict, datetime, int]]] = {}
    for shift, start in _shifts_of(history, since=since):
        by_person.setdefault(_uid(shift.get("mod_id")), []).append((shift, start, int(credited_shift_seconds(shift))))
    out = []

    def flag(uid, shift, start, seconds, rule, text):
        out.append({"user_id": uid, "name": str(shift.get("mod_name") or shift.get("name") or uid), "at": start.isoformat(timespec="minutes"),
                    "hours": round(seconds / 3600, 1), "rule": rule, "text": text})
    for uid, rows in by_person.items():
        credited = [s for _sh, _st, s in rows if s > 0]
        base = limit = None
        if len(credited) >= MIN_SAMPLES:
            base = median(credited)
            mad = median(abs(s - base) for s in credited)
            limit = max(base + MAD_FACTOR * 1.4826 * mad, MIN_ABS_HOURS * 3600)
        per_day: dict[date, int] = {}
        for shift, start, seconds in rows:
            per_day[start.date()] = per_day.get(start.date(), 0) + 1
            if seconds > max_shift_hours * 3600:
                flag(uid, shift, start, seconds, "limit", f"länger als das Maximum von {max_shift_hours:g} h")
            elif limit and seconds > limit:
                flag(uid, shift, start, seconds, "outlier", f"deutlich länger als üblich (typisch {base / 3600:.1f} h)")
            if needs_duration_confirmation(shift):
                flag(uid, shift, start, seconds, "unconfirmed", "automatisch beendet, Dauer noch nicht bestätigt")
            if 0 < seconds < SHORT_SHIFT_SECONDS:
                flag(uid, shift, start, seconds, "short", "kürzer als 5 Minuten")
        for shift, start, seconds in rows:
            if per_day[start.date()] > MAX_SHIFTS_PER_DAY and shift is next(r[0] for r in rows if r[1].date() == start.date()):
                flag(uid, shift, start, seconds, "many", f"{per_day[start.date()]} Schichten an einem Tag")
    return sorted(out, key=lambda r: r["at"], reverse=True)


# ------------------------------------------------------------------------------------- #92 Verfügbarkeitsmatrix
def availability_matrix(history, now: datetime | None = None, weeks: int = 8, tz=None) -> dict:
    """Typical availability from the last ``weeks`` weeks of real shifts: ``cells[weekday][hour]`` = average number of
    different people on duty in that hour (per calendar week of the window), ``people[uid]`` = share of the weeks in
    which the person was on duty in that weekday/hour. A forecast from history, not a promise."""
    now = now or datetime.now(timezone.utc)
    weeks = max(1, min(int(weeks), 26))
    since = now - timedelta(weeks=weeks)
    seen: dict[tuple[int, int], dict[str, set]] = {}
    names: dict[str, str] = {}
    for shift, start in _shifts_of(history, since=since, until=now):
        seconds = int(credited_shift_seconds(shift))
        if seconds <= 0:
            continue
        uid = _uid(shift.get("mod_id"))
        names[uid] = str(shift.get("mod_name") or shift.get("name") or uid)
        cursor, end = start.astimezone(tz) if tz else start, (start + timedelta(seconds=seconds)).astimezone(tz) if tz else start + timedelta(seconds=seconds)
        slot = cursor.replace(minute=0, second=0, microsecond=0)
        while slot < end and (slot.date() - since.date()).days <= weeks * 7 + 1:
            week = (now.date() - slot.date()).days // 7
            seen.setdefault((slot.weekday(), slot.hour), {}).setdefault(uid, set()).add(week)
            slot += timedelta(hours=1)
    cells = [[0.0] * 24 for _ in range(7)]
    people: dict[str, list[list[float]]] = {}
    for (wd, hour), by_uid in seen.items():
        cells[wd][hour] = round(sum(len(w) for w in by_uid.values()) / weeks, 2)
        for uid, wk in by_uid.items():
            people.setdefault(uid, [[0.0] * 24 for _ in range(7)])[wd][hour] = round(len(wk) / weeks, 2)
    return {"weeks": weeks, "cells": cells, "people": people, "names": names}


# ------------------------------------------------------------------------------------ #94 Leistungsübersicht
WEIGHTS = {"hours": 0.30, "tickets": 0.25, "tasks": 0.20, "trainings": 0.15, "rating": 0.10}


def performance(rows, targets: dict | None = None) -> list[dict]:
    """Multi-signal overview. Each row: ``hours`` (30 d), ``tickets`` (30 d), ``tasks_done`` (30 d), ``trainings``,
    ``rating`` (1-5 or None) and ``absent_days`` (of 30). Every component is the share of its *target* (capped at
    100 %), the hours target is reduced pro rata for absent days, a missing signal is left out and the weights of the
    remaining ones are renormalized - so absence and missing data never count against a person. Active warnings are
    shown separately, never as a score deduction."""
    t = {"hours": 12.0, "tickets": 10.0, "tasks": 5.0, "trainings": 3.0, "rating": 4.5, **(targets or {})}
    out = []
    for row in rows or ():
        present = max(0.0, 1 - min(30, max(0, int(row.get("absent_days") or 0))) / 30)
        parts = {}
        if present > 0.1:
            parts["hours"] = min(1.0, float(row.get("hours") or 0) / (t["hours"] * present))
        parts["tickets"] = min(1.0, float(row.get("tickets") or 0) / t["tickets"])
        parts["tasks"] = min(1.0, float(row.get("tasks_done") or 0) / t["tasks"])
        if row.get("trainings") is not None:
            parts["trainings"] = min(1.0, float(row["trainings"]) / t["trainings"])
        if row.get("rating") is not None:
            parts["rating"] = min(1.0, float(row["rating"]) / t["rating"])
        weight = sum(WEIGHTS[k] for k in parts)
        score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()) / weight) if weight else None
        out.append({"id": _uid(row.get("id")), "name": str(row.get("name") or row.get("id")), "score": score,
                    "parts": {k: round(100 * v) for k, v in parts.items()}, "warns": int(row.get("warns") or 0),
                    "absent_days": int(row.get("absent_days") or 0), "missing": [k for k in WEIGHTS if k not in parts]})
    return sorted(out, key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["name"].casefold()))


# ---------------------------------------------------------------------------------------- #87 Abwesenheitskalender
def absence_calendar(members, absences: dict, history, events=(), start: date | None = None, days: int = 14) -> dict:
    """Per member and day one state: ``absent`` (registered absence, wins), ``shift`` (worked or planned shift) or
    ``event`` (calendar event naming the person) - merged from absences, shifts and the team calendar.
    ``absences`` = ``{uid: {"von": .., "bis": ..}}`` (both date formats); ``events`` = ``[{"day": date, "user_ids": [...]}]``."""
    start = start or datetime.now(timezone.utc).date()
    days = max(1, min(int(days), 62))
    dates = [start + timedelta(days=i) for i in range(days)]
    grid = {_uid(i): {"id": _uid(i), "name": str(n), "days": {}} for i, n in members}
    for uid, absence in (absences or {}).items():
        row, von, bis = grid.get(_uid(uid)), _day((absence or {}).get("von")), _day((absence or {}).get("bis"))
        if row is None or bis is None:
            continue
        for d in dates:
            if (von or bis) <= d <= bis:
                row["days"][d.isoformat()] = "absent"
    horizon = datetime.combine(dates[0], datetime.min.time(), timezone.utc), datetime.combine(dates[-1] + timedelta(days=1), datetime.min.time(), timezone.utc)
    for shift, st in _shifts_of(history, since=horizon[0], until=horizon[1]):
        row = grid.get(_uid(shift.get("mod_id")))
        if row is not None:
            row["days"].setdefault(st.date().isoformat(), "shift")
    for event in events or ():
        day = event.get("day")
        for uid in event.get("user_ids", ()):
            row = grid.get(_uid(uid))
            if row is not None and day is not None:
                row["days"].setdefault(day.isoformat(), "event")
    return {"dates": [d.isoformat() for d in dates], "rows": sorted(grid.values(), key=lambda r: r["name"].casefold())}


# ------------------------------------------------------------------------------------ #88 Wochen-/Monatsberichte
KPI_DEFINITIONS = {
    "hours": "Summe der angerechneten Dienstzeit (unbestätigte automatische Beendigungen zählen 0 h).",
    "shifts": "Anzahl begonnener Schichten mit angerechneter Dauer > 0.",
    "active": "Teammitglieder mit mindestens einer angerechneten Schicht im Zeitraum.",
    "avg_shift_h": "Angerechnete Stunden geteilt durch Schichten.",
    "tickets_closed": "Im Zeitraum geschlossene Tickets mit Bearbeiter.",
    "tasks_done": "Im Zeitraum erledigte Aufgaben (Status 'done' mit Änderungsdatum im Zeitraum).",
}


def _period(history, tickets, tasks, start: datetime, end: datetime) -> dict:
    seconds, shifts, people = 0, 0, set()
    for shift, _st in _shifts_of(history, since=start, until=end):
        credited = int(credited_shift_seconds(shift))
        if credited > 0:
            seconds, shifts = seconds + credited, shifts + 1
            people.add(_uid(shift.get("mod_id")))
    closed = sum(1 for t in tickets or () if t.get("claimed_by_id") and str(t.get("status")).lower() == "closed"
                 and (c := _dt(t.get("closed_at"))) and start <= c < end)
    done = sum(1 for t in tasks or () if str(t.get("status")) == "done" and (u := _dt(t.get("updated_at"))) and start <= u < end)
    return {"hours": round(seconds / 3600, 1), "shifts": shifts, "active": len(people),
            "avg_shift_h": round(seconds / 3600 / shifts, 1) if shifts else None, "tickets_closed": closed, "tasks_done": done}


def report(history, tickets, tasks, now: datetime | None = None, kind: str = "week") -> dict:
    """Last *full* week (Mon-Sun) or month against the one before; ``delta`` is the absolute change per KPI."""
    now = now or datetime.now(timezone.utc)
    today = datetime.combine(now.date(), datetime.min.time(), timezone.utc)
    if kind == "month":
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        prev_start = (start - timedelta(days=1)).replace(day=1)
    else:
        end = today - timedelta(days=today.weekday())
        start, prev_start = end - timedelta(days=7), end - timedelta(days=14)
    cur, prev = _period(history, tickets, tasks, start, end), _period(history, tickets, tasks, prev_start, start)
    delta = {k: (None if cur[k] is None or prev[k] is None else round(cur[k] - prev[k], 1)) for k in cur}
    return {"kind": kind, "start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat(),
            "current": cur, "previous": prev, "delta": delta, "definitions": KPI_DEFINITIONS}


# ------------------------------------------------------------------------------------- #75 Entwicklungsziele
GOAL_STATES = ("open", "done", "dropped")


def add_goal(user_id, title, detail="", due="", actor_id="", actor_name="") -> dict:
    title = str(title or "").strip()
    if not title or len(title) > 160:
        raise ValueError("Der Titel fehlt oder ist länger als 160 Zeichen.")
    due_day = _day(due) if str(due or "").strip() else None
    if str(due or "").strip() and due_day is None:
        raise ValueError("Ungültiges Fälligkeitsdatum.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {"id": "dev_" + secrets.token_hex(6), "user_id": _uid(user_id), "title": title, "detail": str(detail or "")[:1000],
           "due": due_day.isoformat() if due_day else "", "status": "open", "actor_id": _uid(actor_id)[:30],
           "actor_name": str(actor_name or "")[:100], "created_at": now, "updated_at": now}
    with pulse_db.connect() as db:
        db.execute("INSERT INTO team_dev_goals (id,user_id,title,detail,due,status,actor_id,actor_name,created_at,updated_at) "
                   "VALUES (:id,:user_id,:title,:detail,:due,:status,:actor_id,:actor_name,:created_at,:updated_at)", row)
    return row


def goals(user_id, limit: int = 50) -> list[dict]:
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM team_dev_goals WHERE user_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
                          "due='' , due, created_at DESC LIMIT ?", (_uid(user_id), max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


def set_goal_status(goal_id, user_id, status) -> bool:
    """Change the state of one goal of this person; returns ``False`` if it does not exist (never touches others)."""
    if status not in GOAL_STATES:
        raise ValueError("Unbekannter Status.")
    with pulse_db.connect() as db:
        return db.execute("UPDATE team_dev_goals SET status=?, updated_at=? WHERE id=? AND user_id=?",
                          (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(goal_id), _uid(user_id))).rowcount > 0


# ------------------------------------------------------------------------------------- #76 Beförderungspaket
def promotion_package(stats: dict, readiness: dict, prev_hours: float | None, open_goals: list[dict],
                      warns_visible: bool, absent_days: int = 0) -> dict:
    """Decision package for leadership: criteria check (``readiness``), trend, open development goals and explicit
    data gaps. Always a *recommendation basis* - the decision and its four-eyes approval stay with people."""
    met = [c for c in readiness["checks"] if c[3]]
    missing = [c for c in readiness["checks"] if not c[3]]
    gaps = []
    if stats.get("rating_avg") is None:
        gaps.append("keine Ticket-Bewertungen vorhanden")
    if stats.get("days_in_team") is None:
        gaps.append("Teameintritt unbekannt")
    if not warns_visible:
        gaps.append("Warnungen für dich nicht einsehbar")
    trend = None
    if prev_hours is not None:
        diff = round(stats["hours_30d"] - prev_hours, 1)
        trend = {"previous": prev_hours, "diff": diff, "direction": "steigend" if diff > 1 else "fallend" if diff < -1 else "stabil"}
    return {"ready": bool(readiness["ready"]), "met": met, "missing": missing, "trend": trend, "gaps": gaps,
            "open_goals": [g for g in open_goals if g.get("status") == "open"], "absent_days": int(absent_days),
            "note": "Entscheidungsgrundlage – keine automatische Beförderung."}


# --------------------------------------------------------------------------------------------- #71/#73 Teamakte
def dossier(*, uid, name: str, stats: dict, workload_row: dict | None, anomalies: list[dict], goals_: list[dict],
            absence: dict | None, certificates: int = 0, departments=(), open_checklist: str = "",
            warns: int | None = None) -> dict:
    """One compact 360° overview of a person from already collected facts (``warns=None`` = not visible to the viewer)."""
    return {"id": _uid(uid), "name": name, "hours_30d": stats.get("hours_30d"), "tickets_30d": stats.get("tickets_30d"),
            "rating_avg": stats.get("rating_avg"), "trainings": stats.get("trainings"), "certificates": certificates,
            "days_in_team": stats.get("days_in_team"), "probation_active": bool(stats.get("probation_active")),
            "warns": warns, "load": workload_row, "anomalies": len(anomalies),
            "goals_open": sum(1 for g in goals_ if g.get("status") == "open"), "absence": absence,
            "departments": list(departments), "checklist": open_checklist}

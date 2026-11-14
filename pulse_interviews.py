"""Audit #99: interview calendar – interview appointments as real calendar objects linked to the application.

No second calendar and no new table. The sources of truth stay where they were:
- ``applications.json`` (the application record): ``interview_at`` / ``interview_end`` (German local ISO time with
  offset), ``interview_event_id`` (link to the calendar row), ``interview_lead_id``/``_name`` (responsible leader),
  ``interview_scheduled_by(_id)`` (organizer), ``interview_state`` (``scheduled``/``cancelled``) and
  ``interview_revision`` (optimistic lock). Participants are *derived* (applicant ``user_id``, reviewer
  ``assignments`` from #96, leader, organizer) – no person data is copied into the calendar row.
- ``pulse_db.calendar_events`` (category ``interview``): neutral title, no applicant identity. The reverse link
  (event → application) is the record's ``interview_event_id``; nothing else is stored.

Everything that changes both runs under one lock with a fixed order (new calendar row → save application →
delete old row) so a failure never leaves an orphaned or doubled entry. Pure functions take ``apps`` and the
persistence callables as arguments, which keeps them testable without Discord.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

EVENT_CATEGORY = "interview"
EVENT_TITLE = "Bewerbungsgespräch"
STATE_SCHEDULED = "scheduled"
STATE_CANCELLED = "cancelled"
DEFAULT_MINUTES = 30
MIN_MINUTES, MAX_MINUTES = 15, 240
DURATIONS = (15, 30, 45, 60, 90, 120)
REMIND_BEFORE = timedelta(hours=1)
HEADS_UP_BEFORE = timedelta(hours=24)
STATUS_LABELS = {"scheduled": "Geplant", "running": "Läuft", "done": "Beendet", "closed": "Bewerbung entschieden",
                 "cancelled": "Abgesagt"}
ROLE_LABELS = {"applicant": "Bewerber", "reviewer": "Prüfer", "lead": "Führungskraft", "organizer": "Terminverantwortlich"}

_LOCK = threading.RLock()  # one process: serialises "check conflicts + write" for concurrent changes


class InterviewError(ValueError):
    """User-facing refusal (German message)."""


class InterviewConflict(InterviewError):
    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        super().__init__(describe_conflicts(conflicts))


def _id(value) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() and len(text) <= 25 else ""


def _parse(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None  # a naive time has no defined instant: never guess it


def revision(item: dict) -> int:
    try:
        return max(0, int(item.get("interview_revision") or 0))
    except (TypeError, ValueError):
        return 0


def duration_minutes(value) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise InterviewError("Bitte eine gültige Gesprächsdauer wählen.") from None
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise InterviewError(f"Die Gesprächsdauer muss zwischen {MIN_MINUTES} und {MAX_MINUTES} Minuten liegen.")
    return minutes


def window(item: dict) -> tuple[datetime, datetime] | None:
    """(start, end) of a stored interview in UTC; a missing/invalid end falls back to the former 30 minutes."""
    if not isinstance(item, dict):
        return None
    start = _parse(item.get("interview_at"))
    if start is None:
        return None
    end = _parse(item.get("interview_end"))
    if end is None or end <= start:
        end = start + timedelta(minutes=DEFAULT_MINUTES)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def is_scheduled(item: dict) -> bool:
    return isinstance(item, dict) and item.get("interview_state") != STATE_CANCELLED and window(item) is not None


def participants(item: dict) -> list[dict]:
    """[{id, name, roles}] – one entry per person; derived from the record, never stored a second time."""
    import pulse_application_review as review
    found: dict[str, dict] = {}

    def add(person_id, name, role):
        person_id = _id(person_id)
        if person_id:
            entry = found.setdefault(person_id, {"id": person_id, "name": str(name or "")[:80], "roles": []})
            if role not in entry["roles"]:
                entry["roles"].append(role)
            if name and not entry["name"]:
                entry["name"] = str(name)[:80]

    add(item.get("user_id"), item.get("name"), "applicant")
    for entry in review.assignments(item):
        add(entry["reviewer_id"], entry["reviewer_name"], "reviewer")
    add(item.get("interview_lead_id"), item.get("interview_lead_name"), "lead")
    add(item.get("interview_scheduled_by_id"), item.get("interview_scheduled_by"), "organizer")
    return list(found.values())


def team_ids(item: dict) -> list[str]:
    applicant = _id(item.get("user_id"))
    return [p["id"] for p in participants(item) if p["id"] != applicant]


def status_key(item: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if item.get("interview_state") == STATE_CANCELLED:
        return "cancelled"
    span = window(item)
    if span is None:
        return "closed"
    if item.get("status") != "interview":
        return "closed"
    if now < span[0]:
        return "scheduled"
    return "running" if now < span[1] else "done"


# --------------------------------------------------------------------------------------------- conflicts
def find_conflicts(apps: dict, app_id: str, start: datetime, end: datetime, *, applicant_id: str,
                   reviewer_ids=(), lead_id: str = "") -> list[dict]:
    """Overlaps (half-open intervals) of the same applicant, reviewer or leader with other scheduled interviews.

    Unknown data is never treated as free: an open interview whose time cannot be read is reported as
    ``unknown`` and blocks the booking until a human has looked at it."""
    wanted = {"applicant": {applicant_id} - {""}, "reviewer": {str(r) for r in reviewer_ids} - {""},
              "lead": {lead_id} - {""}}
    conflicts = []
    for other_id, other in (apps.items() if isinstance(apps, dict) else []):
        if other_id == app_id or not isinstance(other, dict) or other.get("status") != "interview":
            continue
        if other.get("interview_state") == STATE_CANCELLED:
            continue
        if not str(other.get("interview_at") or "").strip():
            continue                                  # no appointment at all
        span = window(other)
        if span is None:
            conflicts.append({"app_id": other_id, "kind": "unknown", "person_id": "", "start": "", "end": ""})
            continue
        if not (start < span[1] and span[0] < end):
            continue
        their = {"applicant": {_id(other.get("user_id"))}, "reviewer": {p["id"] for p in participants(other) if "reviewer" in p["roles"]},
                 "lead": {_id(other.get("interview_lead_id"))}}
        for kind, mine in wanted.items():
            for person_id in sorted(mine & their[kind]):
                conflicts.append({"app_id": other_id, "kind": kind, "person_id": person_id,
                                  "start": span[0].isoformat(), "end": span[1].isoformat()})
    return conflicts


def describe_conflicts(conflicts: list[dict], tz=None) -> str:
    labels = {"applicant": "Der Bewerber hat bereits ein Gespräch", "reviewer": "Ein zugewiesener Prüfer ist bereits gebucht",
              "lead": "Die verantwortliche Führungskraft ist bereits gebucht",
              "unknown": "Ein anderer Gesprächstermin ist nicht lesbar – Überschneidung nicht ausschließbar"}
    parts = []
    for c in conflicts[:3]:
        when = ""
        if c.get("start"):
            try:
                local = datetime.fromisoformat(c["start"]).astimezone(tz) if tz else datetime.fromisoformat(c["start"])
                when = f" ({local.strftime('%d.%m. %H:%M')})"
            except ValueError:
                pass
        parts.append(f"{labels.get(c['kind'], 'Konflikt')}{when}, Bewerbung {c['app_id']}")
    more = f" – und {len(conflicts) - 3} weitere" if len(conflicts) > 3 else ""
    return "Terminkonflikt: " + "; ".join(parts) + more + "."


# --------------------------------------------------------------------------------------------- schedule / cancel
def _stale(item: dict, expected_revision) -> None:
    if expected_revision in (None, ""):
        return
    if str(expected_revision) != str(revision(item)):
        raise InterviewError("Der Termin wurde zwischenzeitlich geändert. Bitte die Seite neu laden und erneut prüfen.")


def _delete_event(db, event_id) -> bool:
    if not str(event_id or "").strip():
        return False
    try:
        return bool(db.delete_calendar_event(event_id))
    except Exception as exc:  # the stale row is cleaned up by reconcile(); never fail the booking over it
        print(f"⚠️ Alter Interview-Kalendereintrag nicht gelöscht: {type(exc).__name__}")
        return False


def schedule(apps: dict, app_id: str, *, start: datetime, minutes, lead_id="", lead_name="", actor_id: str, actor_name: str,
             save, db, expected_revision=None, now: datetime | None = None, load=None) -> dict:
    """Create or change the appointment. Returns {action, item, event_id, start, end, notify, old_start}.

    ``load`` (optional) re-reads the applications *inside* the lock, so a change that was written between the
    caller's read and this call is never overwritten and is part of the conflict check."""
    now = now or datetime.now(timezone.utc)
    minutes = duration_minutes(minutes)
    with _LOCK:
        if load is not None:
            apps = load()
        item = apps.get(app_id) if isinstance(apps, dict) else None
        if not isinstance(item, dict) or item.get("status") != "interview":
            raise InterviewError("Einen Termin gibt es nur für Bewerbungen in der Gesprächsphase.")
        _stale(item, expected_revision)
        if start.tzinfo is None:
            raise InterviewError("Der Gesprächsbeginn braucht eine Zeitzone.")
        start_utc = start.astimezone(timezone.utc)
        if start_utc <= now:
            raise InterviewError("Der Gesprächstermin muss in der Zukunft liegen.")
        end_utc = start_utc + timedelta(minutes=minutes)
        applicant_id = _id(item.get("user_id"))
        if not applicant_id:
            raise InterviewError("Der Bewerber ist nicht eindeutig zuordenbar – Termin und Konfliktprüfung sind nicht möglich.")
        lead_id = _id(lead_id) if str(lead_id or "").strip() else ""
        if str(lead_id or "") == applicant_id:
            raise InterviewError("Der Bewerber kann nicht selbst verantwortliche Führungskraft sein.")
        reviewers = [p["id"] for p in participants(item) if "reviewer" in p["roles"]]
        conflicts = find_conflicts(apps, app_id, start_utc, end_utc, applicant_id=applicant_id, reviewer_ids=reviewers, lead_id=lead_id)
        if conflicts:
            raise InterviewConflict(conflicts)

        previous = window(item) if is_scheduled(item) else None
        if previous == (start_utc, end_utc) and str(item.get("interview_lead_id") or "") == lead_id and item.get("interview_event_id"):
            return {"action": "unchanged", "item": item, "event_id": item["interview_event_id"], "start": start, "end": end_utc, "notify": [], "old_start": start_utc}

        old_event_id = str(item.get("interview_event_id") or "")
        try:  # 1) new calendar row first: when this fails nothing has changed
            event_id = db.create_calendar_event(
                EVENT_TITLE, start_utc.isoformat(timespec="seconds"), end_utc.isoformat(timespec="seconds"),
                EVENT_CATEGORY, "Discord", "", actor_id, actor_name)
        except Exception as exc:
            raise InterviewError("Der Kalendereintrag konnte nicht angelegt werden. Es wurde nichts geändert.") from exc
        snapshot = dict(item)
        item.update({
            "interview_at": start.isoformat(timespec="minutes"), "interview_end": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
            "interview_event_id": event_id, "interview_state": STATE_SCHEDULED, "interview_revision": revision(item) + 1,
            "interview_scheduled_by": actor_name, "interview_scheduled_by_id": str(actor_id),
            "interview_lead_id": lead_id, "interview_lead_name": str(lead_name or "")[:80] if lead_id else "",
            "interview_reminded": False, "interview_heads_up": False, "interview_delivery": {},
        })
        for key in ("interview_cancelled_at", "interview_cancelled_by", "interview_cancel_reason"):
            item.pop(key, None)
        try:  # 2) application record
            save(apps)
        except Exception as exc:
            item.clear()
            item.update(snapshot)
            _delete_event(db, event_id)
            raise InterviewError("Der Termin konnte nicht gespeichert werden. Es wurde nichts geändert.") from exc
        if old_event_id and old_event_id != event_id:  # 3) only now the replaced row goes away
            _delete_event(db, old_event_id)
        notify = [pid for pid in team_ids(item) if pid != str(actor_id)]
        return {"action": "moved" if previous else "created", "item": item, "event_id": event_id, "start": start, "end": end_utc,
                "notify": notify, "old_start": previous[0] if previous else None}


def cancel(apps: dict, app_id: str, *, actor_id: str, actor_name: str, save, db, reason: str = "",
           expected_revision=None, now: datetime | None = None, load=None) -> dict:
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        if load is not None:
            apps = load()
        item = apps.get(app_id) if isinstance(apps, dict) else None
        if not isinstance(item, dict) or item.get("status") != "interview":
            raise InterviewError("Nur Gespräche offener Bewerbungen können abgesagt werden.")
        _stale(item, expected_revision)
        if not is_scheduled(item):
            raise InterviewError("Für diese Bewerbung ist kein Gesprächstermin geplant.")
        old_event_id = str(item.get("interview_event_id") or "")
        span = window(item)
        old_start = span[0] if span else None
        notify = [pid for pid in team_ids(item) if pid != str(actor_id)]
        snapshot = dict(item)
        item.update({
            "interview_state": STATE_CANCELLED, "interview_cancelled_slot": item.get("interview_at"),
            "interview_cancelled_at": now.isoformat(timespec="seconds"), "interview_cancelled_by": actor_name,
            "interview_cancel_reason": str(reason or "").strip()[:200], "interview_revision": revision(item) + 1,
            "interview_at": "", "interview_end": "", "interview_event_id": "", "interview_reminded": True,
            "interview_heads_up": True,
        })
        try:
            save(apps)
        except Exception as exc:
            item.clear()
            item.update(snapshot)
            raise InterviewError("Die Absage konnte nicht gespeichert werden. Es wurde nichts geändert.") from exc
        _delete_event(db, old_event_id)
        return {"action": "cancelled", "item": item, "event_id": old_event_id, "old_start": old_start, "notify": notify}


def record_delivery(item: dict, kind: str, ok: bool, now: datetime | None = None) -> None:
    """Remember the outcome of the (single) applicant DM so a failure is visible and never retried in a loop."""
    delivery = item.get("interview_delivery") if isinstance(item.get("interview_delivery"), dict) else {}
    delivery[kind] = {"ok": bool(ok), "at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    item["interview_delivery"] = delivery


# --------------------------------------------------------------------------------------------- reminders
def due_notices(apps: dict, now: datetime) -> list[tuple[str, str]]:
    """[(app_id, 'reminder' | 'heads_up')]: reminder ≤ 1 h before start, team heads-up ≤ 24 h (once each)."""
    due = []
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict) or item.get("status") != "interview" or not is_scheduled(item):
            continue
        span = window(item)
        if span is None or span[0] < now:
            continue
        if not item.get("interview_reminded") and span[0] <= now + REMIND_BEFORE:
            due.append((app_id, "reminder"))
        elif not item.get("interview_heads_up") and not item.get("interview_reminded") and span[0] <= now + HEADS_UP_BEFORE:
            due.append((app_id, "heads_up"))
    return due


def orphaned_events(apps: dict, events: list[dict]) -> list[str]:
    """Calendar rows of category ``interview`` that no active appointment points to."""
    live = {str(i.get("interview_event_id")) for i in (apps.values() if isinstance(apps, dict) else [])
            if isinstance(i, dict) and i.get("interview_event_id")}  # also finished interviews stay as history
    return [e["id"] for e in events if e.get("category") == EVENT_CATEGORY and e["id"] not in live]


def reconcile(apps: dict, *, save, db, now: datetime | None = None) -> dict:
    """Keep application and calendar in step: appointments of applications that were decided in the meantime are
    closed, and ``interview`` rows without an owner are removed. Never touches other calendar categories."""
    now = now or datetime.now(timezone.utc)
    result = {"closed": 0, "orphans": 0}
    with _LOCK:
        dirty = []
        for item in (apps.values() if isinstance(apps, dict) else []):
            if (isinstance(item, dict) and item.get("status") != "interview" and item.get("interview_event_id")
                    and item.get("interview_state") != STATE_CANCELLED):
                span = window(item)
                if span is None or span[0] > now:  # decided before the date: the appointment is obsolete
                    dirty.append((item, str(item["interview_event_id"])))
        for item, event_id in dirty:
            item.update({"interview_state": STATE_CANCELLED, "interview_cancelled_at": now.isoformat(timespec="seconds"),
                         "interview_cancelled_by": "System", "interview_cancel_reason": "Bewerbung wurde vor dem Termin entschieden",
                         "interview_event_id": "", "interview_revision": revision(item) + 1})
        if dirty:
            save(apps)
            for _, event_id in dirty:
                _delete_event(db, event_id)
            result["closed"] = len(dirty)
        try:
            ids = orphaned_events(apps, db.list_calendar_events(limit=2000))
        except Exception:
            ids = []
        for event_id in ids:
            if _delete_event(db, event_id):
                result["orphans"] += 1
    return result


# --------------------------------------------------------------------------------------------- calendar view
def looks_like_interview(event: dict) -> bool:
    """Category ``interview`` or a former entry (title 'Bewerbungsgespräch <Name>' from before #99)."""
    return event.get("category") == EVENT_CATEGORY or str(event.get("title") or "").startswith(EVENT_TITLE)


def link_index(apps: dict) -> dict[str, tuple[str, dict]]:
    return {str(i["interview_event_id"]): (a, i) for a, i in (apps.items() if isinstance(apps, dict) else [])
            if isinstance(i, dict) and i.get("interview_event_id")}


def describe(event: dict, index: dict, *, privileged: bool, now: datetime | None = None) -> dict | None:
    """What the calendar may show for an interview row; ``None`` for other events.

    Without recruiting permission only generic facts (time, duration, status, headcount) leave this function –
    no applicant name, application number, answer or rating, and the stored title/description are ignored."""
    if not looks_like_interview(event) and str(event.get("id")) not in index:
        return None
    now = now or datetime.now(timezone.utc)
    start, end = _parse(event.get("starts_at")), _parse(event.get("ends_at"))
    info = {"title": EVENT_TITLE, "description": "", "status": "scheduled", "status_label": STATUS_LABELS["scheduled"],
            "minutes": int((end - start).total_seconds() // 60) if start and end and end > start else DEFAULT_MINUTES,
            "participants": [], "headcount": 0, "app_url": "", "linked": False}
    link = index.get(str(event.get("id")))
    if link is None:
        if privileged:
            info["status_label"] = "Kein Bewerbungsbezug"
            info["status"] = "orphan"
        return info
    app_id, item = link
    info["linked"] = True
    key = status_key(item, now)
    info.update(status=key, status_label=STATUS_LABELS[key])
    people = participants(item)
    info["headcount"] = len(people)
    if privileged:
        info["title"] = f"{EVENT_TITLE} {str(item.get('name') or app_id)[:60]}"
        info["app_url"] = f"/applications#app-{app_id}"
        info["participants"] = [{"name": p["name"] or "Unbekannt", "roles": [ROLE_LABELS[r] for r in p["roles"]]} for p in people]
    return info

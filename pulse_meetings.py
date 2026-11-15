"""Audit #51: Team-Besprechungsmodus - Agenda, Teilnehmer, Beschlüsse und daraus erzeugte Aufgaben in einem Ablauf.

Ablauf: ``planned`` (Agenda und Teilnehmende festlegen) → ``running`` (Anwesenheit, Beschlüsse, Aufgaben) → ``closed`` (Protokoll
wird unveränderlich und als Zusammenfassung in ``meetings_history`` abgelegt). Aufgaben aus Beschlüssen entstehen über die
gemeinsame Aufgabenregel (``pulse_task_actions.create``: Recht, Ereignis, Benachrichtigung). Die Besprechung trifft keine Personalentscheidung.

Speicher: ``meeting_sessions``, ``meeting_agenda``, ``meeting_participants``, ``meeting_decisions`` (alle im Backup).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import pulse_db

STATES = ("planned", "running", "closed")
ATTENDANCE = ("invited", "present", "absent")
MAX_TITLE, MAX_AGENDA, MAX_PARTICIPANTS, MAX_DECISIONS, MAX_TEXT = 160, 20, 100, 50, 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(db, meeting_id):
    row = db.execute("SELECT * FROM meeting_sessions WHERE id=?", (str(meeting_id),)).fetchone()
    return dict(row) if row else None


def create(title, scheduled_at, agenda, participants, *, facilitator_id="", facilitator_name="") -> dict:
    """``agenda``: list of titles; ``participants``: ``[(user_id, name)]``. Raises ``ValueError`` for invalid input."""
    head = str(title or "").strip()
    if not head or len(head) > MAX_TITLE:
        raise ValueError("Der Titel fehlt oder ist länger als 160 Zeichen.")
    items = [str(a).strip()[:MAX_TEXT] for a in (agenda or ()) if str(a).strip()]
    if not items:
        raise ValueError("Bitte mindestens einen Agendapunkt angeben.")
    if len(items) > MAX_AGENDA:
        raise ValueError(f"Höchstens {MAX_AGENDA} Agendapunkte.")
    people = [(str(uid).strip(), str(name or uid)[:100]) for uid, name in (participants or ()) if str(uid).strip().isdigit()]
    if len(people) > MAX_PARTICIPANTS:
        raise ValueError(f"Höchstens {MAX_PARTICIPANTS} Teilnehmende.")
    mid = "mtg_" + secrets.token_hex(6)
    with pulse_db.connect() as db:
        db.execute("INSERT INTO meeting_sessions (id,title,scheduled_at,status,facilitator_id,facilitator_name,created_at,closed_at) VALUES (?,?,?,?,?,?,?,?)",
                   (mid, head, str(scheduled_at or "")[:40], "planned", str(facilitator_id)[:30], str(facilitator_name)[:100], _now(), ""))
        for pos, text in enumerate(items, 1):
            db.execute("INSERT INTO meeting_agenda (meeting_id,pos,title) VALUES (?,?,?)", (mid, pos, text))
        for uid, name in dict(people).items():
            db.execute("INSERT INTO meeting_participants (meeting_id,user_id,user_name,attendance) VALUES (?,?,?,?)", (mid, uid, name, "invited"))
    return get(mid)


def get(meeting_id) -> dict | None:
    with pulse_db.connect() as db:
        meeting = _row(db, meeting_id)
        if not meeting:
            return None
        meeting["agenda"] = [dict(r) for r in db.execute("SELECT pos,title FROM meeting_agenda WHERE meeting_id=? ORDER BY pos", (meeting["id"],)).fetchall()]
        meeting["participants"] = [dict(r) for r in db.execute("SELECT user_id,user_name,attendance FROM meeting_participants WHERE meeting_id=? ORDER BY user_name", (meeting["id"],)).fetchall()]
        meeting["decisions"] = [dict(r) for r in db.execute("SELECT * FROM meeting_decisions WHERE meeting_id=? ORDER BY decided_at", (meeting["id"],)).fetchall()]
    return meeting


def list_meetings(limit: int = 50) -> list[dict]:
    with pulse_db.connect() as db:
        rows = db.execute("SELECT m.*, (SELECT COUNT(*) FROM meeting_decisions d WHERE d.meeting_id=m.id) AS decisions "
                          "FROM meeting_sessions m ORDER BY m.created_at DESC LIMIT ?", (max(1, min(int(limit), 200)),)).fetchall()
    return [dict(r) for r in rows]


def start(meeting_id) -> tuple[bool, str]:
    with pulse_db.connect() as db:
        meeting = _row(db, meeting_id)
        if not meeting:
            return False, "not_found"
        if meeting["status"] != "planned":
            return False, "wrong_state"
        db.execute("UPDATE meeting_sessions SET status='running' WHERE id=?", (meeting["id"],))
    return True, "ok"


def set_attendance(meeting_id, user_id, attendance) -> tuple[bool, str]:
    if attendance not in ATTENDANCE:
        return False, "invalid"
    with pulse_db.connect() as db:
        meeting = _row(db, meeting_id)
        if not meeting:
            return False, "not_found"
        if meeting["status"] == "closed":
            return False, "closed"
        changed = db.execute("UPDATE meeting_participants SET attendance=? WHERE meeting_id=? AND user_id=?", (attendance, meeting["id"], str(user_id))).rowcount
    return (True, "ok") if changed else (False, "unknown_participant")


def add_decision(meeting_id, text, *, actor_id="", actor_name="", task=None, perms=None, task_creator=None) -> tuple[bool, str, dict | None]:
    """Record a decision of a *running* meeting. ``task`` = ``{"assignee_id", "assignee_name", "title", "priority", "due_at"}`` creates a task
    through ``task_creator`` (default ``pulse_task_actions.create``) and links it. Returns ``(ok, code, decision)``."""
    body = str(text or "").strip()
    if not body or len(body) > MAX_TEXT:
        return False, "invalid", None
    with pulse_db.connect() as db:
        meeting = _row(db, meeting_id)
        if not meeting:
            return False, "not_found", None
        if meeting["status"] != "running":
            return False, "not_running", None
        if db.execute("SELECT COUNT(*) FROM meeting_decisions WHERE meeting_id=?", (meeting["id"],)).fetchone()[0] >= MAX_DECISIONS:
            return False, "limit", None
    task_id = ""
    if task:
        if task_creator is None:
            import pulse_task_actions
            task_creator = pulse_task_actions.create
        result = task_creator(perms or {}, actor_id, actor_name, task.get("title") or body[:160], description=f"Beschluss aus der Besprechung „{meeting['title']}“: {body}"[:2500],
                              assignee_id=task.get("assignee_id"), assignee_name=task.get("assignee_name", ""), priority=task.get("priority", "normal"),
                              due_at=task.get("due_at"), source="meeting")
        if not result["ok"]:
            return False, result["code"], None
        task_id = result["task"]["id"]
    did = "dec_" + secrets.token_hex(5)
    with pulse_db.connect() as db:
        db.execute("INSERT INTO meeting_decisions (id,meeting_id,text,task_id,actor_id,actor_name,decided_at) VALUES (?,?,?,?,?,?,?)",
                   (did, meeting["id"], body, task_id, str(actor_id)[:30], str(actor_name)[:100], _now()))
        decision = dict(db.execute("SELECT * FROM meeting_decisions WHERE id=?", (did,)).fetchone())
    return True, "ok", decision


def close(meeting_id) -> tuple[bool, str]:
    """Finish a running meeting: the protocol becomes read-only and a summary lands in ``meetings_history``."""
    meeting = get(meeting_id)
    if not meeting:
        return False, "not_found"
    if meeting["status"] != "running":
        return False, "wrong_state"
    with pulse_db.connect() as db:
        db.execute("UPDATE meeting_sessions SET status='closed', closed_at=? WHERE id=? AND status='running'", (_now(), meeting["id"]))
    notes = "\n".join(f"• {d['text']}" + (f" (Aufgabe {d['task_id']})" if d["task_id"] else "") for d in meeting["decisions"]) or "Keine Beschlüsse."
    attendees = {p["user_name"]: p["attendance"] == "present" for p in meeting["participants"]}
    try:
        pulse_db.save_meeting_history(meeting["title"], meeting["scheduled_at"] or _now(), "\n".join(a["title"] for a in meeting["agenda"]), notes, attendees)
    except Exception:  # noqa: BLE001 - the protocol itself is already closed and stored
        pass
    return True, "ok"


def summary_text(meeting: dict) -> str:
    present = [p["user_name"] for p in meeting["participants"] if p["attendance"] == "present"]
    lines = [f"Besprechung „{meeting['title']}“ abgeschlossen.", f"Anwesend: {', '.join(present) or 'niemand erfasst'}."]
    lines += [f"• {d['text']}" + (" (mit Aufgabe)" if d["task_id"] else "") for d in meeting["decisions"][:10]]
    return "\n".join(lines)[:1500]


def export(meeting_id) -> str:
    meeting = get(meeting_id)
    return json.dumps(meeting, ensure_ascii=False, indent=2) if meeting else ""

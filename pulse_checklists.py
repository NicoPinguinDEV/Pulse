"""BOT-11: on- and offboarding checklists for team members.

Created automatically where Pulse already reacts to joining/leaving the team (cogs/pulse_suite.py
on_member_update). Steps Pulse does itself there (tickets handed over, running shift ended, team roles
gone) are ticked at once; the rest is ticked by leadership in the dashboard. One open checklist per person
and kind. Nothing here changes roles or removes anyone (team offboarding ≠ server kick).
"""
from __future__ import annotations

import json
import secrets

import pulse_db

ONBOARDING = (
    ("roles", "Rollen geprüft"),
    ("channels", "Kanäle und Rechte geprüft"),
    ("training", "Pflichtschulung abgeschlossen"),
    ("mentor", "Mentor zugewiesen"),
    ("probation", "Probezeit festgelegt"),
    ("rules", "Teamregeln bestätigt"),
)
OFFBOARDING = (
    ("roles_removed", "Teamrollen entfernt"),
    ("tickets", "Offene Tickets übertragen"),
    ("shift", "Laufende Schicht beendet"),
    ("access", "Zugänge (Dashboard, Panels, Kanäle) geprüft"),
    ("note", "Abschlussnotiz in der Teamakte"),
    ("ex_role", "Ex-Teamler-Rolle geprüft"),
)
KINDS = {"onboarding": ONBOARDING, "offboarding": OFFBOARDING}


def _table(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS team_checklists (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_name TEXT, "
               "kind TEXT NOT NULL, created_at TEXT NOT NULL, done_json TEXT DEFAULT '{}', mentor_id TEXT, mentor_name TEXT, "
               "completed_at TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_team_checklists_user ON team_checklists(user_id, kind, completed_at)")


def _row(row) -> dict:
    item = dict(row)
    item["done"] = json.loads(item.pop("done_json") or "{}")
    item["steps"] = [{"key": k, "label": label, "done": item["done"].get(k)} for k, label in KINDS[item["kind"]]]
    item["progress"] = sum(1 for s in item["steps"] if s["done"])
    return item


def open_for(user_id, kind: str) -> dict | None:
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute("SELECT * FROM team_checklists WHERE user_id=? AND kind=? AND completed_at IS NULL ORDER BY created_at DESC LIMIT 1",
                         (str(user_id), kind)).fetchone()
    return _row(row) if row else None


def start(user_id, user_name: str, kind: str, auto_done: dict | None = None) -> dict:
    """Creates the checklist unless one is already open; auto_done ticks steps Pulse did itself."""
    if kind not in KINDS:
        raise ValueError("unknown checklist")
    existing = open_for(user_id, kind)
    if existing:
        return existing
    now = pulse_db._now()
    done = {k: {"by": "Pulse", "at": now} for k, v in (auto_done or {}).items() if v and k in dict(KINDS[kind])}
    cid = "chk_" + secrets.token_hex(5)
    with pulse_db.connect() as db:
        _table(db)
        db.execute("INSERT INTO team_checklists(id,user_id,user_name,kind,created_at,done_json) VALUES (?,?,?,?,?,?)",
                   (cid, str(user_id), user_name, kind, now, json.dumps(done)))
    return get(cid)


def get(checklist_id: str) -> dict | None:
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute("SELECT * FROM team_checklists WHERE id=?", (checklist_id,)).fetchone()
    return _row(row) if row else None


def toggle(checklist_id: str, step: str, actor_name: str) -> dict:
    item = get(checklist_id)
    if item is None or step not in dict(KINDS[item["kind"]]):
        raise ValueError("Unbekannte Checkliste oder unbekannter Schritt.")
    done = item["done"]
    if step in done:
        done.pop(step)
    else:
        done[step] = {"by": actor_name, "at": pulse_db._now()}
    complete = all(k in done for k, _ in KINDS[item["kind"]])
    with pulse_db.connect() as db:
        db.execute("UPDATE team_checklists SET done_json=?, completed_at=? WHERE id=?",
                   (json.dumps(done), pulse_db._now() if complete else None, checklist_id))
    return get(checklist_id)


def set_mentor(checklist_id: str, mentor_id, mentor_name: str, actor_name: str) -> dict:
    item = get(checklist_id)
    if item is None or item["kind"] != "onboarding":
        raise ValueError("Einen Mentor gibt es nur beim Onboarding.")
    with pulse_db.connect() as db:
        db.execute("UPDATE team_checklists SET mentor_id=?, mentor_name=? WHERE id=?", (str(mentor_id), mentor_name, checklist_id))
    if "mentor" not in item["done"]:
        toggle(checklist_id, "mentor", actor_name)
    pulse_db.notify(mentor_id, "🤝 Du bist Mentor", f"Du begleitest {item['user_name']} beim Einstieg ins Team.", "info",
                    "/team/checklists", f"mentor:{item['user_id']}", 86400)
    return get(checklist_id)


def list_open(limit: int = 200) -> list[dict]:
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute("SELECT * FROM team_checklists WHERE completed_at IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def list_done(limit: int = 30) -> list[dict]:
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute("SELECT * FROM team_checklists WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]

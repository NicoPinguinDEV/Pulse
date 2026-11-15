"""Audit #53: einheitliche Lesebestätigung für Ankündigungen, Team-News und Dienstanweisungen.

Eine Führungskraft kennzeichnet einen Beitrag als „Bestätigung erforderlich“. Jede Person im Team bestätigt ihn selbst
(Dashboard ``/bestaetigungen`` oder Discord ``/bestaetigungen``); Führungskräfte sehen, wer noch fehlt. Eine Bestätigung ist
nur eine Lesequittung - sie entscheidet nichts und kann nicht für andere abgegeben werden.

Speicher: ``ack_requirements`` (Beitrag, Titel, Link, Frist) und ``ack_receipts`` (Beitrag, Person, Zeit), beide im Backup.
Beitragsarten: ``announcement`` (Discord-Ankündigung), ``team_news`` (Team-News), ``instruction`` (freie Dienstanweisung).
Der Wiki-Pflichtlektüre-Mechanismus (#113) bleibt eigenständig, weil er an Versionen gebunden ist.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pulse_db

KINDS = {"announcement": "Ankündigung", "team_news": "Team-News", "instruction": "Dienstanweisung"}
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_TITLE = 160
MAX_DAYS = 365
LIST_URL = "/bestaetigungen"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def require(kind: str, subject_id, title: str, *, url: str = LIST_URL, actor_id="", actor_name="", due_days: int | None = None,
            now: datetime | None = None) -> bool:
    """Mark a post as "confirmation required" (idempotent). ``False`` for invalid input or if it was already required."""
    sid = str(subject_id or "").strip()
    head = str(title or "").strip()[:MAX_TITLE]
    if kind not in KINDS or not _ID.match(sid) or not head:
        return False
    if not str(url or "").startswith("/") or str(url).startswith("//"):
        url = LIST_URL
    now = now or _now()
    due = ""
    if due_days is not None:
        days = max(1, min(int(due_days), MAX_DAYS))
        due = _iso(now + timedelta(days=days))
    with pulse_db.connect() as db:
        if db.execute("SELECT 1 FROM ack_requirements WHERE kind=? AND subject_id=?", (kind, sid)).fetchone():
            return False
        db.execute("INSERT INTO ack_requirements (kind,subject_id,title,url,required_by_id,required_by_name,created_at,due_at) VALUES (?,?,?,?,?,?,?,?)",
                   (kind, sid, head, str(url)[:300], str(actor_id)[:30], str(actor_name)[:100], _iso(now), due))
    return True


def is_required(kind: str, subject_id) -> bool:
    with pulse_db.connect() as db:
        return db.execute("SELECT 1 FROM ack_requirements WHERE kind=? AND subject_id=?", (kind, str(subject_id))).fetchone() is not None


def acknowledge(kind: str, subject_id, user_id, user_name="", now: datetime | None = None) -> bool:
    """Record the person's own confirmation (idempotent). ``False`` if nothing is required for this post."""
    uid = str(user_id or "").strip()
    if not uid or not is_required(kind, subject_id):
        return False
    with pulse_db.connect() as db:
        db.execute("INSERT OR IGNORE INTO ack_receipts (kind,subject_id,user_id,user_name,at) VALUES (?,?,?,?,?)",
                   (kind, str(subject_id), uid[:30], str(user_name)[:100], _iso(now or _now())))
    return True


def pending_for(user_id, now: datetime | None = None) -> list[dict]:
    """Required posts this person has not confirmed yet, overdue first, then oldest first."""
    now = now or _now()
    with pulse_db.connect() as db:
        rows = db.execute("SELECT r.* FROM ack_requirements r WHERE NOT EXISTS (SELECT 1 FROM ack_receipts a WHERE a.kind=r.kind "
                          "AND a.subject_id=r.subject_id AND a.user_id=?) ORDER BY r.created_at ASC LIMIT 200", (str(user_id),)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["kind_label"] = KINDS.get(item["kind"], item["kind"])
        item["overdue"] = bool(item["due_at"]) and item["due_at"] < _iso(now)
        out.append(item)
    return sorted(out, key=lambda i: (not i["overdue"], i["created_at"]))


def coverage(kind: str, subject_id, team) -> dict:
    """``team``: ``[(user_id, name)]``. Who confirmed (with time) and who is still missing."""
    with pulse_db.connect() as db:
        receipts = {r["user_id"]: r["at"] for r in db.execute("SELECT user_id,at FROM ack_receipts WHERE kind=? AND subject_id=?", (kind, str(subject_id))).fetchall()}
    confirmed = [(uid, name, receipts[str(uid)]) for uid, name in team if str(uid) in receipts]
    missing = [(uid, name) for uid, name in team if str(uid) not in receipts]
    return {"confirmed": confirmed, "missing": missing, "total": len(confirmed) + len(missing)}


def overview(team) -> list[dict]:
    """All requirements with their coverage (managers), newest first."""
    with pulse_db.connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM ack_requirements ORDER BY created_at DESC LIMIT 100").fetchall()]
    for row in rows:
        row["kind_label"] = KINDS.get(row["kind"], row["kind"])
        row["coverage"] = coverage(row["kind"], row["subject_id"], team)
    return rows

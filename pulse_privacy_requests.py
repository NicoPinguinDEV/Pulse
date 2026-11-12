"""Audit #171: central data-subject request workflow (access, rectification, erasure, restriction, objection).

One record per request in pulse.db (``privacy_requests``) plus an append-only history (``privacy_request_events``).
Both tables are part of the JSON backup (pulse_db.TABLES) and of the personal export (``user_id`` / ``actor_id``).

Rules:
- A request belongs to exactly one guild; every read and write is scoped by guild_id.
- Statutory answer period: one month (stored as 30 days) from receipt, once extendable by up to 60 days with a reason
  (Art. 12(3) GDPR). Overdue is computed, never stored, so a wrong clock cannot hide a deadline.
- Pulse never deletes or changes personal data on its own. Completing an erasure or rectification request records what
  a person did by hand; the data overview in the dashboard is read-only.
- Only one open request per person and kind; at most MAX_OPEN_PER_PERSON open requests per person (spam guard for the
  Discord self-service command).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pulse_db

KINDS = {
    "access": "Auskunft (Art. 15)",
    "rectification": "Berichtigung (Art. 16)",
    "erasure": "Löschung (Art. 17)",
    "restriction": "Einschränkung (Art. 18)",
    "objection": "Widerspruch (Art. 21)",
}
STATUSES = {
    "received": "eingegangen",
    "in_progress": "in Bearbeitung",
    "waiting": "Rückfrage offen",
    "completed": "erledigt",
    "rejected": "abgelehnt",
}
OPEN_STATUSES = {"received", "in_progress", "waiting"}
CLOSED_STATUSES = {"completed", "rejected"}
CHANNELS = {"discord": "Discord-Befehl", "dashboard": "Dashboard", "ticket": "Support-Ticket", "other": "anderer Weg"}
ANSWER_DAYS = 30
MAX_EXTENSION_DAYS = 60
MAX_OPEN_PER_PERSON = 3
MAX_DETAILS = 1000
MAX_NOTE = 1000
MIN_RESOLUTION = 10
LIST_LIMIT = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if limit <= 200 else str(value or "").strip()[:limit]


def _snowflake(value) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or not 15 <= len(text) <= 22:
        raise ValueError("Bitte eine gültige Discord-ID (15–22 Ziffern) angeben.")
    return text


def _event(db, request_id: str, guild_id: str, actor_id, actor_name, action: str, note: str = "", now: datetime | None = None):
    db.execute(
        "INSERT INTO privacy_request_events(id,request_id,guild_id,actor_id,actor_name,action,note,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (pulse_db.make_id("dsrev"), request_id, guild_id, str(actor_id or ""), _clean(actor_name, 120), action,
         str(note or "")[:MAX_NOTE], _iso(now or _now())),
    )


def decorate(row: dict, now: datetime | None = None) -> dict:
    """Adds labels plus the computed deadline state (days_left, overdue) to a stored row."""
    item = dict(row)
    now = now or _now()
    due = _parse(item.get("due_at"))
    item["kind_label"] = KINDS.get(item.get("kind"), item.get("kind"))
    item["status_label"] = STATUSES.get(item.get("status"), item.get("status"))
    item["channel_label"] = CHANNELS.get(item.get("channel"), item.get("channel"))
    item["is_open"] = item.get("status") in OPEN_STATUSES
    item["days_left"] = None if due is None else (due.date() - now.date()).days
    item["overdue"] = bool(item["is_open"] and due is not None and due < now)
    return item


def create_request(guild_id, user_id, user_name, kind, details="", *, channel="dashboard", created_by_id=None,
                   created_by_name="", received_at: datetime | None = None, now: datetime | None = None) -> dict:
    guild = str(guild_id or "").strip()
    if not guild:
        raise ValueError("Kein Discord-Server zugeordnet.")
    uid = _snowflake(user_id)
    if kind not in KINDS:
        raise ValueError("Unbekannte Art der Anfrage.")
    if channel not in CHANNELS:
        raise ValueError("Unbekannter Eingangsweg.")
    details = str(details or "").strip()
    if len(details) > MAX_DETAILS:
        raise ValueError(f"Die Beschreibung darf höchstens {MAX_DETAILS} Zeichen enthalten.")
    if kind in {"rectification", "objection"} and len(details) < 10:
        raise ValueError("Bitte kurz beschreiben, welche Angaben betroffen sind (mindestens 10 Zeichen).")
    now = now or _now()
    received = received_at or now
    if received > now + timedelta(minutes=5):
        raise ValueError("Das Eingangsdatum darf nicht in der Zukunft liegen.")
    if received < now - timedelta(days=365):
        raise ValueError("Das Eingangsdatum liegt mehr als ein Jahr zurück.")
    row = {
        "id": "DSR-" + secrets.token_hex(3).upper(), "guild_id": guild, "user_id": uid, "user_name": _clean(user_name, 120),
        "kind": kind, "details": details, "channel": channel, "status": "received", "received_at": _iso(received),
        "due_at": _iso(received + timedelta(days=ANSWER_DAYS)), "extended_reason": "", "assignee_id": None,
        "assignee_name": "", "resolution": "", "closed_at": None, "closed_by_id": None,
        "created_by_id": str(created_by_id or uid), "created_by_name": _clean(created_by_name or user_name, 120),
        "updated_at": _iso(now),
    }
    with pulse_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        open_rows = db.execute(
            f"SELECT id, kind FROM privacy_requests WHERE guild_id=? AND user_id=? AND status IN ({','.join('?' * len(OPEN_STATUSES))})",
            (guild, uid, *sorted(OPEN_STATUSES))).fetchall()
        same = next((r["id"] for r in open_rows if r["kind"] == kind), None)
        if same:
            raise ValueError(f"Zu dieser Person ist bereits eine offene Anfrage dieser Art vorhanden ({same}).")
        if len(open_rows) >= MAX_OPEN_PER_PERSON:
            raise ValueError(f"Es sind bereits {MAX_OPEN_PER_PERSON} Anfragen dieser Person offen.")
        db.execute(
            "INSERT INTO privacy_requests(id,guild_id,user_id,user_name,kind,details,channel,status,received_at,due_at,extended_reason,"
            "assignee_id,assignee_name,resolution,closed_at,closed_by_id,created_by_id,created_by_name,updated_at) VALUES "
            "(:id,:guild_id,:user_id,:user_name,:kind,:details,:channel,:status,:received_at,:due_at,:extended_reason,"
            ":assignee_id,:assignee_name,:resolution,:closed_at,:closed_by_id,:created_by_id,:created_by_name,:updated_at)", row)
        _event(db, row["id"], guild, row["created_by_id"], row["created_by_name"], "created",
               f"{KINDS[kind]} über {CHANNELS[channel]}", now)
    pulse_db.record_event("Datenschutzanfrage eingegangen", "privacy_request", row["id"], row["created_by_id"],
                          row["created_by_name"], {"guild_id": guild, "kind": kind, "channel": channel})
    return decorate(row, now)


def get_request(guild_id, request_id) -> dict | None:
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM privacy_requests WHERE guild_id=? AND id=?",
                         (str(guild_id), str(request_id or "").strip().upper())).fetchone()
    return decorate(dict(row)) if row else None


def history(guild_id, request_id) -> list[dict]:
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM privacy_request_events WHERE guild_id=? AND request_id=? ORDER BY created_at, rowid",
                          (str(guild_id), str(request_id).upper())).fetchall()
    return [dict(r) for r in rows]


def list_requests(guild_id, *, state: str = "open", user_id=None, now: datetime | None = None) -> list[dict]:
    """state: open | closed | overdue | all. Open requests are ordered by deadline, closed ones newest first."""
    sql, args = "SELECT * FROM privacy_requests WHERE guild_id=?", [str(guild_id)]
    if user_id is not None:
        sql += " AND user_id=?"
        args.append(str(user_id))
    if state in {"open", "overdue"}:
        sql += f" AND status IN ({','.join('?' * len(OPEN_STATUSES))}) ORDER BY due_at"
        args += sorted(OPEN_STATUSES)
    elif state == "closed":
        sql += f" AND status IN ({','.join('?' * len(CLOSED_STATUSES))}) ORDER BY closed_at DESC"
        args += sorted(CLOSED_STATUSES)
    else:
        sql += " ORDER BY received_at DESC"
    sql += " LIMIT ?"
    args.append(LIST_LIMIT)
    with pulse_db.connect() as db:
        rows = [decorate(dict(r), now) for r in db.execute(sql, args).fetchall()]
    return [r for r in rows if r["overdue"]] if state == "overdue" else rows


def summary(guild_id, now: datetime | None = None) -> dict:
    rows = list_requests(guild_id, state="open", now=now)
    return {"open": len(rows), "overdue": sum(1 for r in rows if r["overdue"]),
            "due_soon": sum(1 for r in rows if not r["overdue"] and r["days_left"] is not None and r["days_left"] <= 7)}


def _load_for_update(db, guild_id, request_id):
    row = db.execute("SELECT * FROM privacy_requests WHERE guild_id=? AND id=?",
                     (str(guild_id), str(request_id or "").strip().upper())).fetchone()
    if row is None:
        raise LookupError("Anfrage nicht gefunden.")
    return dict(row)


def set_status(guild_id, request_id, status, actor_id, actor_name, note="", *, now: datetime | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError("Unbekannter Status.")
    note = str(note or "").strip()
    if len(note) > MAX_NOTE:
        raise ValueError(f"Die Notiz darf höchstens {MAX_NOTE} Zeichen enthalten.")
    if status in CLOSED_STATUSES and len(note) < MIN_RESOLUTION:
        raise ValueError("Zum Abschließen bitte das Ergebnis dokumentieren (mindestens 10 Zeichen), "
                         "z. B. was geliefert, berichtigt oder gelöscht wurde bzw. warum abgelehnt wird.")
    now = now or _now()
    with pulse_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _load_for_update(db, guild_id, request_id)
        if row["status"] == status:
            raise ValueError("Die Anfrage hat diesen Status bereits.")
        if row["status"] in CLOSED_STATUSES and status in CLOSED_STATUSES:
            raise ValueError("Eine abgeschlossene Anfrage bitte zuerst wieder aufnehmen.")
        if status in CLOSED_STATUSES:
            db.execute("UPDATE privacy_requests SET status=?, resolution=?, closed_at=?, closed_by_id=?, updated_at=? WHERE id=?",
                       (status, note, _iso(now), str(actor_id), _iso(now), row["id"]))
        else:
            db.execute("UPDATE privacy_requests SET status=?, closed_at=NULL, closed_by_id=NULL, updated_at=? WHERE id=?",
                       (status, _iso(now), row["id"]))
        action = "reopened" if row["status"] in CLOSED_STATUSES else "status"
        _event(db, row["id"], row["guild_id"], actor_id, actor_name, action,
               f"{STATUSES[row['status']]} → {STATUSES[status]}" + (f": {note}" if note else ""), now)
    pulse_db.record_event("Datenschutzanfrage geändert", "privacy_request", row["id"], actor_id, actor_name,
                          {"guild_id": row["guild_id"], "from": row["status"], "to": status})
    return get_request(guild_id, row["id"])


def assign(guild_id, request_id, assignee_id, assignee_name, actor_id, actor_name, *, now: datetime | None = None) -> dict:
    assignee = str(assignee_id).strip() if assignee_id else None
    if assignee is not None and not (assignee.isdigit() and len(assignee) <= 22):
        raise ValueError("Ungültige Discord-ID für die Zuständigkeit.")
    now = now or _now()
    with pulse_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _load_for_update(db, guild_id, request_id)
        if row["status"] in CLOSED_STATUSES:
            raise ValueError("Abgeschlossene Anfragen können nicht mehr zugewiesen werden.")
        db.execute("UPDATE privacy_requests SET assignee_id=?, assignee_name=?, updated_at=? WHERE id=?",
                   (assignee, _clean(assignee_name, 120) if assignee else "", _iso(now), row["id"]))
        _event(db, row["id"], row["guild_id"], actor_id, actor_name, "assigned",
               f"zuständig: {_clean(assignee_name, 120)}" if assignee else "Zuständigkeit entfernt", now)
    return get_request(guild_id, row["id"])


def extend(guild_id, request_id, days, reason, actor_id, actor_name, *, now: datetime | None = None) -> dict:
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError("Bitte eine Anzahl Tage angeben.") from None
    if not 1 <= days <= MAX_EXTENSION_DAYS:
        raise ValueError(f"Die Frist kann um 1 bis {MAX_EXTENSION_DAYS} Tage verlängert werden.")
    reason = str(reason or "").strip()
    if len(reason) < MIN_RESOLUTION:
        raise ValueError("Eine Verlängerung braucht eine Begründung (mindestens 10 Zeichen); die Person ist darüber zu informieren.")
    now = now or _now()
    with pulse_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _load_for_update(db, guild_id, request_id)
        if row["status"] in CLOSED_STATUSES:
            raise ValueError("Abgeschlossene Anfragen haben keine Frist mehr.")
        if row.get("extended_reason"):
            raise ValueError("Die Frist wurde bereits einmal verlängert.")
        due = (_parse(row["due_at"]) or now) + timedelta(days=days)
        db.execute("UPDATE privacy_requests SET due_at=?, extended_reason=?, updated_at=? WHERE id=?",
                   (_iso(due), reason[:MAX_NOTE], _iso(now), row["id"]))
        _event(db, row["id"], row["guild_id"], actor_id, actor_name, "extended", f"+{days} Tage: {reason}", now)
    return get_request(guild_id, row["id"])


def add_note(guild_id, request_id, note, actor_id, actor_name, *, now: datetime | None = None) -> dict:
    note = str(note or "").strip()
    if not 3 <= len(note) <= MAX_NOTE:
        raise ValueError(f"Die Notiz muss 3 bis {MAX_NOTE} Zeichen lang sein.")
    with pulse_db.connect() as db:
        row = _load_for_update(db, guild_id, request_id)
        _event(db, row["id"], row["guild_id"], actor_id, actor_name, "note", note, now)
    return get_request(guild_id, row["id"])


def data_overview(export: dict) -> list[tuple[str, int]]:
    """Read-only inventory (area, number of records) from pulse_retention.export_person – basis for erasure checks."""
    out: list[tuple[str, int]] = []

    def count(value) -> int:
        if value is None:
            return 0
        if isinstance(value, (list, dict)):
            return len(value)
        return 1

    for key, label in (("team_file", "Teamakte"), ("applications", "Bewerbungen"), ("audit_as_actor", "Audit (eigene Aktionen)"),
                       ("ai_moderation_cases", "KI-Moderationsfälle"), ("voice_support_open_cases", "offene Voice-Supportfälle"),
                       ("dashboard_login_ip_audit", "Dashboard-Anmeldeprotokoll")):
        n = count(export.get(key))
        if n:
            out.append((label, n))
    shifts = export.get("shifts") or {}
    n = len(shifts.get("history") or []) + (1 if shifts.get("active") else 0)
    if n:
        out.append(("Schichten", n))
    for db_name, tables in (export.get("databases") or {}).items():
        if not isinstance(tables, dict):
            continue
        for table, rows in tables.items():
            n = count(rows)
            if n:
                out.append((f"{db_name} · {table}", n))
    return out


def admin_members(guild) -> list[tuple[str, str]]:
    """(id, name) of people who may process requests: server owner and Discord administrators (bounded)."""
    if guild is None:
        return []
    out = []
    for member in getattr(guild, "members", []) or []:
        if getattr(member, "bot", False):
            continue
        perms = getattr(member, "guild_permissions", None)
        if member.id == getattr(guild, "owner_id", None) or getattr(perms, "administrator", False):
            out.append((str(member.id), getattr(member, "display_name", str(member.id))))
        if len(out) >= 50:
            break
    return sorted(out, key=lambda item: item[1].lower())


def overdue_reminders(guild_id, fallback_ids=(), now: datetime | None = None) -> int:
    """Daily inbox reminder for every overdue open request: to the assignee, or – when nobody is assigned – to the
    given fallback recipients (server owner/administrators). Deduplicated per request and recipient for 24 h."""
    sent = 0
    for item in list_requests(guild_id, state="overdue", now=now):
        recipients = [item["assignee_id"]] if item.get("assignee_id") else list(fallback_ids)
        for recipient in recipients:
            pulse_db.notify(recipient, "🔒 Datenschutzanfrage überfällig",
                            f"{item['id']} · {item['kind_label']} – Frist abgelaufen.", "warning",
                            f"/admin/privacy/requests/{item['id']}", f"dsr-overdue:{item['id']}", 86400)
            sent += 1
    return sent

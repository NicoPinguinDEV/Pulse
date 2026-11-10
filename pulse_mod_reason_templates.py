"""Versioned, guild-scoped reason templates for citizen moderation.

Each edit creates an immutable new version. A moderation case stores both the selected
version and a copy of the rendered reason, so future edits never rewrite historical cases.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

import pulse_db

ACTIONS = ("warn", "timeout", "kick", "ban")
MAX_TITLE_LENGTH = 80
MAX_BODY_LENGTH = 900
TEMPLATE_TOKEN_PREFIX = "@modtpl:"
_TEMPLATE_ID_RE = re.compile(r"^MRT-[A-F0-9]{8}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_tables(db) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_reason_templates (
            id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            action TEXT NOT NULL,
            current_version INTEGER NOT NULL,
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived_at TEXT,
            archived_by_id TEXT,
            archived_by_name TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_reason_template_versions (
            template_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (template_id, version),
            FOREIGN KEY (template_id) REFERENCES mod_reason_templates(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_mod_reason_templates_guild_action "
        "ON mod_reason_templates(guild_id, action, archived_at)"
    )


def _clean_action(action: str) -> str:
    value = str(action or "").strip().lower()
    if value not in ACTIONS:
        raise ValueError("Unbekannte Moderationsaktion.")
    return value


def _clean_content(title: str, body: str) -> tuple[str, str]:
    title = str(title or "").strip()
    body = str(body or "").strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Vorlagentitel muss 1 bis {MAX_TITLE_LENGTH} Zeichen enthalten.")
    if len(body) < 5 or len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"Der Vorlagentext muss 5 bis {MAX_BODY_LENGTH} Zeichen enthalten.")
    if "{" in body or "}" in body:
        raise ValueError("Vorlagentexte dürfen keine nicht unterstützten Platzhalter enthalten.")
    return title, body


def create_template(guild_id, action: str, title: str, body: str, actor_id, actor_name: str) -> dict:
    guild = str(guild_id or "").strip()
    actor = str(actor_id or "").strip()
    action = _clean_action(action)
    title, body = _clean_content(title, body)
    if not guild or not actor:
        raise ValueError("Server und ausführendes Teammitglied müssen angegeben werden.")
    template_id = "MRT-" + secrets.token_hex(4).upper()
    created = _now()
    name = str(actor_name or "Teammitglied").strip()[:100] or "Teammitglied"
    with pulse_db.connect() as db:
        _ensure_tables(db)
        db.execute(
            "INSERT INTO mod_reason_templates "
            "(id,guild_id,action,current_version,created_by_id,created_by_name,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (template_id, guild, action, 1, actor, name, created),
        )
        db.execute(
            "INSERT INTO mod_reason_template_versions "
            "(template_id,version,title,body,created_by_id,created_by_name,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (template_id, 1, title, body, actor, name, created),
        )
    return get_template(guild, template_id)


def update_template(guild_id, template_id: str, title: str, body: str, actor_id, actor_name: str) -> dict:
    guild = str(guild_id or "").strip()
    template_id = str(template_id or "").strip().upper()
    actor = str(actor_id or "").strip()
    title, body = _clean_content(title, body)
    if not _TEMPLATE_ID_RE.fullmatch(template_id):
        raise ValueError("Ungültige Vorlagen-ID.")
    if not guild or not actor:
        raise ValueError("Server und ausführendes Teammitglied müssen angegeben werden.")
    name = str(actor_name or "Teammitglied").strip()[:100] or "Teammitglied"
    created = _now()
    with pulse_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT current_version,archived_at FROM mod_reason_templates WHERE id=? AND guild_id=?",
            (template_id, guild),
        ).fetchone()
        if row is None:
            raise ValueError("Diese Vorlage wurde nicht gefunden.")
        if row["archived_at"]:
            raise ValueError("Archivierte Vorlagen können nicht bearbeitet werden.")
        version = int(row["current_version"]) + 1
        db.execute(
            "INSERT INTO mod_reason_template_versions "
            "(template_id,version,title,body,created_by_id,created_by_name,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (template_id, version, title, body, actor, name, created),
        )
        db.execute(
            "UPDATE mod_reason_templates SET current_version=? WHERE id=? AND guild_id=?",
            (version, template_id, guild),
        )
    return get_template(guild, template_id)


def archive_template(guild_id, template_id: str, actor_id, actor_name: str) -> bool:
    guild = str(guild_id or "").strip()
    template_id = str(template_id or "").strip().upper()
    if not _TEMPLATE_ID_RE.fullmatch(template_id):
        raise ValueError("Ungültige Vorlagen-ID.")
    with pulse_db.connect() as db:
        _ensure_tables(db)
        cursor = db.execute(
            """
            UPDATE mod_reason_templates
            SET archived_at=?, archived_by_id=?, archived_by_name=?
            WHERE id=? AND guild_id=? AND archived_at IS NULL
            """,
            (_now(), str(actor_id or ""), str(actor_name or "")[:100], template_id, guild),
        )
        return cursor.rowcount == 1


def get_template(guild_id, template_id: str, version: int | None = None) -> dict | None:
    guild = str(guild_id or "").strip()
    template_id = str(template_id or "").strip().upper()
    with pulse_db.connect() as db:
        _ensure_tables(db)
        row = db.execute(
            "SELECT * FROM mod_reason_templates WHERE id=? AND guild_id=?",
            (template_id, guild),
        ).fetchone()
        if row is None:
            return None
        selected_version = int(version) if version is not None else int(row["current_version"])
        revision = db.execute(
            "SELECT * FROM mod_reason_template_versions WHERE template_id=? AND version=?",
            (template_id, selected_version),
        ).fetchone()
    if revision is None:
        return None
    result = dict(row)
    result.update({f"version_{key}": value for key, value in dict(revision).items() if key != "template_id"})
    result["version"] = selected_version
    result["title"] = revision["title"]
    result["body"] = revision["body"]
    return result


def list_templates(guild_id, action: str | None = None, *, include_archived: bool = False, limit: int = 100) -> list[dict]:
    guild = str(guild_id or "").strip()
    if not guild:
        return []
    action = _clean_action(action) if action else None
    try:
        page_size = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        page_size = 100
    query = (
        "SELECT t.id,t.guild_id,t.action,t.current_version,t.created_by_id,t.created_by_name,"
        "t.created_at,t.archived_at,v.title,v.body,v.created_by_id AS version_created_by_id,"
        "v.created_by_name AS version_created_by_name,v.created_at AS version_created_at "
        "FROM mod_reason_templates t JOIN mod_reason_template_versions v "
        "ON v.template_id=t.id AND v.version=t.current_version WHERE t.guild_id=?"
    )
    params: list[object] = [guild]
    if not include_archived:
        query += " AND t.archived_at IS NULL"
    if action:
        query += " AND t.action=?"
        params.append(action)
    query += " ORDER BY t.action,t.id LIMIT ?"
    params.append(page_size)
    with pulse_db.connect() as db:
        _ensure_tables(db)
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_versions(guild_id, template_id: str, limit: int = 50) -> list[dict]:
    guild = str(guild_id or "").strip()
    template_id = str(template_id or "").strip().upper()
    try:
        page_size = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        page_size = 50
    with pulse_db.connect() as db:
        _ensure_tables(db)
        exists = db.execute(
            "SELECT 1 FROM mod_reason_templates WHERE id=? AND guild_id=?",
            (template_id, guild),
        ).fetchone()
        if not exists:
            return []
        rows = db.execute(
            "SELECT template_id,version,title,body,created_by_id,created_by_name,created_at "
            "FROM mod_reason_template_versions WHERE template_id=? ORDER BY version DESC LIMIT ?",
            (template_id, page_size),
        ).fetchall()
    return [dict(row) for row in rows]


def selection_token(template_id: str, version: int) -> str:
    return f"{TEMPLATE_TOKEN_PREFIX}{str(template_id).upper()}:{int(version)}"


def resolve_reason(guild_id, action: str, value: str) -> tuple[str, str | None, int | None]:
    """Resolve an autocomplete token to an immutable version; plain text stays plain text."""
    text = str(value or "").strip()
    if not text.startswith(TEMPLATE_TOKEN_PREFIX):
        return text, None, None
    match = re.fullmatch(r"@modtpl:(MRT-[A-F0-9]{8}):(\d{1,6})", text)
    if not match:
        raise ValueError("Ungültige Moderationsvorlagen-Auswahl. Bitte erneut aus der Liste auswählen.")
    template = get_template(guild_id, match.group(1), int(match.group(2)))
    if template is None or template["action"] != _clean_action(action) or template.get("archived_at"):
        raise ValueError("Diese Vorlagenversion ist nicht mehr verfügbar. Bitte eine aktuelle Vorlage auswählen.")
    return template["body"], template["id"], int(template["version"])

"""BOT-09 (E-8): case file for citizen moderation – warn, timeout, kick, ban with case ID, proof and expiry.

Rights are deny-by-default and never wider than before: a Pulse right (can_moderate_kicks / _bans, or admin)
AND the matching Discord permission are both required. Team members are not handled here (team warnings and
team kicks have their own, stricter flows). Every case is stored in pulse.db (new table mod_cases).
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pulse_db

ACTIONS = {"warn": "Verwarnung", "timeout": "Timeout", "kick": "Kick", "ban": "Ban"}
PULSE_RIGHT = {"warn": "can_moderate_kicks", "timeout": "can_moderate_kicks", "kick": "can_moderate_kicks", "ban": "can_moderate_bans"}
DISCORD_RIGHT = {"warn": "moderate_members", "timeout": "moderate_members", "kick": "kick_members", "ban": "ban_members"}
MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord limit
MAX_TEMPLATE_NAME_LENGTH = 40
MAX_TEMPLATE_BODY_LENGTH = 600
MAX_REASON_LENGTH = 1000
DM_TEMPLATE = ("⚖️ **{aktion}** auf {server}\nGrund: {grund}{bis}\nFall-ID: `{fall}`\n"
               "Einspruch: {einspruch}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS mod_cases (id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, target_id TEXT NOT NULL, "
        "target_name TEXT, action TEXT NOT NULL, reason TEXT NOT NULL, proof TEXT DEFAULT '', expires_at TEXT, "
        "actor_id TEXT NOT NULL, actor_name TEXT, created_at TEXT NOT NULL, status TEXT DEFAULT 'active', "
        "dm_sent INTEGER DEFAULT 0, closed_at TEXT, closed_by TEXT, close_note TEXT DEFAULT '', "
        "reason_template_id TEXT, reason_template_version INTEGER)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mod_cases_target ON mod_cases(target_id, created_at)")


def may_manage_templates(pulse_perms: dict) -> bool:
    """Only administrators may create, revise or archive shared moderation templates."""
    return bool(isinstance(pulse_perms, dict) and pulse_perms.get("is_admin"))


def _validate_template(action: str, name: str, body: str) -> tuple[str, str]:
    if action not in ACTIONS:
        raise ValueError("Unbekannte Moderationsaktion.")
    name = str(name or "").strip()
    if not 2 <= len(name) <= MAX_TEMPLATE_NAME_LENGTH or not name.isprintable():
        raise ValueError(f"Der Vorlagenname muss 2 bis {MAX_TEMPLATE_NAME_LENGTH} druckbare Zeichen enthalten.")
    body = str(body or "").strip()
    if not 5 <= len(body) <= MAX_TEMPLATE_BODY_LENGTH:
        raise ValueError(f"Der Vorlagentext muss 5 bis {MAX_TEMPLATE_BODY_LENGTH} Zeichen enthalten.")
    if body.count("{details}") > 1:
        raise ValueError("Der Platzhalter {details} darf höchstens einmal vorkommen.")
    without_details = body.replace("{details}", "")
    if "{" in without_details or "}" in without_details:
        raise ValueError("Nur der optionale Platzhalter {details} ist erlaubt.")
    return name, body


def create_reason_template(guild_id, action: str, name: str, body: str, actor) -> dict:
    """Create immutable version 1 of a guild-local moderation reason template."""
    name, body = _validate_template(action, name, body)
    template_id = "MRT-" + secrets.token_hex(5).upper()
    created_at = _now().isoformat(timespec="seconds")
    actor_id = str(getattr(actor, "id", actor))
    try:
        with pulse_db.connect() as db:
            _table(db)
            db.execute(
                "INSERT INTO mod_reason_templates(id,guild_id,action,name,active_version,archived,created_by,created_at,updated_by,updated_at) "
                "VALUES (?,?,?,?,1,0,?,?,?,?)",
                (template_id, str(guild_id), action, name, actor_id, created_at, actor_id, created_at),
            )
            db.execute(
                "INSERT INTO mod_reason_template_versions(template_id,version,body,created_by,created_at) VALUES (?,?,?,?,?)",
                (template_id, 1, body, actor_id, created_at),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Eine Vorlage mit diesem Namen existiert für diese Aktion bereits.") from exc
    return get_reason_template(guild_id, action, name)


def list_reason_templates(guild_id, actions=None, include_archived: bool = False) -> list[dict]:
    """List only the active version of each template belonging to this guild."""
    clauses = ["t.guild_id=?"]
    params = [str(guild_id)]
    if actions is not None:
        actions = [str(action) for action in actions if str(action) in ACTIONS]
        if not actions:
            return []
        clauses.append("t.action IN (" + ",".join("?" for _ in actions) + ")")
        params.extend(actions)
    if not include_archived:
        clauses.append("t.archived=0")
    query = (
        "SELECT t.*, v.body FROM mod_reason_templates t "
        "JOIN mod_reason_template_versions v ON v.template_id=t.id AND v.version=t.active_version "
        "WHERE " + " AND ".join(clauses) + " ORDER BY t.action, t.name COLLATE NOCASE"
    )
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def get_reason_template(guild_id, action: str, name: str) -> dict | None:
    """Resolve an active guild- and action-scoped template by case-insensitive name."""
    if action not in ACTIONS:
        return None
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute(
            "SELECT t.*, v.body FROM mod_reason_templates t "
            "JOIN mod_reason_template_versions v ON v.template_id=t.id AND v.version=t.active_version "
            "WHERE t.guild_id=? AND t.action=? AND lower(t.name)=lower(?) AND t.archived=0",
            (str(guild_id), action, str(name or "").strip()),
        ).fetchone()
    return dict(row) if row else None


def list_reason_template_versions(guild_id, action: str, name: str, limit: int = 10) -> list[dict]:
    """Return immutable revision history, newest first, without exposing other guilds."""
    if action not in ACTIONS:
        return []
    limit = max(1, min(int(limit), 50))
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute(
            "SELECT v.version, v.body, v.created_by, v.created_at FROM mod_reason_templates t "
            "JOIN mod_reason_template_versions v ON v.template_id=t.id "
            "WHERE t.guild_id=? AND t.action=? AND lower(t.name)=lower(?) "
            "ORDER BY v.version DESC LIMIT ?",
            (str(guild_id), action, str(name or "").strip(), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def revise_reason_template(guild_id, action: str, name: str, body: str, actor) -> dict:
    """Append a new immutable revision and atomically point the template at it."""
    name, body = _validate_template(action, name, body)
    actor_id = str(getattr(actor, "id", actor))
    updated_at = _now().isoformat(timespec="seconds")
    try:
        with pulse_db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id, active_version, archived FROM mod_reason_templates "
                "WHERE guild_id=? AND action=? AND lower(name)=lower(?)",
                (str(guild_id), action, name),
            ).fetchone()
            if row is None:
                raise ValueError("Diese Moderationsvorlage wurde nicht gefunden.")
            if row["archived"]:
                raise ValueError("Archivierte Vorlagen können nicht mehr bearbeitet werden.")
            next_version = int(row["active_version"]) + 1
            db.execute(
                "INSERT INTO mod_reason_template_versions(template_id,version,body,created_by,created_at) VALUES (?,?,?,?,?)",
                (row["id"], next_version, body, actor_id, updated_at),
            )
            db.execute(
                "UPDATE mod_reason_templates SET active_version=?,updated_by=?,updated_at=? WHERE id=? AND active_version=?",
                (next_version, actor_id, updated_at, row["id"], row["active_version"]),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Die Vorlage wurde gleichzeitig geändert. Bitte erneut laden und versuchen.") from exc
    return get_reason_template(guild_id, action, name)


def archive_reason_template(guild_id, action: str, name: str, actor) -> bool:
    """Archive a template without deleting its revision history."""
    if action not in ACTIONS:
        raise ValueError("Unbekannte Moderationsaktion.")
    actor_id = str(getattr(actor, "id", actor))
    updated_at = _now().isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        _table(db)
        result = db.execute(
            "UPDATE mod_reason_templates SET archived=1,updated_by=?,updated_at=? "
            "WHERE guild_id=? AND action=? AND lower(name)=lower(?) AND archived=0",
            (actor_id, updated_at, str(guild_id), action, str(name or "").strip()),
        )
    return result.rowcount > 0


def render_reason_template(template: dict, details: str = "") -> str:
    """Render a stored revision and append details when the optional placeholder is absent."""
    body = str(template.get("body") or "").strip()
    details = str(details or "").strip()
    reason = body.replace("{details}", details) if "{details}" in body else (
        f"{body}\nZusatzdetails: {details}" if details else body
    )
    if not reason.strip() or len(reason.strip()) > MAX_REASON_LENGTH:
        raise ValueError(f"Der Vorlagengrund darf höchstens {MAX_REASON_LENGTH} Zeichen enthalten.")
    return reason.strip()


def may_moderate(action: str, pulse_perms: dict, discord_perms) -> bool:
    """Both a Pulse right (or admin) and the Discord permission for this action."""
    if action not in ACTIONS:
        return False
    has_pulse = bool(pulse_perms.get("is_admin") or pulse_perms.get(PULSE_RIGHT[action]))
    has_discord = bool(getattr(discord_perms, DISCORD_RIGHT[action], False) or getattr(discord_perms, "administrator", False))
    return has_pulse and has_discord


def target_problem(actor, target, guild, team_role_ids) -> str:
    """Empty string when the actor may act on the target, else the reason (same hierarchy rules as team actions)."""
    if target.bot:
        return "Bots werden hier nicht moderiert."
    if target.id == actor.id:
        return "Du kannst dich nicht selbst moderieren."
    if target.id == getattr(guild, "owner_id", None):
        return "Der Serverinhaber kann nicht moderiert werden."
    if any(r.id in set(int(x) for x in team_role_ids) for r in getattr(target, "roles", [])):
        return "Teammitglieder bitte über die Team-Wege (Team-Warnung, Team-Kick) behandeln."
    if getattr(target, "top_role", None) is not None and getattr(actor, "top_role", None) is not None \
            and target.top_role >= actor.top_role and actor.id != getattr(guild, "owner_id", None):
        return "Die Person steht in der Rollenhierarchie nicht unter dir."
    me = getattr(guild, "me", None)
    if me is not None and getattr(target, "top_role", None) is not None and target.top_role >= me.top_role:
        return "Pulse steht in der Rollenhierarchie nicht über dieser Person."
    return ""


def create_case(guild_id, target, action: str, reason: str, proof: str, minutes: int | None, actor, template: dict | None = None) -> dict:
    reason = (reason or "").strip()[:1000]
    if action not in ACTIONS:
        raise ValueError("Unbekannte Aktion.")
    if len(reason) < 5:
        raise ValueError("Bitte einen nachvollziehbaren Grund (mindestens 5 Zeichen) angeben.")
    if action == "timeout" and not (minutes and 1 <= minutes <= MAX_TIMEOUT_MINUTES):
        raise ValueError("Timeout: 1 Minute bis 28 Tage.")
    expires = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds") if minutes and action in ("timeout", "ban") else None
    case = {"id": "MOD-" + secrets.token_hex(3).upper(), "guild_id": str(guild_id), "target_id": str(target.id),
            "target_name": getattr(target, "display_name", str(target)), "action": action, "reason": reason,
            "proof": (proof or "").strip()[:500], "expires_at": expires, "actor_id": str(actor.id),
            "actor_name": getattr(actor, "display_name", str(actor)), "created_at": _now().isoformat(timespec="seconds"),
            "status": "active",
            "reason_template_id": str(template["id"]) if template else None,
            "reason_template_version": int(template["active_version"]) if template else None}
    with pulse_db.connect() as db:
        _table(db)
        db.execute("INSERT INTO mod_cases(id,guild_id,target_id,target_name,action,reason,proof,expires_at,actor_id,actor_name,created_at,status,reason_template_id,reason_template_version) "
                   "VALUES (:id,:guild_id,:target_id,:target_name,:action,:reason,:proof,:expires_at,:actor_id,:actor_name,:created_at,:status,:reason_template_id,:reason_template_version)", case)
    return case


def mark(case_id: str, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("status", "dm_sent", "closed_at", "closed_by", "close_note")}
    if not allowed:
        return
    with pulse_db.connect() as db:
        _table(db)
        db.execute(f"UPDATE mod_cases SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?", (*allowed.values(), case_id))


def get_case(case_id: str) -> dict | None:
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute("SELECT * FROM mod_cases WHERE id=?", (str(case_id).upper(),)).fetchone()
    return dict(row) if row else None


def list_cases(target_id=None, limit: int = 5000) -> list[dict]:
    with pulse_db.connect() as db:
        _table(db)
        if target_id:
            rows = db.execute("SELECT * FROM mod_cases WHERE target_id=? ORDER BY created_at DESC LIMIT ?", (str(target_id), limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM mod_cases ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def expired_bans(now: datetime | None = None) -> list[dict]:
    """Active bans whose expiry has passed (the automation loop lifts them)."""
    now_iso = (now or _now()).isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute("SELECT * FROM mod_cases WHERE action='ban' AND status='active' AND expires_at IS NOT NULL AND expires_at<=?",
                          (now_iso,)).fetchall()
    return [dict(r) for r in rows]


def dm_text(case: dict, server_name: str, appeal: str) -> str:
    until = f"\nBis: {case['expires_at'][:16].replace('T', ' ')} UTC" if case.get("expires_at") else ""
    return DM_TEMPLATE.format(aktion=ACTIONS[case["action"]], server=server_name, grund=case["reason"], bis=until,
                              fall=case["id"], einspruch=appeal)[:1900]


def appeal_text(guild_id: int, case_id: str) -> str:
    try:
        import pulse_panels
        panel = next((p for p in pulse_panels.collect(guild_id) if p["key"] == "tickets" and p["channel_id"]), None)
    except Exception:
        panel = None
    where = f"Öffne ein Ticket in <#{panel['channel_id']}>" if panel else "Melde dich beim Serverteam"
    return f"{where} und nenne die Fall-ID {case_id}."

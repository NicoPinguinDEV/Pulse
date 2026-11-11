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
        "reason_template_id TEXT, reason_template_version INTEGER)"
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(mod_cases)").fetchall()}
    if "reason_template_id" not in columns:
        db.execute("ALTER TABLE mod_cases ADD COLUMN reason_template_id TEXT")
    if "reason_template_version" not in columns:
        db.execute("ALTER TABLE mod_cases ADD COLUMN reason_template_version INTEGER")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mod_cases_target ON mod_cases(target_id, created_at)")


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


def create_case(
    guild_id,
    target,
    action: str,
    reason: str,
    proof: str,
    minutes: int | None,
    actor,
    *,
    reason_template_id: str | None = None,
    reason_template_version: int | None = None,
) -> dict:
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
            "status": "active", "reason_template_id": str(reason_template_id) if reason_template_id else None,
            "reason_template_version": int(reason_template_version) if reason_template_version is not None else None}
    with pulse_db.connect() as db:
        _table(db)
        db.execute(
            "INSERT INTO mod_cases(id,guild_id,target_id,target_name,action,reason,proof,expires_at,"
            "actor_id,actor_name,created_at,status,reason_template_id,reason_template_version) "
            "VALUES (:id,:guild_id,:target_id,:target_name,:action,:reason,:proof,:expires_at,"
            ":actor_id,:actor_name,:created_at,:status,:reason_template_id,:reason_template_version)",
            case,
        )
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


APPEAL_WINDOW_DAYS = 14
APPEAL_REASON_MIN_LENGTH = 20
APPEAL_REASON_MAX_LENGTH = 1500
APPEAL_DECISION_STATUSES = {"accepted", "rejected"}


def appeal_deadline(case: dict, days: int = APPEAL_WINDOW_DAYS) -> datetime:
    """Return the UTC deadline measured from the original moderation decision."""
    try:
        created = datetime.fromisoformat(str(case.get("created_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Für diesen Fall ist kein gültiges Entscheidungsdatum gespeichert.") from exc
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(timezone.utc) + timedelta(days=max(1, min(int(days), 30)))


def _appeals_table(db) -> None:
    """Keep the new table available when older databases are first opened."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS mod_case_appeals ("
        "case_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, target_id TEXT NOT NULL, "
        "reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', submitted_at TEXT NOT NULL, "
        "deadline_at TEXT NOT NULL, reviewed_by_id TEXT, reviewed_by_name TEXT, reviewed_at TEXT, "
        "decision_note TEXT DEFAULT '', "
        "FOREIGN KEY(case_id) REFERENCES mod_cases(id) ON DELETE CASCADE)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_mod_case_appeals_guild_status "
        "ON mod_case_appeals(guild_id, status, submitted_at DESC)"
    )


def get_appeal(case_id: str, guild_id=None) -> dict | None:
    with pulse_db.connect() as db:
        _appeals_table(db)
        if guild_id is None:
            row = db.execute(
                "SELECT * FROM mod_case_appeals WHERE case_id=?",
                (str(case_id or "").strip().upper(),),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM mod_case_appeals WHERE case_id=? AND guild_id=?",
                (str(case_id or "").strip().upper(), str(guild_id)),
            ).fetchone()
    return dict(row) if row else None


def can_submit_appeal(guild_id, target_id, case_id: str, now: datetime | None = None) -> dict:
    """Validate the exact case owner and 14-day window, returning the case and deadline."""
    guild, target = str(guild_id or "").strip(), str(target_id or "").strip()
    if not guild.isdigit() or not target.isdigit():
        raise ValueError("Server und Nutzer konnten nicht eindeutig zugeordnet werden.")
    case = get_case(case_id)
    if not case or case.get("guild_id") != guild or case.get("target_id") != target:
        raise ValueError("Dieser Moderationsfall gehört nicht zu deinem Konto.")
    if case.get("status") == "failed":
        raise ValueError("Zu einer fehlgeschlagenen Moderationsaktion kann kein Einspruch eingereicht werden.")
    deadline = appeal_deadline(case)
    current = now or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current.astimezone(timezone.utc) > deadline:
        raise ValueError("Die Einspruchsfrist von 14 Tagen ist abgelaufen.")
    if get_appeal(case["id"], guild):
        raise ValueError("Für diesen Fall wurde bereits ein Einspruch eingereicht.")
    return {"case": case, "deadline": deadline}


def submit_appeal(guild_id, target_id, case_id: str, reason: str, now: datetime | None = None) -> dict:
    """Store one appeal for the affected member; it never changes the moderation action."""
    validated = can_submit_appeal(guild_id, target_id, case_id, now=now)
    text = " ".join(str(reason or "").split())
    if not APPEAL_REASON_MIN_LENGTH <= len(text) <= APPEAL_REASON_MAX_LENGTH:
        raise ValueError(
            f"Die Begründung muss {APPEAL_REASON_MIN_LENGTH} bis {APPEAL_REASON_MAX_LENGTH} Zeichen enthalten."
        )
    submitted = now or _now()
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=timezone.utc)
    submitted = submitted.astimezone(timezone.utc)
    row = {
        "case_id": validated["case"]["id"],
        "guild_id": str(guild_id),
        "target_id": str(target_id),
        "reason": text,
        "status": "pending",
        "submitted_at": submitted.isoformat(timespec="seconds"),
        "deadline_at": validated["deadline"].isoformat(timespec="seconds"),
        "reviewed_by_id": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "decision_note": "",
    }
    try:
        with pulse_db.connect() as db:
            _appeals_table(db)
            db.execute(
                """INSERT INTO mod_case_appeals(
                    case_id,guild_id,target_id,reason,status,submitted_at,deadline_at,
                    reviewed_by_id,reviewed_by_name,reviewed_at,decision_note
                ) VALUES (
                    :case_id,:guild_id,:target_id,:reason,:status,:submitted_at,:deadline_at,
                    :reviewed_by_id,:reviewed_by_name,:reviewed_at,:decision_note
                )""",
                row,
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Für diesen Fall wurde bereits ein Einspruch eingereicht.") from exc
    return row


def list_appeals(guild_id, status: str | None = "pending", limit: int = 25) -> list[dict]:
    guild = str(guild_id or "").strip()
    if not guild.isdigit():
        return []
    try:
        bounded = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        bounded = 25
    if status is not None and status not in {"pending", *APPEAL_DECISION_STATUSES}:
        raise ValueError("Unbekannter Einspruchsstatus.")
    with pulse_db.connect() as db:
        _appeals_table(db)
        if status is None:
            rows = db.execute(
                "SELECT * FROM mod_case_appeals WHERE guild_id=? ORDER BY submitted_at DESC LIMIT ?",
                (guild, bounded),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM mod_case_appeals WHERE guild_id=? AND status=? ORDER BY submitted_at DESC LIMIT ?",
                (guild, status, bounded),
            ).fetchall()
    return [dict(row) for row in rows]


def decide_appeal(
    guild_id, case_id: str, decision: str, note: str, reviewer_id, reviewer_name: str,
    now: datetime | None = None,
) -> dict:
    """Record a human decision once. Accepting an appeal never lifts or alters a sanction."""
    guild, reviewer = str(guild_id or "").strip(), str(reviewer_id or "").strip()
    decision = str(decision or "").strip().lower()
    text = " ".join(str(note or "").split())
    if not guild.isdigit() or not reviewer.isdigit():
        raise ValueError("Server und prüfende Person müssen eindeutig sein.")
    if decision not in APPEAL_DECISION_STATUSES:
        raise ValueError("Entscheidung muss angenommen oder abgelehnt sein.")
    if not 5 <= len(text) <= 500:
        raise ValueError("Die interne Entscheidungsnotiz muss 5 bis 500 Zeichen enthalten.")
    case_id = str(case_id or "").strip().upper()
    reviewed_at = now or _now()
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
    with pulse_db.connect() as db:
        _appeals_table(db)
        cursor = db.execute(
            """UPDATE mod_case_appeals
               SET status=?, reviewed_by_id=?, reviewed_by_name=?, reviewed_at=?, decision_note=?
               WHERE case_id=? AND guild_id=? AND status='pending'""",
            (decision, reviewer, str(reviewer_name or reviewer)[:100],
             reviewed_at.astimezone(timezone.utc).isoformat(timespec="seconds"), text,
             case_id, guild),
        )
        if cursor.rowcount != 1:
            raise ValueError("Offener Einspruch wurde nicht gefunden oder bereits entschieden.")
        row = db.execute(
            "SELECT * FROM mod_case_appeals WHERE case_id=? AND guild_id=?",
            (case_id, guild),
        ).fetchone()
    return dict(row)


def list_appealable_cases(now: datetime | None = None, limit: int = 500) -> list[dict]:
    """Bounded cases eligible for persistent DM buttons after a bot restart."""
    current = now or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current.astimezone(timezone.utc) - timedelta(days=APPEAL_WINDOW_DAYS)).isoformat(timespec="seconds")
    try:
        bounded = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        bounded = 500
    with pulse_db.connect() as db:
        _appeals_table(db)
        rows = db.execute(
            """SELECT cases.* FROM mod_cases AS cases
               LEFT JOIN mod_case_appeals AS appeals ON appeals.case_id=cases.id
               WHERE cases.status!='failed' AND cases.created_at>=? AND appeals.case_id IS NULL
               ORDER BY cases.created_at DESC LIMIT ?""",
            (cutoff, bounded),
        ).fetchall()
    return [dict(row) for row in rows]


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
    """Instruction displayed with the persistent DM button; works after kick and ban."""
    return (
        f"Du kannst innerhalb von {APPEAL_WINDOW_DAYS} Tagen über den Button "
        f"„Einspruch einreichen“ unter dieser Nachricht eine Prüfung beantragen. "
        f"Nenne dabei keine weiteren vertraulichen Daten; die Fall-ID lautet {case_id}."
    )

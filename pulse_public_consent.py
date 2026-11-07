"""Explicit, revocable consent for public BOCHUM RP team profile display."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pulse_db

CONSENT_VERSION = 2


def _role_ids(values) -> list[int]:
    result = set()
    for value in values or ():
        try:
            role_id = int(value)
        except (TypeError, ValueError):
            continue
        if role_id > 0:
            result.add(role_id)
    return sorted(result)


def _ensure_table(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS public_team_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            consent_version INTEGER NOT NULL,
            role_ids_json TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            withdrawn_at TEXT
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_team_consents_active "
        "ON public_team_consents(consent_version, withdrawn_at)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_public_team_consents_one_active "
        "ON public_team_consents(user_id) WHERE withdrawn_at IS NULL"
    )


def grant(user_id: int | str, role_ids) -> dict:
    """Record a positive opt-in after the user has seen the versioned disclosure."""
    uid = str(user_id).strip()
    roles = _role_ids(role_ids)
    if not uid.isdigit() or int(uid) <= 0:
        raise ValueError("Ungültige Discord-ID.")
    if not roles:
        raise ValueError("Ohne aktuelle Teamrolle kann keine öffentliche Teamfreigabe erteilt werden.")
    granted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    role_json = json.dumps(roles, separators=(",", ":"))
    with pulse_db.connect() as db:
        _ensure_table(db)
        # Keep a separate, timestamped record for each grant/withdrawal cycle.
        db.execute(
            "UPDATE public_team_consents SET withdrawn_at=? WHERE user_id=? AND withdrawn_at IS NULL",
            (granted_at, uid),
        )
        db.execute(
            """INSERT INTO public_team_consents
               (user_id, consent_version, role_ids_json, granted_at, withdrawn_at)
               VALUES (?, ?, ?, ?, NULL)""",
            (uid, CONSENT_VERSION, role_json, granted_at),
        )
    return {"user_id": uid, "consent_version": CONSENT_VERSION,
            "role_ids": roles, "granted_at": granted_at}


def withdraw(user_id: int | str) -> bool:
    """Withdraw public display; a minimal record of the withdrawal is retained as evidence."""
    uid = str(user_id).strip()
    if not uid.isdigit() or int(uid) <= 0:
        return False
    withdrawn_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        _ensure_table(db)
        cursor = db.execute(
            """UPDATE public_team_consents SET withdrawn_at=?
               WHERE user_id=? AND withdrawn_at IS NULL""",
            (withdrawn_at, uid),
        )
        return cursor.rowcount > 0


def active_consents() -> dict[str, dict]:
    """Return current-version, non-withdrawn consents; never return profile content."""
    with pulse_db.connect() as db:
        _ensure_table(db)
        rows = db.execute(
            """SELECT user_id, role_ids_json, granted_at
               FROM public_team_consents
               WHERE consent_version=? AND withdrawn_at IS NULL""",
            (CONSENT_VERSION,),
        ).fetchall()
    result = {}
    for row in rows:
        try:
            roles = _role_ids(json.loads(row["role_ids_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if roles:
            result[str(row["user_id"])] = {
                "role_ids": roles,
                "granted_at": str(row["granted_at"]),
            }
    return result


def revoke_if_role_scope_changed(user_id: int | str, current_role_ids) -> bool:
    """Require fresh consent after a member's configured TeamBot role set changes."""
    uid = str(user_id).strip()
    active = active_consents().get(uid)
    if not active:
        return False
    return withdraw(uid) if active["role_ids"] != _role_ids(current_role_ids) else False

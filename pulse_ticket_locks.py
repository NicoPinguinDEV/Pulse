"""Temporary, server-scoped ticket creation locks stored in Pulse's existing settings table.

Locks apply only to future ticket creation, expire lazily when checked, and can be
cleared administratively. Malformed stored lock records fail closed until an admin
unlocks them; existing ticket channels and history are never modified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pulse_db


MAX_LOCK_HOURS = 168
MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 500


def _key(guild_id, user_id) -> str:
    return f"ticket_lock:{int(guild_id)}:{int(user_id)}"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def create_lock(guild_id, user_id, duration_hours, reason, *, actor_id, actor_name, now=None) -> dict:
    try:
        hours = int(duration_hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("Die Sperrdauer muss eine Zahl von 1 bis 168 Stunden sein.") from exc
    if not 1 <= hours <= MAX_LOCK_HOURS:
        raise ValueError("Die Sperrdauer muss zwischen 1 und 168 Stunden liegen.")
    reason_text = str(reason or "").strip()
    if len(reason_text) < MIN_REASON_LENGTH:
        raise ValueError("Der Sperrgrund muss mindestens fünf Zeichen enthalten.")
    if len(reason_text) > MAX_REASON_LENGTH:
        raise ValueError(f"Der Sperrgrund darf höchstens {MAX_REASON_LENGTH} Zeichen enthalten.")
    current = _aware(now or datetime.now(timezone.utc))
    record = {
        "active": True,
        "guild_id": str(int(guild_id)),
        "user_id": str(int(user_id)),
        "reason": reason_text,
        "created_at": current.isoformat(timespec="seconds"),
        "expires_at": (current + timedelta(hours=hours)).isoformat(timespec="seconds"),
        "duration_hours": hours,
        "created_by_id": str(int(actor_id)),
        "created_by_name": str(actor_name or "Unbekannt")[:100],
    }
    pulse_db.set_setting(_key(guild_id, user_id), record)
    pulse_db.record_event(
        "ticket_member_locked", "member", str(int(user_id)), actor_id, actor_name,
        {"guild_id": str(int(guild_id)), "duration_hours": hours,
         "expires_at": record["expires_at"], "reason": reason_text},
    )
    return record


def get_lock(guild_id, user_id, *, now=None) -> dict | None:
    raw = pulse_db.get_setting(_key(guild_id, user_id))
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"active": True, "reason": "Sperrdaten sind ungültig.", "expires_at": ""}
    if not raw.get("active", True):
        pulse_db.delete_setting(_key(guild_id, user_id))
        return None
    try:
        expires = datetime.fromisoformat(str(raw.get("expires_at") or "").replace("Z", "+00:00"))
        expires = _aware(expires)
    except (TypeError, ValueError):
        return {"active": True, "reason": str(raw.get("reason") or "Sperrdaten sind ungültig."),
                "expires_at": "", "invalid": True}
    if expires <= _aware(now or datetime.now(timezone.utc)):
        pulse_db.delete_setting(_key(guild_id, user_id))
        return None
    return dict(raw)


def release_lock(guild_id, user_id, *, actor_id=None, actor_name=None) -> dict | None:
    key = _key(guild_id, user_id)
    raw = pulse_db.get_setting(key)
    if raw is None:
        return None
    pulse_db.delete_setting(key)
    if isinstance(raw, dict):
        pulse_db.record_event(
            "ticket_member_lock_removed", "member", str(int(user_id)),
            actor_id or "0", actor_name or "System",
            {"guild_id": str(int(guild_id)), "reason": str(raw.get("reason") or ""),
             "original_expires_at": str(raw.get("expires_at") or "")},
        )
        return raw
    return {"reason": "Sperrdaten waren ungültig.", "expires_at": ""}

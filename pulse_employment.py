"""Minimal, independent record of a confirmed Pulse team-entry event.

The event deliberately excludes application answers, interview notes, review text and
reasons. It is stored inside the existing per-user team record and is not a migration.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_EMPLOYMENT_SOURCES = frozenset({
    "application_interview",
    "manual_hire",
    "team_role_assignment",
})
_DISCORD_ID = re.compile(r"^[0-9]{1,20}$")
_AUDIT_REF = re.compile(r"^audit_[0-9a-f]{6}$")
_CAREER_PATH = re.compile(r"^[a-z0-9_-]{1,64}$")


def build_employment_event(
    *,
    user_id: Any,
    hired_at: Any,
    actor_id: Any,
    source: str,
    audit_ref: str,
    career_path: Any = None,
) -> dict[str, Any]:
    """Build a validated, minimal v1 employment event."""
    normalized_user_id = str(user_id or "").strip()
    normalized_actor_id = str(actor_id or "").strip()
    normalized_source = str(source or "").strip()
    normalized_audit_ref = str(audit_ref or "").strip()
    if not _DISCORD_ID.fullmatch(normalized_user_id):
        raise ValueError("employment event requires a valid Discord user ID")
    if not _DISCORD_ID.fullmatch(normalized_actor_id):
        raise ValueError("employment event requires a valid actor ID")
    if normalized_source not in _EMPLOYMENT_SOURCES:
        raise ValueError("employment event source is not supported")
    if not _AUDIT_REF.fullmatch(normalized_audit_ref):
        raise ValueError("employment event requires a valid audit reference")

    timestamp = str(hired_at or "").strip()
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("employment event timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("employment event timestamp must include a timezone")

    event: dict[str, Any] = {
        "version": 1,
        "user_id": normalized_user_id,
        "hired_at": parsed.isoformat(timespec="seconds"),
        "actor_id": normalized_actor_id,
        "source": normalized_source,
        "audit_ref": normalized_audit_ref,
    }
    normalized_career_path = str(career_path or "").strip().casefold()
    if normalized_career_path and _CAREER_PATH.fullmatch(normalized_career_path):
        event["career_path"] = normalized_career_path
    return event


def store_employment_event(team_entry: dict[str, Any], event: dict[str, Any]) -> bool:
    """Store the current confirmed-entry event; repeating its audit reference is a no-op.

    A later, separately confirmed re-entry replaces the current-tenure event, matching
    the existing team_since behaviour. Older actions remain in the audit log.
    """
    if not isinstance(team_entry, dict):
        raise TypeError("team entry must be an object")
    if not isinstance(event, dict) or not event.get("audit_ref"):
        raise ValueError("a validated employment event is required")
    current = team_entry.get("employment_event")
    if isinstance(current, dict) and current.get("audit_ref") == event.get("audit_ref"):
        return False
    team_entry["employment_event"] = dict(event)
    return True


def employment_hired_at(team_entry: Any, user_id: Any) -> str | None:
    """Return the timestamp only when the stored event belongs to the requested user."""
    if not isinstance(team_entry, dict):
        return None
    event = team_entry.get("employment_event")
    if not isinstance(event, dict):
        return None
    if str(event.get("user_id") or "").strip() != str(user_id or "").strip():
        return None
    timestamp = str(event.get("hired_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.isoformat(timespec="seconds")

"""Shared, side-effect-free Team-Warn record operations.

Discord role changes, authorization, consequential actions, notifications, and
persistence remain in their existing callers. This module keeps the durable warning
schema and the state transition identical for dashboard and slash-command flows.
"""
from __future__ import annotations

from datetime import timedelta


def build_warning_record(
    *,
    warning_id: str,
    reason: str,
    proof: str,
    by: str,
    issued_at,
    duration_days: int,
    max_reason_length: int = 1000,
) -> dict:
    """Build the existing durable warning schema with a consistent expiry calculation."""
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValueError("Ein Warnungsgrund ist erforderlich.")
    try:
        days = int(duration_days)
        maximum = max(1, int(max_reason_length))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültige Warnungsdauer oder -länge.") from exc
    if not 1 <= days <= 365:
        raise ValueError("Die Warnungsdauer muss zwischen 1 und 365 Tagen liegen.")
    if not warning_id or not str(warning_id).strip():
        raise ValueError("Eine Warnungs-ID ist erforderlich.")
    if issued_at is None or not hasattr(issued_at, "strftime") or not hasattr(issued_at, "isoformat"):
        raise ValueError("Ein gültiger Ausstellungszeitpunkt ist erforderlich.")

    issued = issued_at
    expiry = issued + timedelta(days=days)
    return {
        "id": str(warning_id),
        "reason": reason_text[:maximum],
        "proof": str(proof or "").strip(),
        "by": str(by or "System").strip()[:100],
        "date": issued.strftime("%d.%m.%Y %H:%M"),
        "duration_days": days,
        "expires_at": expiry.isoformat(timespec="seconds"),
        "active": True,
        "revoked_at": None,
        "revoked_by": None,
        "revoked_reason": "",
    }


def revoke_warning_record(
    entry: dict,
    *,
    warning_id: str,
    revoked_at: str,
    revoked_by: str,
    reason: str,
) -> dict | None:
    """Revoke one active warning in-place; return it, or None when already absent/revoked."""
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValueError("Ein Rücknahmegrund ist erforderlich.")
    warnings = entry.get("warns_list") if isinstance(entry, dict) else None
    if not isinstance(warnings, list):
        return None
    target = next(
        (
            item for item in warnings
            if isinstance(item, dict) and str(item.get("id") or "") == str(warning_id)
        ),
        None,
    )
    if target is None or not target.get("active", True) or target.get("revoked_at"):
        return None
    target["active"] = False
    target["revoked_at"] = str(revoked_at or "")
    target["revoked_by"] = str(revoked_by or "System")[:100]
    target["revoked_reason"] = reason_text[:1000]
    return target

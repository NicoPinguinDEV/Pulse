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


async def issue_team_warning(
    *,
    web,
    guild,
    actor_member,
    target,
    entry: dict,
    tracker,
    warning_id: str,
    reason: str,
    proof: str,
    by: str,
    duration_days: int,
    max_reason_length: int,
    config: dict,
    source: str,
) -> dict:
    """Run shared warning checks, persist the record and synchronize its effects.

    Caller-specific permission and rank validation stay in the dashboard/command.
    This function owns the common active-warning limit, protection approvals,
    canonical record, merge-on-save checkpoints, role sync and warning consequences.
    """
    current_count = len(web.active_warns(entry))
    if current_count >= 5:
        return {"ok": False, "message": "Dieses Teammitglied hat bereits 5 aktive Verwarnungen."}

    allowed_warn, protection_message = await web.enforce_team_warn_protection(
        guild, actor_member, target, config, source=source,
    )
    if not allowed_warn:
        return {"ok": False, "message": protection_message}

    allowed_impact, impact_message = await web.enforce_high_impact_warning_approval(
        guild, actor_member, target, entry, current_count, config, source=source,
    )
    if not allowed_impact:
        return {"ok": False, "message": impact_message}

    warning = build_warning_record(
        warning_id=warning_id,
        reason=reason,
        proof=proof,
        by=by,
        issued_at=web.now_de(),
        duration_days=duration_days,
        max_reason_length=max_reason_length,
    )
    entry.setdefault("warns_list", []).append(warning)
    tracker.commit()
    count = len(web.active_warns(entry))

    role_report = await web.sync_warn_roles(guild, target, count, config)
    consequence_report = await web.apply_warn_consequences(
        guild, target, warning, count, config, entry=entry,
    )
    tracker.commit()
    return {
        "ok": True,
        "warning": warning,
        "count": count,
        "role_report": role_report,
        "consequence_report": consequence_report,
    }

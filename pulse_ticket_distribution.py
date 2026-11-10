"""Deterministic, opt-in ticket assignment based on the eligible staff's open workload.

No Discord calls or database access live here; callers supply only the already-authorized
members and tickets for one guild/category. Kept pure so capacity rules remain testable.
"""
from __future__ import annotations

DISTRIBUTION_DEFAULTS = {
    "enabled": False,
    "max_open_per_staff": 5,
}
_PRIORITY_WEIGHT = {"urgent": 4, "high": 3, "normal": 2, "low": 1}


def distribution_settings(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get("enabled", DISTRIBUTION_DEFAULTS["enabled"])
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = bool(enabled)
    try:
        maximum = int(raw.get("max_open_per_staff", DISTRIBUTION_DEFAULTS["max_open_per_staff"]))
    except (TypeError, ValueError):
        maximum = DISTRIBUTION_DEFAULTS["max_open_per_staff"]
    return {
        "enabled": enabled,
        "max_open_per_staff": max(1, min(maximum, 50)),
    }


def choose_assignee(
    candidates: list[dict],
    open_tickets: list[dict],
    guild_id,
    max_open_per_staff: int = 5,
) -> dict | None:
    """Choose the least-loaded eligible candidate; return None when everyone is at capacity."""
    guild_key = str(guild_id or "").strip()
    if not guild_key:
        return None
    try:
        capacity = max(1, min(int(max_open_per_staff), 50))
    except (TypeError, ValueError):
        capacity = DISTRIBUTION_DEFAULTS["max_open_per_staff"]

    unique = {}
    for candidate in candidates or []:
        if not isinstance(candidate, dict) or candidate.get("bot"):
            continue
        try:
            member_id = str(int(candidate.get("id")))
        except (TypeError, ValueError):
            continue
        if member_id == "0":
            continue
        unique.setdefault(member_id, {
            "id": member_id,
            "name": str(candidate.get("name") or candidate.get("display_name") or member_id)[:100],
        })
    if not unique:
        return None

    counts = {member_id: 0 for member_id in unique}
    workload = {member_id: 0 for member_id in unique}
    for ticket in open_tickets or []:
        if not isinstance(ticket, dict):
            continue
        if str(ticket.get("guild_id") or "") != guild_key:
            continue
        if str(ticket.get("status") or "").lower() == "closed":
            continue
        claimed_by = str(ticket.get("claimed_by_id") or "")
        if claimed_by not in counts:
            continue
        counts[claimed_by] += 1
        workload[claimed_by] += _PRIORITY_WEIGHT.get(
            str(ticket.get("priority") or "normal").lower(), 2
        )

    eligible = [
        member_id for member_id in unique
        if counts[member_id] < capacity
    ]
    if not eligible:
        return None
    selected = min(
        eligible,
        key=lambda member_id: (workload[member_id], counts[member_id], int(member_id)),
    )
    return {
        **unique[selected],
        "open_ticket_count": counts[selected],
        "workload_points": workload[selected],
        "capacity": capacity,
    }

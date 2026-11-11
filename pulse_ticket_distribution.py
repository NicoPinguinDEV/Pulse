"""Deterministic, opt-in ticket assignment based on the eligible staff's open workload.

No Discord calls or database access live here; callers supply only the already-authorized
members and tickets for one guild/category. Kept pure so capacity rules remain testable.
"""
from __future__ import annotations

from datetime import date, datetime

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


def _absence_date(value) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def active_absence_ids(rows, guild_id, today: date) -> set[str]:
    """Member IDs with a recorded absence that covers `today` (rows: user_id, von, bis, guild_id).

    Only a clearly past or clearly future absence is ignored. An entry whose dates cannot be read
    counts as active: assigning a ticket to someone who is away is the worse mistake.
    Entries without a guild (older records) apply to every guild.
    """
    guild_key = str(guild_id or "").strip()
    absent = set()
    for row in list(rows or [])[:5000]:
        try:
            user_id, start, end, row_guild = row[0], row[1], row[2], row[3]
            member_id = str(int(user_id))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if row_guild not in (None, "", 0, "0") and str(row_guild) != guild_key:
            continue
        start_date, end_date = _absence_date(start), _absence_date(end)
        if end_date is not None and today > end_date:
            continue
        if start_date is not None and today < start_date:
            continue
        absent.add(member_id)
    return absent


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


def workload_snapshot(
    open_tickets: list[dict],
    guild_id,
    max_open_per_staff: int = 5,
    limit: int = 8,
) -> dict:
    """Summarize assigned ticket load for leadership warnings."""
    guild_key = str(guild_id or "").strip()
    try:
        capacity = max(1, min(int(max_open_per_staff), 50))
    except (TypeError, ValueError):
        capacity = DISTRIBUTION_DEFAULTS["max_open_per_staff"]
    try:
        output_limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        output_limit = 8
    if not guild_key:
        return {
            "max_open_per_staff": capacity,
            "staff_count": 0,
            "assigned_open_tickets": 0,
            "overloaded_count": 0,
            "staff": [],
        }

    loads = {}
    source = open_tickets if isinstance(open_tickets, (list, tuple)) else ()
    for ticket in source[:2000]:
        if not isinstance(ticket, dict):
            continue
        if str(ticket.get("guild_id") or "") != guild_key:
            continue
        if str(ticket.get("status") or "").strip().lower() == "closed":
            continue
        member_id = str(ticket.get("claimed_by_id") or "").strip()
        if not member_id or member_id == "0":
            continue
        row = loads.setdefault(member_id, {
            "member_id": member_id,
            "name": str(ticket.get("claimed_by_name") or member_id)[:100],
            "open_ticket_count": 0,
            "workload_points": 0,
            "urgent_count": 0,
            "high_count": 0,
        })
        row["open_ticket_count"] += 1
        priority = str(ticket.get("priority") or "normal").strip().lower()
        row["workload_points"] += _PRIORITY_WEIGHT.get(priority, 2)
        if priority == "urgent":
            row["urgent_count"] += 1
        elif priority == "high":
            row["high_count"] += 1

    staff = sorted(
        loads.values(),
        key=lambda row: (-row["open_ticket_count"], -row["workload_points"], row["member_id"]),
    )
    for row in staff:
        row["capacity"] = capacity
        row["at_capacity"] = row["open_ticket_count"] >= capacity
        row["remaining_capacity"] = max(0, capacity - row["open_ticket_count"])

    return {
        "max_open_per_staff": capacity,
        "staff_count": len(staff),
        "assigned_open_tickets": sum(row["open_ticket_count"] for row in staff),
        "overloaded_count": sum(1 for row in staff if row["at_capacity"]),
        "staff": staff[:output_limit],
    }

"""F-01 "Wer darf was?" and F-02 channel visibility for unverified members – both read-only.

F-01 builds on pulse_permissions (A-03): the matrix of every team member × dashboard permission with the
origin of each permission, and a simulation "what could this person do with these team roles?" that
never touches Discord roles.
F-02 computes, from Discord's own overwrite rules, which channels @everyone plus the unverified role can
see. Nothing is changed in Discord; the result is a warning list for an admin.
"""
from __future__ import annotations

from types import SimpleNamespace

import pulse_permissions

LABELS = {
    "can_view_dashboard": "Dashboard sehen", "can_warn": "Verwarnen", "can_promote": "Befördern (Leitung)",
    "can_add_notes": "Notizen", "can_manage_tickets": "Tickets verwalten", "can_manage_applications": "Bewerbungen",
    "can_manage_tasks": "Aufgaben", "can_manage_training": "Schulungen", "can_manage_wiki": "Wiki",
    "can_view_analytics": "Analytics & Exporte", "can_manage_announcements": "Ankündigungen",
    "can_manage_handover": "Übergaben", "can_manage_promotions": "Beförderungsanträge",
    "can_view_team_status": "Teamstatus", "can_manage_checkins": "Activity Check",
    "can_moderate_kicks": "Melonly-Kick", "can_moderate_bans": "Melonly-Ban", "can_moderate_bolo": "Melonly-BOLO",
}


def team_members(guild, config: dict) -> list:
    team_ids = {int(x) for x in config.get("team_role_ids", []) if str(x).isdigit()}
    return sorted((m for m in getattr(guild, "members", []) or [] if not getattr(m, "bot", False)
                   and any(r.id in team_ids for r in getattr(m, "roles", []))), key=lambda m: m.display_name.lower())


def matrix(guild, config: dict) -> list[dict]:
    """[{"member", "is_admin", "grants": {key: [origins]}}] for every team member."""
    rows = []
    for member in team_members(guild, config):
        info = pulse_permissions.permission_grants(guild, member, config)
        rows.append({"member": member, "is_admin": info["is_admin"], "grants": info["grants"]})
    return rows


def simulate(guild, member, role_ids: list[int], config: dict) -> dict:
    """Permissions the person *would* have with exactly these roles (Discord admin flag kept as is)."""
    roles = [r for r in (guild.get_role(int(rid)) for rid in role_ids) if r is not None]
    fake = SimpleNamespace(id=getattr(member, "id", 0), roles=roles,
                           guild_permissions=getattr(member, "guild_permissions", None))
    return pulse_permissions.permission_grants(guild, fake, config)


# --------------------------------------------------------------------------- F-02
VIEW = 1 << 10  # discord.Permissions.view_channel
ADMINISTRATOR = 1 << 3


def _pair(overwrite) -> tuple[int, int]:
    allow, deny = overwrite.pair()
    return allow.value, deny.value


def can_view(channel, roles: list, everyone) -> bool:
    """Discord's permission resolution for a set of roles (no member overwrite): base role permissions,
    then the @everyone overwrite, then all role overwrites together (deny first, then allow)."""
    base = 0
    for role in roles:
        base |= role.permissions.value
    if base & ADMINISTRATOR:
        return True
    overwrites = getattr(channel, "overwrites", {}) or {}
    for target, overwrite in overwrites.items():
        if getattr(target, "id", None) == everyone.id:
            allow, deny = _pair(overwrite)
            base = (base & ~deny) | allow
    allow_all = deny_all = 0
    role_ids = {r.id for r in roles if r.id != everyone.id}
    for target, overwrite in overwrites.items():
        if getattr(target, "id", None) in role_ids:
            allow, deny = _pair(overwrite)
            allow_all |= allow
            deny_all |= deny
    base = (base & ~deny_all) | allow_all
    return bool(base & VIEW)


EXPECTED_KEY = "visibility_expected_channels"


def visibility(guild, unverified_role_id: int, expected_ids: set[int]) -> dict:
    """{"role_found": bool, "visible": [...], "warnings": [...]} – warnings = visible but not marked as expected."""
    everyone = guild.default_role
    unverified = guild.get_role(int(unverified_role_id))
    roles = [everyone] + ([unverified] if unverified is not None else [])
    visible = []
    for channel in getattr(guild, "channels", []) or []:
        if getattr(channel, "type", None) is not None and str(channel.type) == "category":
            continue
        if can_view(channel, roles, everyone):
            visible.append(channel)
    visible.sort(key=lambda c: (getattr(c, "position", 0), c.name))
    warnings = [c for c in visible if c.id not in expected_ids]
    return {"role_found": unverified is not None, "visible": visible, "warnings": warnings}

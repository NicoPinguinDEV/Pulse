"""A-03: one place that computes dashboard permissions – with the origin of every permission.

Rules (deny by default):
* Server owner and Discord administrators: every permission, `is_admin`.
* Otherwise permissions come **only** from configured team roles (`config["team_role_ids"]`):
  - `config["permissions"][role_id]` of a team role,
  - a team role that was never configured: only `can_view_dashboard` (E-4 – analytics only explicitly),
  - dashboard profiles (`config["dashboard_roles"]`) mapped to one of the member's team roles.
  A role that is not (or no longer) a team role grants nothing, even if it still has an entry in
  `config["permissions"]` – before A-03 the extra Pulse permissions ignored that rule.
* Inheritances live only here (`INHERITED`).
Dashboard profiles never change Discord roles or `is_admin`.
"""
from __future__ import annotations

BASE_KEYS = (
    "can_view_dashboard", "can_warn", "can_promote", "can_add_notes",
    "can_manage_tickets", "can_manage_applications", "can_manage_tasks",
    "can_manage_training", "can_manage_wiki", "can_view_analytics",
)
EXTRA_KEYS = (
    "can_manage_announcements", "can_manage_handover", "can_manage_promotions", "can_view_team_status",
    "can_manage_checkins", "can_moderate_kicks", "can_moderate_bans", "can_moderate_bolo",
)
ALL_KEYS = BASE_KEYS + EXTRA_KEYS
DEFAULT_UNCONFIGURED_TEAM_ROLE = ("can_view_dashboard",)  # E-4
# Leadership (can_promote) also manages these – the single place for implicit inheritance.
INHERITED = {"can_promote": ("can_manage_applications", "can_manage_tickets", "can_manage_announcements",
                             "can_manage_promotions")}


def _ids(values) -> set[int]:
    return {int(v) for v in (values or []) if str(v).isdigit()}


def permission_grants(guild, member, config: dict) -> dict:
    """{"is_admin": bool, "grants": {permission: [origin, ...]}} for one member (None → nothing)."""
    result = {"is_admin": False, "grants": {}}
    if member is None:
        return result
    grants: dict[str, list[str]] = {}

    def grant(key, origin):
        grants.setdefault(key, [])
        if origin not in grants[key]:
            grants[key].append(origin)

    if member.id == getattr(guild, "owner_id", None) or getattr(getattr(member, "guild_permissions", None), "administrator", False):
        result["is_admin"] = True
        for key in ALL_KEYS:
            grant(key, "Serverinhaber/Administrator")
        result["grants"] = grants
        return result

    team_ids = _ids(config.get("team_role_ids", []))
    configured = config.get("permissions", {}) or {}
    member_roles = list(getattr(member, "roles", []) or [])
    for role in member_roles:
        if role.id not in team_ids:
            continue  # deny by default: only team roles grant dashboard permissions
        role_perms = configured.get(str(role.id))
        name = getattr(role, "name", str(role.id))
        if role_perms is None:
            for key in DEFAULT_UNCONFIGURED_TEAM_ROLE:
                grant(key, f"Teamrolle {name} (Standard, nie konfiguriert)")
            continue
        for key in ALL_KEYS:
            if isinstance(role_perms, dict) and role_perms.get(key):
                grant(key, f"Teamrolle {name}")

    member_role_ids = {int(role.id) for role in member_roles}
    for profile_id, profile in (config.get("dashboard_roles", {}) or {}).items():
        if not isinstance(profile, dict):
            continue
        if not (_ids(profile.get("discord_role_ids", [])) & member_role_ids & team_ids):
            continue
        profile_perms = profile.get("permissions", {}) or {}
        for key in ALL_KEYS:
            if profile_perms.get(key):
                grant(key, f"Dashboard-Profil {profile.get('name') or profile_id}")

    for source, inherited in INHERITED.items():
        if source in grants:
            for key in inherited:
                grant(key, f"geerbt von {source}")
    result["grants"] = grants
    return result


def compute(guild, user_id, config: dict) -> tuple[dict, object]:
    """Same contract as the former webserver.compute_perms: ({key: bool, ..., "is_admin": bool}, member)."""
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    info = permission_grants(guild, member, config)
    perms = {key: key in info["grants"] for key in ALL_KEYS}
    perms["is_admin"] = info["is_admin"]
    return perms, member


def explain(guild, user_id, config: dict) -> dict:
    """For "Wer darf was?" (F-01): every permission with its origins (empty list = not granted)."""
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    info = permission_grants(guild, member, config)
    return {"is_admin": info["is_admin"], "member": member is not None,
            "permissions": {key: info["grants"].get(key, []) for key in ALL_KEYS}}

"""Audit #160: target/actual comparison between the Discord server and the Pulse rights configuration.

Pulse grants dashboard rights *only* through configured team roles (``pulse_permissions``). This module reports where
the configuration and the live server disagree - read-only, it never changes a role or a setting:

* ``missing_roles``   configured roles that no longer exist on the server (the right silently grants nothing)
* ``stale_rights``    right entries for roles that are not team roles (they have no effect, but look like they do)
* ``unconfigured``    team roles without any right entry (members get the dashboard view only)
* ``admin_roles``     non-team roles with the Discord *Administrator* permission (full access outside Pulse's rules)
* ``admin_members``   members who are Discord administrators (they are Pulse administrators as well)
"""
from __future__ import annotations

SEVERITY = {"missing_roles": "bad", "stale_rights": "warn", "admin_roles": "bad", "unconfigured": "info", "admin_members": "info"}
_ORDER = {"bad": 0, "warn": 1, "info": 2}


def _ids(values) -> list[int]:
    if not isinstance(values, (list, tuple, set, dict)):
        return []
    return [int(v) for v in values if str(v).isdigit()]


def _name(guild, role_id: int) -> str:
    role = guild.get_role(role_id)
    return str(getattr(role, "name", None) or role_id)


def diff(guild, config: dict) -> list[dict]:
    config = config if isinstance(config, dict) else {}
    team = _ids(config.get("team_role_ids"))
    rights = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
    right_ids = _ids(rights)
    out: list[dict] = []

    def add(key, title, items):
        if items:
            out.append({"key": key, "severity": SEVERITY[key], "title": title, "items": sorted({str(i) for i in items})})
    add("missing_roles", "Konfigurierte Rollen existieren auf dem Server nicht mehr",
        [f"{rid} ({'Teamrolle' if rid in team else 'Rechte-Eintrag'})" for rid in dict.fromkeys(team + right_ids) if guild.get_role(rid) is None])
    add("stale_rights", "Rechte-Einträge für Rollen, die keine Teamrolle sind (wirkungslos)",
        [_name(guild, rid) for rid in right_ids if rid not in team and guild.get_role(rid) is not None])
    add("unconfigured", "Teamrollen ohne Rechte-Eintrag (nur Dashboard-Ansicht)",
        [_name(guild, rid) for rid in team if guild.get_role(rid) is not None and rid not in right_ids])
    default_id = getattr(getattr(guild, "default_role", None), "id", -1)
    admin_roles = [role.name for role in getattr(guild, "roles", []) or []
                   if getattr(getattr(role, "permissions", None), "administrator", False) and not getattr(role, "managed", False)
                   and role.id not in team and role.id != default_id]
    add("admin_roles", "Nicht-Team-Rollen mit Discord-Administratorrecht", admin_roles)
    admins = [m.display_name for m in getattr(guild, "members", []) or []
              if not getattr(m, "bot", False) and getattr(getattr(m, "guild_permissions", None), "administrator", False)
              and m.id != getattr(guild, "owner_id", None)]
    add("admin_members", "Mitglieder mit Discord-Administratorrecht (zusätzlich zum Serverinhaber)", admins)
    return sorted(out, key=lambda f: _ORDER[f["severity"]])

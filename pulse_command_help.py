"""BOT-04: visibility of slash commands and a help that is generated from the real command tree.

* VISIBILITY: setup and maintenance commands get Discord `default_permissions` at start-up, so ordinary
  members do not even see them. Server admins can still allow single roles under
  Server settings → Integrations → Pulse. Every command keeps its own server-side permission check.
* `/pulse-hilfe` is built from the commands that are actually registered (incl. groups such as /dienst),
  sorted into sections and filtered by the caller's Pulse rights and Discord permissions.
"""
from __future__ import annotations

import discord

MANAGE_GUILD = {
    "setup_activitycheck", "setup_backup", "setup_buero", "setup_flaggenquiz", "setup_liste", "setup_rp",
    "setup_teamliste", "ticketpanels_setup", "ticketsetup", "ticketpanel", "ticketrole", "ticketlog",
    "backup_erstellen", "social_add", "social_remove", "social_toggle", "social_test", "social_link", "social_list",
    "supportbot_test", "supportbot_status", "aimod_status", "test_activitycheck", "pulse-lock", "pulse-unlock",
    "pulse-permission", "pulse-diagnose", "pulsehealth",
}
MANAGE_ROLES = {"teamrollen_hinzufuegen", "teamrolle_entfernen", "teamliste_leeren", "update_teamliste", "buero_rolle_setzen"}
MANAGE_MESSAGES = {"messagedelete", "automod-fehlalarm", "automod-feedback"}

# Pulse right needed to *see* a command in the help (the command checks it again when used).
NEEDS = {
    "warn": "can_manage_team_warnings", "warns": "can_manage_team_warnings", "warn-remove": "can_manage_team_warnings",
    "automod-fehlalarm": "can_moderate_kicks", "automod-feedback": "can_moderate_kicks",
    "promote": "lead", "demote": "lead", "team-kick": "lead", "team-exit": "lead", "kick": "lead",
    "server-exit": "lead", "pulse-approve": "lead", "teamtask": "lead", "teamannounce": "lead", "team-ping": "lead",
    "activity-check": "lead",
}
SECTIONS = (
    ("⏱ Dienst & ich", ("dienst", "pulse", "profil", "pulse-home", "mytasks", "taskdone", "teamstatus", "anmeldung",
                        "abmeldung", "abmeldung_entfernen", "melden", "pulse-hilfe", "activity_status",
                        "ki_vergessen", "pulse-favoriten", "pulse-sprache", "datenschutz-anfrage", "datenschutz-status")),
    ("🧭 Team", ("memberinfo", "pulse-ticket", "pulse-suite", "ticketstats", "activity-check", "team-ping", "vorschlag", "feedback", "vorschlaege", "vorschlag-abstimmen", "spieler-meldung", "meine-meldungen")),
    ("⚠ Moderation", ("warn", "warns", "warn-remove", "messagedelete", "automod-fehlalarm", "automod-feedback")),
    ("🛡 Führung", ("teamtask", "teamannounce", "promote", "demote", "team-kick", "team-exit", "kick", "server-exit",
                   "pulse-approve")),
)
FALLBACK_SECTION = "⚙ Einrichtung & Wartung"


def required_permissions(name: str) -> discord.Permissions | None:
    if name in MANAGE_GUILD:
        return discord.Permissions(manage_guild=True)
    if name in MANAGE_ROLES:
        return discord.Permissions(manage_roles=True)
    if name in MANAGE_MESSAGES:
        return discord.Permissions(manage_messages=True)
    return None


def apply_visibility(tree) -> int:
    """Apply visibility permissions to root commands and moved subcommands.

    Group-level permissions are retained. Children inherit their group's restriction in
    Discord; child-specific checks and callback authorization remain untouched.
    """
    from pulse_command_groups import original_of

    changed = 0
    for command in tree.get_commands():
        wanted = required_permissions(command.name)
        if wanted is not None and getattr(command, "default_permissions", None) is None:
            command.default_permissions = wanted
            changed += 1

        for sub in getattr(command, "commands", None) or []:
            original = original_of(f"{command.name} {sub.name}") or sub.name
            child_wanted = required_permissions(original)
            if (
                child_wanted is not None
                and getattr(command, "default_permissions", None) is None
                and getattr(sub, "default_permissions", None) is None
            ):
                sub.default_permissions = child_wanted
                changed += 1
    return changed


def entries(tree) -> list[tuple[str, str, discord.Permissions | None, str, str]]:
    """Return every slash command path, effective default permissions and rights key.

    Moved /team and /setup children keep their original rights key, so help visibility
    remains identical even though the displayed command path has changed.
    """
    from pulse_command_groups import original_of

    out, aliased = [], set()
    for command in tree.get_commands():
        root_permissions = getattr(command, "default_permissions", None)
        for sub in getattr(command, "commands", None) or []:
            qualified = f"{command.name} {sub.name}"
            original = original_of(qualified)
            if original:
                aliased.add(original)
            child_permissions = getattr(sub, "default_permissions", None)
            effective_permissions = (
                child_permissions if child_permissions is not None else root_permissions
            )
            # Rights follow the leaf command. Ordinary nested commands remain grouped
            # under their parent command for the help section.
            rights_key = original or sub.name
            section_key = original or command.name
            out.append((
                qualified,
                getattr(sub, "description", "") or "",
                effective_permissions,
                rights_key,
                section_key,
            ))

    for command in tree.get_commands():
        if not getattr(command, "commands", None) and command.name not in aliased:
            out.append((
                command.name,
                getattr(command, "description", "") or "",
                getattr(command, "default_permissions", None),
                command.name,
                command.name,
            ))
    return out


def build_help(items, pulse_perms: dict, discord_perms: discord.Permissions | None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Sections with (/command, description) the caller may see."""
    lead = bool(pulse_perms.get("can_promote") or pulse_perms.get("is_admin"))
    visible = []
    for item in items:
        name, description, needed = item[:3]
        rights_key = item[3] if len(item) > 3 else name.split(" ")[0]
        section_key = item[4] if len(item) > 4 else rights_key
        need = NEEDS.get(rights_key)
        if need == "lead" and not lead:
            continue
        if need == "can_manage_team_warnings" and not pulse_perms.get(need):
            continue
        if need and need not in {"lead", "can_manage_team_warnings"} and not (pulse_perms.get(need) or lead):
            continue
        if needed is not None and not pulse_perms.get("is_admin") and not (discord_perms and discord_perms >= needed):
            continue
        visible.append((name, description, section_key))
    sections, used = [], set()
    for title, names in SECTIONS:
        rows = [(f"/{n}", d) for n, d, key in visible if key in names or n.split(" ")[0] in names]
        used.update(n for n, d, key in visible if key in names or n.split(" ")[0] in names)
        if rows:
            sections.append((title, sorted(rows)))
    rest = sorted((f"/{n}", d) for n, d, _ in visible if n not in used)
    if rest:
        sections.append((FALLBACK_SECTION, rest))
    return sections

"""BOT-04 (E-11): command groups with the old names kept as aliases.

`/team …` and `/setup …` are built at start-up from the commands that already exist: same callback, same
parameters (incl. descriptions, choices and checks), same cog. Nothing is duplicated by hand, so the group
version and the old name can never drift apart. The old top-level names stay registered (aliases), so nobody
has to relearn anything; /pulse-hilfe shows the group names. /dienst, /ticket and /mod are groups already.
"""
from __future__ import annotations

import discord
from discord import app_commands

ALIASES = {
    "team": ("Teamaktionen", {
        "warn": "warn", "warns": "warns", "warn-entfernen": "warn-remove", "befoerdern": "promote",
        "degradieren": "demote", "kick": "team-kick", "austritt": "team-exit", "ping": "team-ping",
    }),
    "setup": ("Einrichtung von Panels und Systemen", {
        "abmeldeliste": "setup_liste", "activitycheck": "setup_activitycheck", "backup": "setup_backup",
        "buero": "setup_buero", "flaggenquiz": "setup_flaggenquiz", "rp": "setup_rp", "teamliste": "setup_teamliste",
        "ticketpanels": "ticketpanels_setup", "tickets": "ticketsetup",
    }),
}
GROUP_PERMISSIONS = {"setup": discord.Permissions(manage_guild=True)}


def original_of(qualified_name: str) -> str | None:
    """'team kick' → 'team-kick' (None if it is not an alias)."""
    group, _, sub = qualified_name.partition(" ")
    return ALIASES.get(group, ("", {}))[1].get(sub)


def add_alias_groups(tree) -> list[str]:
    """Adds the groups to the tree (before sync). Missing originals are skipped and reported."""
    missing = []
    for group_name, (description, mapping) in ALIASES.items():
        if tree.get_command(group_name) is not None:
            continue  # already added (e.g. a second call) or a name clash – never replace an existing command
        group = app_commands.Group(name=group_name, description=description,
                                   default_permissions=GROUP_PERMISSIONS.get(group_name))
        for sub_name, original_name in mapping.items():
            original = tree.get_command(original_name)
            if not isinstance(original, app_commands.Command):
                missing.append(original_name)
                continue
            alias = app_commands.Command(name=sub_name, description=original.description, callback=original.callback, parent=group)
            alias.binding = original.binding
            for check in getattr(original, "checks", []):
                if check not in alias.checks:
                    alias.add_check(check)
            group.add_command(alias)
        if group.commands:
            tree.add_command(group)
    return missing

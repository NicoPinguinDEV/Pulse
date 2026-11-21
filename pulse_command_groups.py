"""BOT-04 (E-11): move selected top-level commands into compact Discord command groups.

The old implementation copied commands into /team and /setup but kept every original
root command registered as well. That duplicated callbacks and pushed the global command
tree over Discord's 100 chat-input-command limit.

We now move the original Command objects. This preserves callbacks, cog bindings, parameters,
descriptions, choices, autocomplete handlers, checks, extras and other command metadata.
The old top-level paths become group/subcommand paths; no command implementation is copied.
"""
from __future__ import annotations

import discord
from discord import app_commands


ALIASES = {
    "team": ("Teamaktionen", {
        "warn": "warn",
        "warns": "warns",
        "warn-entfernen": "warn-remove",
        "befoerdern": "promote",
        "degradieren": "demote",
        "kick": "team-kick",
        "austritt": "team-exit",
        "ping": "team-ping",
    }),
    "setup": ("Einrichtung von Panels und Systemen", {
        "abmeldeliste": "setup_liste",
        "activitycheck": "setup_activitycheck",
        "backup": "setup_backup",
        "buero": "setup_buero",
        "flaggenquiz": "setup_flaggenquiz",
        "rp": "setup_rp",
        "teamliste": "setup_teamliste",
        "ticketpanels": "ticketpanels_setup",
        "tickets": "ticketsetup",
    }),
}
GROUP_PERMISSIONS = {"setup": discord.Permissions(manage_guild=True)}


def original_of(qualified_name: str) -> str | None:
    """'team kick' -> 'team-kick' (None if it is not a moved command)."""
    group, _, sub = str(qualified_name or "").partition(" ")
    return ALIASES.get(group, ("", {}))[1].get(sub)


def canonical_command_name(value: str) -> str:
    """Translate stored legacy root names to their new slash-command path."""
    name = str(value or "").strip()
    if name.startswith("/"):
        name = name[1:]
    if " " in name:
        return name
    for group_name, (_description, mapping) in ALIASES.items():
        for sub_name, original_name in mapping.items():
            if name == original_name:
                return f"{group_name} {sub_name}"
    return name


def add_alias_groups(tree) -> list[str]:
    """Move existing commands into /team and /setup, returning unavailable targets.

    This function is idempotent. It never replaces a pre-existing non-group command,
    never fabricates a handler, and restores a removed root command if group insertion
    unexpectedly fails.
    """
    missing: list[str] = []

    for group_name, (description, mapping) in ALIASES.items():
        existing = tree.get_command(group_name)
        if existing is not None and not isinstance(existing, app_commands.Group):
            missing.append(f"{group_name} (Gruppenname bereits belegt)")
            continue

        group = existing or app_commands.Group(
            name=group_name,
            description=description,
            default_permissions=GROUP_PERMISSIONS.get(group_name),
        )

        for sub_name, original_name in mapping.items():
            if group.get_command(sub_name) is not None:
                continue

            original = tree.get_command(original_name)
            if not isinstance(original, app_commands.Command):
                missing.append(original_name)
                continue

            # Remove the exact registered object before changing its qualified name.
            removed = tree.remove_command(original_name)
            if removed is not original:
                if removed is not None:
                    tree.add_command(removed)
                missing.append(original_name)
                continue

            original_name_before_move = original.name
            original.name = sub_name
            # Keep automatic command-name localization aligned with the new qualified path.
            if hasattr(original, "_locale_name"):
                original._locale_name = app_commands.locale_str(sub_name)
            try:
                group.add_command(original)
            except Exception:
                # Fail closed without losing the command if Discord.py rejects the move.
                original.name = original_name_before_move
                tree.add_command(original)
                raise

        if existing is None and group.commands:
            tree.add_command(group)

    return missing

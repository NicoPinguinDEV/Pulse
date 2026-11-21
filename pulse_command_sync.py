"""P-09: safely validate and sync Pulse's global Discord application commands.

Discord allows at most 100 global chat-input commands, with separate limits for context
menus and subcommands inside each group. A command-tree hash avoids unnecessary syncs,
but a hash must never bypass the capacity check.
"""
from __future__ import annotations

import hashlib
import json
import os

import pulse_db


SETTING_KEY = "command_tree_hash"
MAX_GLOBAL_CHAT_INPUT_COMMANDS = 100
MAX_GLOBAL_USER_CONTEXT_MENUS = 15
MAX_GLOBAL_MESSAGE_CONTEXT_MENUS = 15
MAX_GROUP_SUBCOMMANDS = 25


class CommandTreeLimitError(ValueError):
    """Raised before sync when the global application-command payload exceeds Discord limits."""


def _command_type(command) -> int:
    value = getattr(command, "type", 1)
    return int(getattr(value, "value", value))


def command_inventory(tree) -> dict:
    """Return counts of global root commands, nested options and context menus."""
    roots = list(tree.get_commands())
    chat_input = [command for command in roots if _command_type(command) == 1]
    user_context = sum(_command_type(command) == 2 for command in roots)
    message_context = sum(_command_type(command) == 3 for command in roots)
    groups = []
    nested = 0
    for command in chat_input:
        children = list(getattr(command, "commands", None) or [])
        if children:
            groups.append({"name": command.name, "subcommands": len(children)})
            nested += len(children)
    return {
        "root_commands": len(roots),
        "chat_input_commands": len(chat_input),
        "user_context_menus": user_context,
        "message_context_menus": message_context,
        "nested_subcommands": nested,
        "groups": groups,
    }


def validate_tree_capacity(tree) -> dict:
    """Validate Discord's per-type global limits before making any HTTP request."""
    inventory = command_inventory(tree)
    problems = []

    if inventory["chat_input_commands"] > MAX_GLOBAL_CHAT_INPUT_COMMANDS:
        roots = [
            str(command.name)
            for command in tree.get_commands()
            if _command_type(command) == 1
        ]
        problems.append(
            f"{inventory['chat_input_commands']}/{MAX_GLOBAL_CHAT_INPUT_COMMANDS} globale "
            f"Chat-Input-Commands; Root-Befehle: {', '.join(roots)}"
        )

    if inventory["user_context_menus"] > MAX_GLOBAL_USER_CONTEXT_MENUS:
        problems.append(
            f"{inventory['user_context_menus']}/{MAX_GLOBAL_USER_CONTEXT_MENUS} globale User-Context-Menüs"
        )

    if inventory["message_context_menus"] > MAX_GLOBAL_MESSAGE_CONTEXT_MENUS:
        problems.append(
            f"{inventory['message_context_menus']}/{MAX_GLOBAL_MESSAGE_CONTEXT_MENUS} globale Message-Context-Menüs"
        )

    for group in inventory["groups"]:
        if group["subcommands"] > MAX_GROUP_SUBCOMMANDS:
            problems.append(
                f"/{group['name']} hat {group['subcommands']}/{MAX_GROUP_SUBCOMMANDS} Subcommands"
            )

    if problems:
        raise CommandTreeLimitError(
            "Discord-Command-Limit verhindert die Synchronisierung: " + "; ".join(problems)
        )

    return inventory


def tree_hash(tree) -> str:
    payload = []
    for command in tree.get_commands():
        try:
            payload.append(command.to_dict(tree))
        except TypeError:  # older discord.py signature
            payload.append(command.to_dict())
    payload.sort(key=lambda c: (str(c.get("type", 1)), c.get("name", "")))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sync_needed(tree) -> tuple[bool, str]:
    current = tree_hash(tree)
    forced = str(os.getenv("PULSE_FORCE_COMMAND_SYNC", "")).strip().lower() in {"1", "true", "yes", "on"}
    return forced or pulse_db.get_setting(SETTING_KEY, "") != current, current


async def sync_commands(tree, *, force: bool = False) -> dict:
    """Validate, then sync only when changed. Never records a failed sync as complete."""
    inventory = validate_tree_capacity(tree)
    needed, current = sync_needed(tree)
    if not (needed or force):
        return {
            "synced": False,
            "count": inventory["chat_input_commands"],
            "reason": "unverändert",
            **inventory,
        }

    synced = await tree.sync()
    pulse_db.set_setting(SETTING_KEY, current)
    return {
        "synced": True,
        "count": len(synced),
        "reason": "manuell" if force else "geändert",
        **inventory,
    }

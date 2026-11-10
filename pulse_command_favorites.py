"""Personal slash-command favorites, scoped to one Discord user."""
from __future__ import annotations

import re

SETTING_PREFIX = "pulse_command_favorites"
MAX_FAVORITES = 8
_COMMAND_NAME_RE = re.compile(r"^[\w-]+(?: [\w-]+)*$", re.UNICODE)


def normalize_command_name(value) -> str:
    name = str(value or "").strip()
    if name.startswith("/"):
        name = name[1:]
    if len(name) > 100 or not _COMMAND_NAME_RE.fullmatch(name):
        return ""
    return name


def normalize_favorites(value) -> list[str]:
    """Return a unique, bounded list of syntactically valid slash-command names."""
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for raw in value:
        name = normalize_command_name(raw)
        if name and name not in result:
            result.append(name)
            if len(result) >= MAX_FAVORITES:
                break
    return result


def setting_key(user_id) -> str:
    return f"{SETTING_PREFIX}:{str(user_id)}"


def add_favorite(value, command_name, allowed_names) -> list[str]:
    favorites = normalize_favorites(value)
    name = normalize_command_name(command_name)
    allowed = {normalize_command_name(item) for item in (allowed_names or ())}
    allowed.discard("")
    if not name or name not in allowed:
        raise ValueError("Dieser Befehl ist für dich nicht freigegeben oder existiert nicht.")
    if name in favorites:
        raise ValueError("Dieser Befehl ist bereits in deinen Favoriten.")
    if len(favorites) >= MAX_FAVORITES:
        raise ValueError(f"Du kannst höchstens {MAX_FAVORITES} Befehle speichern.")
    return [*favorites, name]


def remove_favorite(value, command_name) -> list[str]:
    favorites = normalize_favorites(value)
    name = normalize_command_name(command_name)
    if not name or name not in favorites:
        raise ValueError("Dieser Befehl ist nicht in deinen Favoriten gespeichert.")
    return [item for item in favorites if item != name]


def permitted_command_names(tree, pulse_perms: dict, discord_perms) -> list[str]:
    """Reuse the same live command-tree and rights filter as /pulse-hilfe."""
    import pulse_command_help

    sections = pulse_command_help.build_help(
        pulse_command_help.entries(tree),
        pulse_perms or {},
        discord_perms,
    )
    names = []
    for _section, rows in sections:
        for command, _description in rows:
            name = normalize_command_name(command)
            if name and name != "pulse-favoriten" and name not in names:
                names.append(name)
    return names


def command_mention(tree, command_name: str) -> str:
    """Return a clickable Discord command mention when the synced command ID is known."""
    name = normalize_command_name(command_name)
    if not name:
        return ""
    parts = name.split()
    root_command = tree.get_command(parts[0])
    command = root_command
    for part in parts[1:]:
        getter = getattr(command, "get_command", None)
        command = getter(part) if getter else None
        if command is None:
            return f"`/{name}`"
    command_id = getattr(root_command if len(parts) > 1 else command, "id", None)
    if command_id:
        return f"</{name}:{int(command_id)}>"
    return f"`/{name}`"

"""Pure helpers for the administration panel inventory.

These helpers never call Discord or mutate settings; reachability is supplied by the
caller so the UI can distinguish unknown, missing and verified publication state.
"""
from __future__ import annotations


def panel_status(channel_exists: bool, message_id, message_reachable: bool | None) -> str:
    """Describe stored publication state without pretending an unchecked message is healthy."""
    if not channel_exists:
        return "Kanal nicht gefunden"
    if not str(message_id or "").isdigit() or int(message_id or 0) <= 0:
        return "Noch nicht veröffentlicht"
    if message_reachable is True:
        return "Veröffentlicht"
    if message_reachable is False:
        return "Discord-Nachricht fehlt"
    return "Status nicht prüfbar"


def discord_message_url(guild_id, channel_id, message_id) -> str:
    """Return a Discord deep link only when all IDs are positive decimal values."""
    values = (guild_id, channel_id, message_id)
    if not all(str(value or "").isdigit() and int(value or 0) > 0 for value in values):
        return ""
    return f"https://discord.com/channels/{int(guild_id)}/{int(channel_id)}/{int(message_id)}"

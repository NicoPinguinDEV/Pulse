"""K-08: one place that answers every failed slash command.

discord.py never dispatches an `on_app_command_error` listener – errors of app commands go to
`CommandTree.on_error`. main.py installs `PulseCommandTree`, which calls `handle_app_command_error`.
Users get a short ephemeral explanation; unexpected errors get an error ID that is also logged.
"""
from __future__ import annotations

import secrets
import traceback

import discord
from discord import app_commands


def _names(perms) -> str:
    return ", ".join(str(p).replace("_", " ") for p in perms) or "unbekannt"


def describe_error(error: Exception) -> tuple[str, str | None]:
    """(message for the user, error id or None). Expected errors get no id and no traceback."""
    original = getattr(error, "original", None)
    if isinstance(error, app_commands.MissingPermissions):
        return f"❌ Dafür fehlen dir Discord-Rechte: **{_names(error.missing_permissions)}**.", None
    if isinstance(error, app_commands.BotMissingPermissions):
        return f"❌ Pulse fehlen auf diesem Server Rechte: **{_names(error.missing_permissions)}**. Bitte ein Admin prüfen lassen.", None
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingAnyRole)):
        return "❌ Dir fehlt die nötige Rolle für diesen Befehl.", None
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"⏳ Bitte warte noch **{max(1, round(error.retry_after))} s**, bevor du den Befehl erneut nutzt.", None
    if isinstance(error, app_commands.NoPrivateMessage):
        return "❌ Dieser Befehl funktioniert nur auf dem Server, nicht in Direktnachrichten.", None
    if isinstance(error, app_commands.TransformerError):
        return f"❌ Ungültige Eingabe: **{str(getattr(error, 'value', ''))[:80]}** passt nicht zum erwarteten Wert.", None
    if isinstance(error, app_commands.CheckFailure):
        return "❌ Du darfst diesen Befehl hier nicht verwenden.", None
    if isinstance(original, discord.Forbidden):
        error_id = secrets.token_hex(3).upper()
        return (f"❌ Discord hat Pulse die Aktion verweigert (Rechte/Rollenreihenfolge). Fehler-ID **{error_id}**.", error_id)
    error_id = secrets.token_hex(3).upper()
    return (f"❌ Dabei ist ein unerwarteter Fehler aufgetreten. Fehler-ID **{error_id}** – das Team findet sie im Log.", error_id)


async def handle_app_command_error(interaction: discord.Interaction, error: Exception) -> str:
    """Answer the user (response or followup) and log unexpected errors. Never raises."""
    message, error_id = describe_error(error)
    command = getattr(getattr(interaction, "command", None), "qualified_name", "?")
    if error_id:
        cause = getattr(error, "original", error)
        print(f"❌ Slash-Befehl /{command} fehlgeschlagen [Fehler-ID {error_id}]: {type(cause).__name__}: {cause}")
        import pulse_logging
        pulse_logging.report_error(f"slash./{command}", f"{type(cause).__name__}: {cause}", error_id)
        traceback.print_exception(type(cause), cause, cause.__traceback__)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass
    return message


class PulseCommandTree(app_commands.CommandTree):
    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await handle_app_command_error(interaction, error)

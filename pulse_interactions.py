"""BOT-01: shared rules for buttons and selects (slash commands: pulse_command_errors, K-08).

`PulseView` is a drop-in base for discord.ui.View:
* an error in a callback gives the clicker a short ephemeral message with an error ID (and the log the
  same ID) instead of Discord's generic "Interaktion fehlgeschlagen";
* the same person clicking the same component again within CLICK_COOLDOWN seconds is told to wait
  instead of running the action twice;
* on timeout all components are disabled (when the view knows its message).
Views that define their own interaction_check/on_timeout/on_error keep them (normal overriding).
"""
from __future__ import annotations

import secrets
import time
import traceback

import discord

CLICK_COOLDOWN = 1.5  # seconds per person and component
# Bot-wide default (main.py): people may be mentioned, @everyone/@here and roles only where a message
# passes its own allowed_mentions – a pasted "@everyone" or role ID in user text then pings nobody.
DEFAULT_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True)
_MAX_TRACKED = 2000   # bounded memory for the click map
_last_click: dict[tuple[int, str], float] = {}


def too_fast(user_id: int, component_id: str, now: float | None = None) -> bool:
    """True when this person clicked this component less than CLICK_COOLDOWN seconds ago."""
    now = time.monotonic() if now is None else now
    key = (int(user_id), str(component_id))
    last = _last_click.get(key)
    _last_click[key] = now
    if len(_last_click) > _MAX_TRACKED:
        for old in sorted(_last_click, key=_last_click.get)[: _MAX_TRACKED // 2]:
            _last_click.pop(old, None)
    return last is not None and now - last < CLICK_COOLDOWN


async def reply_ephemeral(interaction: discord.Interaction, content: str) -> None:
    """Answers whether or not the interaction was already acknowledged; never raises."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except (discord.HTTPException, discord.InteractionResponded):
        pass


async def defer_ephemeral(interaction: discord.Interaction, thinking: bool = True) -> None:
    """Acknowledge at once when the work may take longer than ~1 s (Discord allows 3 s)."""
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True, thinking=thinking)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class PulseView(discord.ui.View):
    message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        component = str((interaction.data or {}).get("custom_id") or "")
        if too_fast(interaction.user.id, component):
            await reply_ephemeral(interaction, "⏳ Schon unterwegs – bitte nicht doppelt klicken.")
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        if isinstance(error, discord.NotFound) and getattr(error, "code", 0) == 10062:
            # Not acknowledged within Discord's 3 s (usually a blocked event loop): a reply is impossible.
            print(f"⚠️ Interaktion abgelaufen (10062) {type(self).__name__}/{getattr(item, 'custom_id', '?')} – "
                  "Antwort kam nach Discords 3-s-Frist; siehe Event-Loop-Warnungen.")
            return
        error_id = secrets.token_hex(3).upper()
        print(f"❌ Pulse Interaktion fehlgeschlagen [Fehler-ID {error_id}] {type(self).__name__}/"
              f"{getattr(item, 'custom_id', '?')}: {type(error).__name__}: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)
        text = (f"❌ Discord hat Pulse die Aktion verweigert (Rechte/Rollenreihenfolge). Fehler-ID **{error_id}**."
                if isinstance(error, discord.Forbidden) else
                f"❌ Das hat nicht geklappt. Fehler-ID **{error_id}** – das Team findet sie im Log.")
        await reply_ephemeral(interaction, text)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

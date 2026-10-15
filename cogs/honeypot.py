"""Classic honeypot channel: warn visibly, then ban anyone who posts there."""
from __future__ import annotations

import discord
from discord.ext import commands

HONEYPOT_CHANNEL_ID = 1560397792751657053
HONEYPOT_WARNING_MARKER = "PULSE_HONEYPOT_WARNING_V1"


def build_honeypot_embed() -> discord.Embed:
    """Warning shown in the channel before anyone can trigger the honeypot."""
    embed = discord.Embed(
        title="🚨 HONEYPOT – HIER AUF KEINEN FALL SCHREIBEN!",
        description=(
            "**Dieser Kanal ist ausschließlich eine Sicherheitsfalle.**\n\n"
            "⛔ **Schreibe hier keine Nachricht – auch nicht zum Testen.**\n"
            "Jede Nachricht in diesem Kanal löst automatisch einen **permanenten Server-Ban** aus.\n\n"
            "Wenn du diesen Kanal versehentlich geöffnet hast, verlasse ihn einfach wieder. "
            "Antworte nicht und sende keine Testnachricht."
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(
        name="Wichtig",
        value="**Nichts senden. Keine Texte, Emojis, Befehle oder Testnachrichten.**",
        inline=False,
    )
    embed.set_footer(text=HONEYPOT_WARNING_MARKER)
    return embed


class Honeypot(commands.Cog):
    """Bans message authors in the configured honeypot channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_honeypot_channel(self):
        channel = self.bot.get_channel(HONEYPOT_CHANNEL_ID)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(HONEYPOT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(
                "🚨 Honeypot: Kanal "
                f"{HONEYPOT_CHANNEL_ID} konnte nicht geladen werden: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensure the warning embed exists without posting a duplicate on reconnect."""
        channel = await self._get_honeypot_channel()
        if channel is None or not hasattr(channel, "send") or not hasattr(channel, "history"):
            print(f"🚨 Honeypot: {HONEYPOT_CHANNEL_ID} ist kein erreichbarer Textkanal.")
            return

        try:
            async for message in channel.history(limit=50):
                if (
                    self.bot.user
                    and message.author.id == self.bot.user.id
                    and any(embed.footer.text == HONEYPOT_WARNING_MARKER for embed in message.embeds)
                ):
                    return
        except discord.Forbidden:
            # Still post the warning if sending is allowed; the missing history
            # permission will be visible to admins in the hosting logs.
            print(
                "⚠️ Honeypot: Kein Zugriff auf den Nachrichtenverlauf. "
                "Zum Verhindern doppelter Warnungen 'Nachrichtenverlauf anzeigen' erlauben."
            )
        except discord.HTTPException as exc:
            print(f"⚠️ Honeypot: Warn-Embed-Prüfung fehlgeschlagen: {exc}")

        try:
            await channel.send(embed=build_honeypot_embed())
            print(f"🚨 Honeypot-Warnung wurde in Kanal {HONEYPOT_CHANNEL_ID} sichergestellt.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(
                "❌ Honeypot: Warn-Embed konnte nicht gesendet werden. "
                "Benötigt werden 'Kanal ansehen', 'Nachrichten senden' und 'Links einbetten'. "
                f"Fehler: {type(exc).__name__}: {exc}"
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.channel.id != HONEYPOT_CHANNEL_ID:
            return

        # Pulse's own warning embed must never trigger its own honeypot.
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        author = message.author
        guild = message.guild
        reason = (
            f"Honeypot ausgelöst: Nachricht im verbotenen Kanal "
            f"{HONEYPOT_CHANNEL_ID} (Nachrichten-ID {message.id})."
        )
        print(
            f"🚨 Honeypot ausgelöst: {getattr(author, 'name', 'Unbekannt')} "
            f"(ID {author.id}) schrieb in Kanal {HONEYPOT_CHANNEL_ID}."
        )

        # Remove the trigger message when possible, but never skip the ban just
        # because message deletion is blocked by channel permissions.
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            print("⚠️ Honeypot: Nachricht konnte nicht gelöscht werden; Ban wird trotzdem versucht.")
        except discord.HTTPException as exc:
            print(f"⚠️ Honeypot: Nachrichtenlöschung fehlgeschlagen: {exc}")

        # Webhook messages are not member accounts and cannot be banned with
        # Guild.ban. Delete their message and explicitly report the limitation.
        if getattr(message, "webhook_id", None):
            print(
                f"⚠️ Honeypot: Nachricht stammte von Webhook {message.webhook_id}; "
                "die Nachricht wurde entfernt, ein Nutzer-Ban ist für Webhooks nicht möglich."
            )
            return

        try:
            await guild.ban(author, reason=reason)
            print(
                f"🔨 Honeypot: {getattr(author, 'name', 'Unbekannt')} "
                f"(ID {author.id}) wurde aus {guild.name} gebannt."
            )
        except discord.Forbidden:
            print(
                f"❌ Honeypot: Ban von Nutzer {author.id} abgelehnt. "
                "Prüfe 'Mitglieder bannen' und ob die Bot-Rolle höher als die Zielrolle steht."
            )
        except discord.NotFound:
            print(f"⚠️ Honeypot: Nutzer {author.id} war bereits nicht mehr bannbar/auf dem Server.")
        except discord.HTTPException as exc:
            print(f"❌ Honeypot: Ban von Nutzer {author.id} fehlgeschlagen: {exc}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Honeypot(bot))

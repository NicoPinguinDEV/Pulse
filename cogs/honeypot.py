"""Classic honeypot channel: warn visibly, then ban anyone who posts there."""
from __future__ import annotations

import discord
from pulse_embed_design import color as _pulse_color, is_modern
from discord.ext import commands

from pulse_discord_style import card_info, edit_styled, is_v2, same_content, send_styled

HONEYPOT_CHANNEL_ID = 1560397792751657053
# Former (visible) footer marker – still recognised so the old warning is replaced, never duplicated.
HONEYPOT_WARNING_MARKER = "PULSE_HONEYPOT_WARNING_V1"
HONEYPOT_FOOTER = "🛡️ Pulse Sicherheitssystem · Honeypot"
HONEYPOT_FOOTERS = {HONEYPOT_WARNING_MARKER, HONEYPOT_FOOTER}


def build_honeypot_embed() -> discord.Embed:
    """Warning shown in the channel before anyone can trigger the honeypot."""
    embed = discord.Embed(
        title="🚨 HONEYPOT – HIER AUF KEINEN FALL SCHREIBEN!",
        description=(
            "**Dieser Kanal ist ausschließlich eine Sicherheitsfalle gegen Spam-Bots und gehackte Accounts.**\n\n"
            "⛔ **Schreibe hier keine Nachricht – auch nicht zum Testen.**\n"
            "Jede Nachricht in diesem Kanal löst automatisch einen **permanenten Server-Ban** aus."
        ),
        color=_pulse_color("danger"),
    )
    embed.add_field(
        name="🔨 Was passiert, wenn du hier schreibst?",
        value="Die Nachricht wird gelöscht und dein Account **sofort und dauerhaft gebannt** – ohne Vorwarnung.",
        inline=False,
    )
    embed.add_field(
        name="✅ Was solltest du tun?",
        value="Nichts. Verlasse den Kanal einfach wieder – **keine Texte, Emojis, Befehle oder Testnachrichten.**",
        inline=False,
    )
    embed.set_footer(text=HONEYPOT_FOOTER)
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
        from pulse_ready import first_ready
        if not first_ready(self.bot, "honeypot-card"):  # P-08: one history scan per process
            return
        channel = await self._get_honeypot_channel()
        if channel is None or not hasattr(channel, "send") or not hasattr(channel, "history"):
            print(f"🚨 Honeypot: {HONEYPOT_CHANNEL_ID} ist kein erreichbarer Textkanal.")
            return

        warnings = []
        try:
            async for message in channel.history(limit=50):
                info = card_info(message)
                if (
                    self.bot.user
                    and message.author.id == self.bot.user.id
                    and info
                    and info["footer"] in HONEYPOT_FOOTERS
                ):
                    warnings.append(message)
        except discord.Forbidden:
            # Still post the warning if sending is allowed; the missing history
            # permission will be visible to admins in the hosting logs.
            print(
                "⚠️ Honeypot: Kein Zugriff auf den Nachrichtenverlauf. "
                "Zum Verhindern doppelter Warnungen 'Nachrichtenverlauf anzeigen' erlauben."
            )
        except discord.HTTPException as exc:
            print(f"⚠️ Honeypot: Warn-Embed-Prüfung fehlgeschlagen: {exc}")

        embed = build_honeypot_embed()
        keep = warnings[0] if warnings else None  # history is newest first
        for duplicate in warnings[1:]:
            try:
                await duplicate.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        if keep is not None and is_v2(keep) != is_modern():
            # Embed and container cannot be converted: replace the warning exactly once.
            try:
                await keep.delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Honeypot: alte Warnung konnte nicht ersetzt werden: {exc}")
                return
            keep = None
        if keep is not None:
            if not same_content(keep, embed):
                try:
                    await edit_styled(keep, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"⚠️ Honeypot: Warnung konnte nicht aktualisiert werden: {exc}")
            return

        try:
            await send_styled(channel, embed=embed, allowed_mentions=discord.AllowedMentions.none())
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

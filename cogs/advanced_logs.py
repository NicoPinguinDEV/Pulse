import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
from discord.ext import commands

import pulse_db

# K-12: the channel is chosen in Verwaltung → Discord-Auftritt (setting in pulse.db) instead of a hard-coded 0.
LOG_SETTING_KEY = "moderation_log_channel_id"
_HINT_SHOWN = False


def configured_log_channel_id() -> int:
    try:
        raw = str(pulse_db.get_setting(LOG_SETTING_KEY, "") or "").strip()
    except Exception:
        return 0
    return int(raw) if raw.isdigit() else 0


def log_channel_status(guild) -> str:
    """Short status for the system page; empty string when everything is fine."""
    channel_id = configured_log_channel_id()
    if not channel_id:
        return "Moderations-Log nicht eingerichtet (Verwaltung → Discord-Auftritt)"
    if guild is not None and guild.get_channel(channel_id) is None:
        return f"Moderations-Log-Kanal {channel_id} nicht gefunden"
    return ""


class AdvancedLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def log_channel_id(self) -> int:
        return configured_log_channel_id()

    async def get_log_channel(self, guild):
        global _HINT_SHOWN
        if guild is None:
            return None
        channel_id = configured_log_channel_id()
        if not channel_id:
            if not _HINT_SHOWN:
                _HINT_SHOWN = True
                print("ℹ️ Moderations-Log: kein Log-Kanal eingerichtet – Bearbeitungen, Voice- und Rollenänderungen "
                      "werden nicht protokolliert (Verwaltung → Discord-Auftritt).")
            return None
        return guild.get_channel(channel_id)

    async def _send(self, channel, embed):
        try:
            await send_styled(channel, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Moderations-Log: Senden fehlgeschlagen ({type(exc).__name__}).")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.author.bot or before.content == after.content:
            return
        channel = await self.get_log_channel(before.guild)
        if channel:
            embed = discord.Embed(title="Nachricht bearbeitet", color=_pulse_color("warning"), timestamp=discord.utils.utcnow())
            embed.add_field(name="Autor", value=before.author.mention, inline=False)
            embed.add_field(name="Vorher", value=before.content[:1024] or "Kein Text", inline=False)
            embed.add_field(name="Nachher", value=after.content[:1024] or "Kein Text", inline=False)
            await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        channel = await self.get_log_channel(member.guild)
        if not channel:
            return

        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title=" Sprachkanal betreten", description=f"{member.mention} hat **{after.channel.name}** betreten.", color=_pulse_color("success"), timestamp=discord.utils.utcnow())
            await self._send(channel, embed)
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title=" Sprachkanal verlassen", description=f"{member.mention} hat **{before.channel.name}** verlassen.", color=_pulse_color("danger"), timestamp=discord.utils.utcnow())
            await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            channel = await self.get_log_channel(before.guild)
            if channel:
                added = [r.mention for r in after.roles if r not in before.roles]
                removed = [r.mention for r in before.roles if r not in after.roles]
                embed = discord.Embed(title="Rollen geändert", color=_pulse_color("info"), timestamp=discord.utils.utcnow())
                embed.add_field(name="Mitglied", value=after.mention, inline=False)
                if added:
                    embed.add_field(name="Hinzugefügt", value=", ".join(added), inline=False)
                if removed:
                    embed.add_field(name="Entfernt", value=", ".join(removed), inline=False)
                await self._send(channel, embed)

async def setup(bot):
    await bot.add_cog(AdvancedLogs(bot))

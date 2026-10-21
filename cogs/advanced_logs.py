import asyncio
import time

import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
from discord.ext import commands

import pulse_db
import pulse_logcfg

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


def account_age(member) -> str:
    created = getattr(member, "created_at", None)
    if created is None:
        return "unbekannt"
    days = (discord.utils.utcnow() - created).days
    return f"{days} Tage" + (" ⚠️ neu" if days < 7 else "")


class AdvancedLogs(commands.Cog):
    """BOT-02: messages, members, moderation (with actor from the audit log), roles, channels and voice.
    Categories can go to their own channels (pulse_logcfg); entries are sent in batches."""

    def __init__(self, bot):
        self.bot = bot
        self.buffer = pulse_logcfg.LogBuffer()
        self.voice_throttle = pulse_logcfg.Throttle(pulse_logcfg.VOICE_THROTTLE)

    @property
    def log_channel_id(self) -> int:
        return configured_log_channel_id()

    async def get_log_channel(self, guild, category: str = "messages"):
        global _HINT_SHOWN
        if guild is None:
            return None
        channel_id = pulse_logcfg.route(category, pulse_logcfg.load(), configured_log_channel_id())
        if not channel_id:
            if not _HINT_SHOWN and not configured_log_channel_id():
                _HINT_SHOWN = True
                print("ℹ️ Moderations-Log: kein Log-Kanal eingerichtet – Bearbeitungen, Voice- und Rollenänderungen "
                      "werden nicht protokolliert (Verwaltung → Discord-Auftritt).")
            return None
        return guild.get_channel(channel_id)

    async def _send(self, channel, embed):
        """Queue the entry; the first one schedules a single flush a few seconds later (no permanent loop)."""
        if self.buffer.add(channel.id, embed):
            asyncio.get_running_loop().call_later(
                pulse_logcfg.BATCH_SECONDS, lambda: asyncio.ensure_future(self._flush(channel)))

    async def _flush(self, channel):
        while self.buffer.pending(channel.id):
            batch = self.buffer.take(channel.id)
            try:
                if len(batch) == 1:
                    await send_styled(channel, embed=batch[0])
                else:
                    await channel.send(embeds=batch, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Moderations-Log: Senden fehlgeschlagen ({type(exc).__name__}).")
                return

    async def log(self, guild, category: str, embed: discord.Embed):
        channel = await self.get_log_channel(guild, category)
        if channel:
            embed.set_footer(text=pulse_logcfg.CATEGORIES.get(category, category))
            await self._send(channel, embed)

    @staticmethod
    def _skip(member=None, channel=None) -> bool:
        cfg = pulse_logcfg.load()
        return pulse_logcfg.ignored(cfg, channel_id=getattr(channel, "id", None),
                                    role_ids=[r.id for r in getattr(member, "roles", []) or []],
                                    is_bot=bool(getattr(member, "bot", False)))

    # ------------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild is None or before.author.bot or before.content == after.content or self._skip(before.author, getattr(before, "channel", None)):
            return
        embed = discord.Embed(title="Nachricht bearbeitet", color=_pulse_color("warning"), timestamp=discord.utils.utcnow())
        embed.add_field(name="Autor", value=before.author.mention, inline=False)
        if getattr(before, "channel", None) is not None:
            embed.add_field(name="Kanal", value=before.channel.mention, inline=False)
        embed.add_field(name="Vorher", value=before.content[:1024] or "Kein Text", inline=False)
        embed.add_field(name="Nachher", value=after.content[:1024] or "Kein Text", inline=False)
        await self.log(before.guild, "messages", embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return
        message = payload.cached_message
        channel = guild.get_channel(payload.channel_id)
        if message is not None and self._skip(message.author, channel):
            return
        if message is None and self._skip(None, channel):
            return
        embed = discord.Embed(title="Nachricht gelöscht", color=_pulse_color("danger"), timestamp=discord.utils.utcnow())
        embed.add_field(name="Kanal", value=f"<#{payload.channel_id}>", inline=False)
        if message is None:
            embed.description = "Inhalt nicht im Cache (ältere Nachricht)."
        else:
            embed.add_field(name="Autor", value=message.author.mention, inline=False)
            embed.add_field(name="Inhalt", value=(message.content or "Kein Text")[:1024], inline=False)
            if message.attachments:
                embed.add_field(name="Anhänge", value=", ".join(a.filename for a in message.attachments)[:1024], inline=False)
        await self.log(guild, "messages", embed)

    # ------------------------------------------------------------------ members
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self._skip(member):
            return
        embed = discord.Embed(title="Beigetreten", description=f"{member.mention} · {member}", color=_pulse_color("success"),
                              timestamp=discord.utils.utcnow())
        embed.add_field(name="Kontoalter", value=account_age(member), inline=True)
        await self.log(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self._skip(member):
            return
        embed = discord.Embed(title="Verlassen", description=f"{member.mention} · {member}", color=_pulse_color("danger"),
                              timestamp=discord.utils.utcnow())
        joined = getattr(member, "joined_at", None)
        if joined:
            embed.add_field(name="Dabei seit", value=discord.utils.format_dt(joined, "R"), inline=True)
        await self.log(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if self._skip(after):
            return
        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            embed = discord.Embed(title="Rollen geändert", color=_pulse_color("info"), timestamp=discord.utils.utcnow())
            embed.add_field(name="Mitglied", value=after.mention, inline=False)
            if added:
                embed.add_field(name="Hinzugefügt", value=", ".join(added)[:1024], inline=False)
            if removed:
                embed.add_field(name="Entfernt", value=", ".join(removed)[:1024], inline=False)
            await self.log(after.guild, "roles", embed)
        if before.nick != after.nick:
            embed = discord.Embed(title="Nickname geändert", color=_pulse_color("info"), timestamp=discord.utils.utcnow())
            embed.add_field(name="Mitglied", value=after.mention, inline=False)
            embed.add_field(name="Vorher", value=before.nick or "–", inline=True)
            embed.add_field(name="Nachher", value=after.nick or "–", inline=True)
            await self.log(after.guild, "members", embed)

    # ------------------------------------------------------------------ moderation (actor from the audit log)
    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        """Needs the bot permission "Audit-Log anzeigen". Bans, unbans, kicks and timeouts with actor and reason."""
        action = entry.action
        titles = {discord.AuditLogAction.ban: ("Gebannt", "danger"), discord.AuditLogAction.unban: ("Entbannt", "success"),
                  discord.AuditLogAction.kick: ("Gekickt", "danger")}
        title = titles.get(action)
        if action == discord.AuditLogAction.member_update:
            until = getattr(getattr(entry.changes, "after", None), "timed_out_until", None)
            was = getattr(getattr(entry.changes, "before", None), "timed_out_until", None)
            if until:
                title = ("Timeout", "warning")
            elif was:
                title = ("Timeout aufgehoben", "success")
        if title is None:
            return
        target = entry.target
        embed = discord.Embed(title=title[0], color=_pulse_color(title[1]), timestamp=discord.utils.utcnow())
        embed.add_field(name="Betroffen", value=getattr(target, "mention", str(target)), inline=True)
        embed.add_field(name="Durch", value=getattr(entry.user, "mention", "unbekannt"), inline=True)
        if title[0] == "Timeout":
            embed.add_field(name="Bis", value=discord.utils.format_dt(entry.changes.after.timed_out_until, "f"), inline=True)
        embed.add_field(name="Grund", value=(entry.reason or "kein Grund angegeben")[:1024], inline=False)
        await self.log(entry.guild, "moderation", embed)

    # ------------------------------------------------------------------ server roles and channels
    async def _structure(self, guild, category, title, color, text):
        embed = discord.Embed(title=title, description=text[:2000], color=_pulse_color(color), timestamp=discord.utils.utcnow())
        await self.log(guild, category, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        await self._structure(role.guild, "roles", "Rolle erstellt", "success", f"{role.mention} ({role.name})")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self._structure(role.guild, "roles", "Rolle gelöscht", "danger", role.name)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"Name: {before.name} → {after.name}")
        if before.permissions != after.permissions:
            changes.append("Berechtigungen geändert")
        if before.position != after.position:
            return  # position shifts come in bulk when one role moves – not useful in the log
        if changes:
            await self._structure(after.guild, "roles", "Rolle geändert", "info", f"{after.mention}\n" + "\n".join(changes))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self._structure(channel.guild, "channels", "Kanal erstellt", "success", f"{channel.mention} ({channel.name})")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._structure(channel.guild, "channels", "Kanal gelöscht", "danger", f"#{channel.name}")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"Name: {before.name} → {after.name}")
        if getattr(before, "overwrites", None) != getattr(after, "overwrites", None):
            changes.append("Berechtigungen geändert")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append("Thema geändert")
        if changes:
            await self._structure(after.guild, "channels", "Kanal geändert", "info", f"{after.mention}\n" + "\n".join(changes))

    # ------------------------------------------------------------------ voice (switchable, throttled)
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel or self._skip(member):
            return
        if not self.voice_throttle.allow(member.id, time.monotonic()):
            return
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="Sprachkanal betreten", description=f"{member.mention} hat **{after.channel.name}** betreten.",
                                  color=_pulse_color("success"), timestamp=discord.utils.utcnow())
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title="Sprachkanal verlassen", description=f"{member.mention} hat **{before.channel.name}** verlassen.",
                                  color=_pulse_color("danger"), timestamp=discord.utils.utcnow())
        else:
            embed = discord.Embed(title="Sprachkanal gewechselt",
                                  description=f"{member.mention}: **{before.channel.name}** → **{after.channel.name}**",
                                  color=_pulse_color("info"), timestamp=discord.utils.utcnow())
        await self.log(member.guild, "voice", embed)


async def setup(bot):
    await bot.add_cog(AdvancedLogs(bot))

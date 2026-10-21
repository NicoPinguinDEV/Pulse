"""Advanced Discord event logging using the admin-configured Pulse log channel."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

import pulse_db
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color

# K-12: default destination is configured in Verwaltung → Discord-Auftritt.
LOG_SETTING_KEY = "moderation_log_channel_id"
LOG_CONFIG_SETTING_KEY = "advanced_logs_config"
_HINT_SHOWN = False
LOG_CATEGORIES = ("messages", "members", "moderation", "roles", "channels", "voice")
DEFAULT_LOG_CONFIG = {
    "enabled": {category: True for category in LOG_CATEGORIES},
    "channels": {},
    "ignore_channel_ids": [],
    "ignore_role_ids": [],
    "ignore_bots": True,
}


def _id_set(values) -> set[int]:
    result = set()
    if not isinstance(values, (list, tuple, set)):
        return result
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0:
            result.add(item)
    return result


def configured_log_channel_id() -> int:
    try:
        raw = str(pulse_db.get_setting(LOG_SETTING_KEY, "") or "").strip()
    except Exception:
        return 0
    return int(raw) if raw.isdigit() else 0


def configured_log_config() -> dict:
    """Return a safe, default-filled snapshot for category and ignore settings."""
    try:
        raw = pulse_db.get_setting(LOG_CONFIG_SETTING_KEY, {})
    except Exception:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    enabled = dict(DEFAULT_LOG_CONFIG["enabled"])
    if isinstance(raw.get("enabled"), dict):
        for key in LOG_CATEGORIES:
            if key in raw["enabled"]:
                enabled[key] = bool(raw["enabled"][key])
    channels = {}
    if isinstance(raw.get("channels"), dict):
        for category in LOG_CATEGORIES:
            try:
                channel_id = int(raw["channels"].get(category) or 0)
            except (TypeError, ValueError):
                channel_id = 0
            if channel_id > 0:
                channels[category] = channel_id
    return {
        "enabled": enabled,
        "channels": channels,
        "ignore_channel_ids": sorted(_id_set(raw.get("ignore_channel_ids", []))),
        "ignore_role_ids": sorted(_id_set(raw.get("ignore_role_ids", []))),
        "ignore_bots": bool(raw.get("ignore_bots", DEFAULT_LOG_CONFIG["ignore_bots"])),
    }


def log_channel_status(guild) -> str:
    """Short status for the system page; empty string when everything is fine."""
    channel_id = configured_log_channel_id()
    if not channel_id:
        return "Moderations-Log nicht eingerichtet (Verwaltung → Discord-Auftritt)"
    if guild is not None and guild.get_channel(channel_id) is None:
        return f"Moderations-Log-Kanal {channel_id} nicht gefunden"
    return ""


def _text_channel(guild, channel_id):
    if guild is None or not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if channel is not None and callable(getattr(channel, "send", None)) else None


def _account_age_days(created_at, now=None) -> int | None:
    if not isinstance(created_at, datetime):
        return None
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, (now - created_at.astimezone(timezone.utc)).days)


def _role_ids(member) -> set[int]:
    try:
        return {int(role.id) for role in getattr(member, "roles", [])}
    except (TypeError, ValueError):
        return set()


class AdvancedLogs(commands.Cog):
    """Optional, metadata-only event logs with a single backwards-compatible default channel."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def log_channel_id(self) -> int:
        return configured_log_channel_id()

    def _ignored(self, category: str, *, source_channel=None, member=None) -> bool:
        cfg = configured_log_config()
        if not cfg["enabled"].get(category, True):
            return True
        if source_channel is not None and int(getattr(source_channel, "id", 0) or 0) in cfg["ignore_channel_ids"]:
            return True
        if member is not None:
            if cfg["ignore_bots"] and bool(getattr(member, "bot", False)):
                return True
            if _role_ids(member) & set(cfg["ignore_role_ids"]):
                return True
        return False

    async def get_log_channel(self, guild, category: str = "moderation"):
        global _HINT_SHOWN
        if guild is None:
            return None
        cfg = configured_log_config()
        if category not in LOG_CATEGORIES:
            category = "moderation"
        if not cfg["enabled"].get(category, True):
            return None
        destination_id = cfg["channels"].get(category) or configured_log_channel_id()
        if not destination_id:
            if not _HINT_SHOWN:
                _HINT_SHOWN = True
                print("ℹ️ Discord-Protokolle: kein Log-Kanal eingerichtet – Verwaltung → Discord-Auftritt.")
            return None
        channel = _text_channel(guild, destination_id)
        if channel is None:
            # Preserve the legacy single-channel setup if a category override becomes stale.
            if destination_id != configured_log_channel_id():
                channel = _text_channel(guild, configured_log_channel_id())
            if channel is None:
                print(f"⚠️ Discord-Protokoll: Zielkanal {destination_id} ist nicht erreichbar.")
        return channel

    async def _send(self, channel, embed):
        try:
            await send_styled(
                channel,
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Discord-Protokoll: Senden fehlgeschlagen ({type(exc).__name__}).")

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or self._ignored("messages", source_channel=message.channel, member=message.author):
            return
        channel = await self.get_log_channel(message.guild, "messages")
        if channel is None:
            return
        attachments = [str(getattr(item, "filename", "Anhang"))[:180] for item in getattr(message, "attachments", [])]
        embed = discord.Embed(
            title="Nachricht gelöscht",
            description=f"{getattr(message.author, 'mention', str(message.author))} · {getattr(message.channel, 'mention', '#' + str(message.channel.id))}",
            color=_pulse_color("danger"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Nachrichten-ID", value=str(message.id), inline=True)
        embed.add_field(name="Textlänge", value=f"{len(getattr(message, 'content', '') or '')} Zeichen (Inhalt nicht gespeichert)", inline=True)
        if attachments:
            listed = "\n".join(f"• {name}" for name in attachments[:10])
            if len(attachments) > 10:
                listed += f"\n• … und {len(attachments) - 10} weitere"
            embed.add_field(name="Anhänge", value=listed[:1024], inline=False)
        else:
            embed.add_field(name="Anhänge", value="Keine", inline=False)
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild is None or before.content == after.content:
            return
        if self._ignored("messages", source_channel=before.channel, member=before.author):
            return
        channel = await self.get_log_channel(before.guild, "messages")
        if channel is None:
            return
        embed = discord.Embed(
            title="Nachricht bearbeitet",
            description=f"{before.author.mention} · {getattr(before.channel, 'mention', '#' + str(getattr(before.channel, 'id', 'unknown')))}",
            color=_pulse_color("warning"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Nachrichten-ID", value=str(before.id), inline=True)
        embed.add_field(name="Textlänge vorher", value=f"{len(before.content or '')} Zeichen", inline=True)
        embed.add_field(name="Textlänge nachher", value=f"{len(after.content or '')} Zeichen", inline=True)
        embed.add_field(name="Datenschutz", value="Nachrichteninhalte werden nicht in das Protokoll kopiert.", inline=False)
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self._ignored("members", member=member):
            return
        channel = await self.get_log_channel(member.guild, "members")
        if channel is None:
            return
        age_days = _account_age_days(getattr(member, "created_at", None))
        embed = discord.Embed(
            title="Mitglied beigetreten",
            description=f"{member.mention} ({member.id})",
            color=_pulse_color("success"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Kontoalter", value=f"{age_days} Tage" if age_days is not None else "Unbekannt", inline=True)
        embed.add_field(name="Konto erstellt", value=discord.utils.format_dt(member.created_at, style="R") if getattr(member, "created_at", None) else "Unbekannt", inline=True)
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self._ignored("members", member=member):
            return
        channel = await self.get_log_channel(member.guild, "members")
        if channel is None:
            return
        age_days = _account_age_days(getattr(member, "created_at", None))
        embed = discord.Embed(
            title="Mitglied hat den Server verlassen",
            description=f"{getattr(member, 'mention', str(member))} ({member.id})",
            color=_pulse_color("warning"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Kontoalter", value=f"{age_days} Tage" if age_days is not None else "Unbekannt", inline=True)
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        old = getattr(before, "channel", None)
        new = getattr(after, "channel", None)
        if old == new or self._ignored("voice", source_channel=new or old, member=member):
            return
        channel = await self.get_log_channel(member.guild, "voice")
        if channel is None:
            return
        if old is None:
            title, description, color = "Sprachkanal betreten", f"{member.mention} hat **{new.name}** betreten.", "success"
        elif new is None:
            title, description, color = "Sprachkanal verlassen", f"{member.mention} hat **{old.name}** verlassen.", "danger"
        else:
            title, description, color = "Sprachkanal gewechselt", f"{member.mention}: **{old.name}** → **{new.name}**.", "info"
        embed = discord.Embed(title=title, description=description, color=_pulse_color(color), timestamp=discord.utils.utcnow())
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles and not self._ignored("roles", member=after):
            channel = await self.get_log_channel(after.guild, "roles")
            if channel:
                added = [role.mention for role in after.roles if role not in before.roles]
                removed = [role.mention for role in before.roles if role not in after.roles]
                embed = discord.Embed(title="Rollen geändert", color=_pulse_color("info"), timestamp=discord.utils.utcnow())
                embed.add_field(name="Mitglied", value=f"{after.mention} ({after.id})", inline=False)
                if added:
                    embed.add_field(name="Hinzugefügt", value=", ".join(added)[:1024], inline=False)
                if removed:
                    embed.add_field(name="Entfernt", value=", ".join(removed)[:1024], inline=False)
                embed.add_field(name="Täter", value="Discord-Audit-Log prüfen (Rollenänderungen werden hier nicht einer Person zugeordnet).", inline=False)
                embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
                await self._send(channel, embed)

        if getattr(before, "nick", None) != getattr(after, "nick", None):
            if self._ignored("members", member=after):
                return
            channel = await self.get_log_channel(after.guild, "members")
            if channel:
                embed = discord.Embed(title="Nickname geändert", color=_pulse_color("info"), timestamp=discord.utils.utcnow())
                embed.add_field(name="Mitglied", value=f"{after.mention} ({after.id})", inline=False)
                embed.add_field(name="Vorher", value=str(getattr(before, "nick", None) or before.name)[:256], inline=True)
                embed.add_field(name="Nachher", value=str(getattr(after, "nick", None) or after.name)[:256], inline=True)
                embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
                await self._send(channel, embed)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        guild = getattr(entry, "guild", None)
        action = str(getattr(getattr(entry, "action", None), "name", "") or "").casefold()
        target = getattr(entry, "target", None)
        actor = getattr(entry, "user", None)
        changes = getattr(entry, "changes", None)
        before_changes = getattr(changes, "before", None)
        after_changes = getattr(changes, "after", None)

        category = None
        title = None
        if action in {"ban", "unban", "kick"}:
            category, title = "moderation", {"ban": "Mitglied gebannt", "unban": "Mitglied entbannt", "kick": "Mitglied gekickt"}[action]
        elif action in {"member_update"} and (
            hasattr(before_changes, "timed_out_until") or hasattr(after_changes, "timed_out_until")
        ):
            category, title = "moderation", "Timeout geändert"
        elif action.startswith("role_"):
            category, title = "roles", "Discord-Rolle geändert"
        elif action.startswith("channel_") or action.startswith("overwrite_"):
            category, title = "channels", "Discord-Kanal/Berechtigung geändert"
        if not category or self._ignored(category, member=actor):
            return
        channel = await self.get_log_channel(guild, category)
        if channel is None:
            return
        embed = discord.Embed(title=title, color=_pulse_color("warning"), timestamp=discord.utils.utcnow())
        embed.add_field(name="Täter", value=f"{getattr(actor, 'mention', str(actor or 'Unbekannt'))}", inline=True)
        embed.add_field(name="Ziel", value=f"{getattr(target, 'mention', str(target or 'Unbekannt'))}", inline=True)
        reason = str(getattr(entry, "reason", "") or "Kein Grund im Audit-Log hinterlegt.")[:900]
        embed.add_field(name="Grund", value=reason, inline=False)
        embed.add_field(name="Audit-Log-ID", value=str(getattr(entry, "id", "—")), inline=True)
        embed.set_footer(text="Pulse TeamOS · Discord-Protokoll")
        await self._send(channel, embed)


async def setup(bot):
    await bot.add_cog(AdvancedLogs(bot))

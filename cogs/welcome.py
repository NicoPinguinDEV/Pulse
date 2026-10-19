"""Welcome/leave messages with member avatar (thumbnail) and custom banner (image).

Configuration lives in Verwaltung → Discord-Auftritt (/admin/discord) and is read
on every event, so changes apply without a restart. Exactly one message per
member event: this is the only cog that sends welcome/leave messages, and a
short-lived de-duplication guard protects against repeated gateway events.
"""
from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands

import pulse_db
from pulse_embed_design import to_discord_embed
from pulse_welcome import SETTING_KEY, build_member_message, normalize_settings

DEDUPE_SECONDS = 60
MAX_DEDUPE_ENTRIES = 500
WARNING_INTERVAL_SECONDS = 3600


def member_info(member: discord.Member | discord.User, guild: discord.Guild) -> dict:
    avatar = getattr(member, "display_avatar", None)
    return {
        "id": member.id,
        "mention": member.mention,
        "display_name": getattr(member, "display_name", None) or member.name,
        "username": member.name,
        "bot": bool(member.bot),
        "avatar_url": avatar.replace(size=256, static_format="png").url if avatar else "",
        "created_at": getattr(member, "created_at", None),
        "joined_at": getattr(member, "joined_at", None),
        "guild_name": guild.name,
        "member_count": guild.member_count if isinstance(guild.member_count, int) else None,
    }


async def send_member_message(bot, guild: discord.Guild, payload: dict) -> tuple[bool, str]:
    """Send a built payload. Returns (ok, error text) – never raises, never retries."""
    channel = guild.get_channel(int(payload["channel_id"])) if payload.get("channel_id") else None
    if channel is None:
        return False, "Zielkanal nicht gefunden (gelöscht oder keine Sicht-Rechte)."
    if not hasattr(channel, "send"):
        return False, "In den Zielkanal kann Pulse keine Nachrichten schreiben (kein Textkanal)."
    me = guild.me
    if me is not None:
        perms = channel.permissions_for(me)
        missing = [label for flag, label in (("send_messages", "Nachrichten senden"), ("embed_links", "Links einbetten"))
                   if not getattr(perms, flag, False)]
        if payload.get("banner_file") and not perms.attach_files:
            missing.append("Dateien anhängen")
        if missing:
            return False, f"Pulse fehlen im Kanal #{channel.name} Rechte: {', '.join(missing)}."
    embed = to_discord_embed(payload["spec"])
    files = []
    if payload.get("banner_file"):
        files.append(discord.File(payload["banner_file"], filename=payload["attachment_name"]))
    allowed = discord.AllowedMentions(
        everyone=False, roles=False,
        users=[discord.Object(id=payload["mention_user_id"])] if payload.get("mention_user_id") else False,
    )
    try:
        await channel.send(content=payload.get("content") or None, embed=embed, files=files, allowed_mentions=allowed)
    except discord.Forbidden:
        return False, f"Discord hat das Senden in #{channel.name} verweigert (Rechte/Rollenhierarchie prüfen)."
    except discord.HTTPException as exc:
        return False, f"Discord-Fehler beim Senden: {exc.status} {str(exc.text)[:120]}"
    return True, ""


class WelcomeLeave(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent: dict[tuple, float] = {}
        self._warned: dict[tuple, float] = {}
        self.base_dir: str | None = None

    def _is_duplicate(self, key: tuple) -> bool:
        now = time.monotonic()
        last = self._recent.get(key)
        if last is not None and now - last < DEDUPE_SECONDS:
            return True
        self._recent[key] = now
        if len(self._recent) > MAX_DEDUPE_ENTRIES:
            for old_key in [k for k, ts in self._recent.items() if now - ts >= DEDUPE_SECONDS]:
                self._recent.pop(old_key, None)
            while len(self._recent) > MAX_DEDUPE_ENTRIES:
                self._recent.pop(next(iter(self._recent)))
        return False

    def _warn_once(self, key: tuple, text: str) -> None:
        now = time.monotonic()
        if now - self._warned.get(key, -WARNING_INTERVAL_SECONDS) < WARNING_INTERVAL_SECONDS:
            return
        self._warned[key] = now
        if len(self._warned) > 50:
            self._warned.pop(next(iter(self._warned)))
        print(f"⚠️ Willkommen/Abschied: {text}")

    async def _handle(self, kind: str, member: discord.Member) -> None:
        guild = member.guild
        if self._is_duplicate((kind, guild.id, member.id)):
            return
        try:
            raw = await asyncio.to_thread(pulse_db.get_setting, SETTING_KEY, {})
            settings = normalize_settings(raw)
            if self.base_dir is None:
                import webserver
                self.base_dir = str(webserver.BASE_DIR)
            payload = build_member_message(kind, settings, member_info(member, guild),
                                           now=discord.utils.utcnow(), base_dir=self.base_dir)
        except Exception as exc:
            print(f"⚠️ Willkommen/Abschied konnte nicht vorbereitet werden: {type(exc).__name__}: {exc}")
            return
        if payload is None:
            return
        ok, error = await send_member_message(self.bot, guild, payload)
        if not ok:
            self._warn_once((kind, payload["channel_id"]), error)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._handle("welcome", member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._handle("leave", member)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeLeave(bot))

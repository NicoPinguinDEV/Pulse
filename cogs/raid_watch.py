"""Read-only raid/spam early warnings. Disabled by default; never takes moderation actions."""
from __future__ import annotations

import hashlib
import os
import time
from collections import deque
from datetime import datetime, timezone

import discord
from discord.ext import commands


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().casefold()
    return raw in {"1", "true", "yes", "on"} if raw else default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _account_age_days(created_at, now=None) -> int | None:
    if not isinstance(created_at, datetime):
        return None
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, (now - created_at.astimezone(timezone.utc)).days)


def _fingerprint(content: str) -> str:
    """Keep a digest, not user message content, in the short-lived in-memory window."""
    normalized = " ".join(str(content or "").casefold().split())
    return hashlib.blake2b(normalized.encode("utf-8", "replace"), digest_size=16).hexdigest()


class RaidWatch(commands.Cog):
    """Optional heuristic alerts for join bursts, mass mentions and cross-channel repeats."""

    MAX_EVENTS = 500
    ALERT_COOLDOWN_SECONDS = 300
    NEW_ACCOUNT_DAYS = 7
    DUPLICATE_MIN_LENGTH = 40
    DUPLICATE_WINDOW_SECONDS = 90

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.enabled = _env_bool("PULSE_RAID_WATCH_ENABLED", False)
        configured_alert_id = _env_int("PULSE_RAID_WATCH_ALERT_CHANNEL_ID", 0, 0, 2**63 - 1)
        fallback_alert_id = _env_int("PULSE_SYSTEM_ALERT_CHANNEL_ID", 0, 0, 2**63 - 1)
        self.alert_channel_id = configured_alert_id or fallback_alert_id
        self.join_window_seconds = _env_int("PULSE_RAID_WATCH_JOIN_WINDOW_SECONDS", 120, 30, 600)
        self.join_threshold = _env_int("PULSE_RAID_WATCH_JOIN_THRESHOLD", 12, 5, 100)
        self.new_account_threshold = _env_int("PULSE_RAID_WATCH_NEW_ACCOUNT_THRESHOLD", 6, 3, 50)
        self.mention_threshold = _env_int("PULSE_RAID_WATCH_MENTION_THRESHOLD", 8, 4, 100)
        self.duplicate_channel_threshold = _env_int("PULSE_RAID_WATCH_DUPLICATE_CHANNELS", 4, 3, 20)
        self._joins: deque[tuple[float, int, bool]] = deque(maxlen=self.MAX_EVENTS)
        # Only digests/IDs/timestamps are held: message bodies are never retained.
        self._messages: deque[tuple[float, int, int, str]] = deque(maxlen=self.MAX_EVENTS)
        self._alerted_at: dict[tuple[int, str], float] = {}
        self._missing_channel_warned = False

    def _now(self) -> float:
        return time.monotonic()

    async def _alert(self, guild, kind: str, headline: str, observation: str, suggestion: str) -> bool:
        if not self.enabled or not self.alert_channel_id:
            return False
        now = self._now()
        key = (int(guild.id), kind)
        previous = self._alerted_at.get(key)
        if previous is not None and now - previous < self.ALERT_COOLDOWN_SECONDS:
            return False

        channel = self.bot.get_channel(self.alert_channel_id) if hasattr(self.bot, "get_channel") else None
        if channel is None and hasattr(self.bot, "fetch_channel"):
            try:
                channel = await self.bot.fetch_channel(self.alert_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel is None or not callable(getattr(channel, "send", None)):
            if not self._missing_channel_warned:
                self._missing_channel_warned = True
                print("⚠️ Pulse Raid-Watch aktiviert, aber der Alarmkanal ist nicht erreichbar.")
            return False
        channel_guild = getattr(channel, "guild", None)
        if channel_guild is not None and int(getattr(channel_guild, "id", 0)) != int(guild.id):
            return False

        content = (
            "⚠️ **Pulse · Raid-/Spam-Frühwarnung**\n"
            f"**Muster:** {headline}\n"
            f"**Beobachtung:** {observation}\n"
            f"**Empfohlene Prüfung:** {suggestion}\n"
            "_Nur ein Hinweis: Pulse führt keine Sanktionen aus und ändert keine Kanalrechte._"
        )
        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Pulse Raid-Watch: Alarm konnte nicht gesendet werden ({type(exc).__name__}).")
            return False

        self._alerted_at[key] = now
        while len(self._alerted_at) > 32:
            self._alerted_at.pop(next(iter(self._alerted_at)))
        return True

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.enabled or getattr(member, "guild", None) is None:
            return
        now = self._now()
        guild_id = int(member.guild.id)
        age_days = _account_age_days(getattr(member, "created_at", None))
        is_fresh = age_days is not None and age_days <= self.NEW_ACCOUNT_DAYS
        self._joins.append((now, guild_id, is_fresh))

        recent = [
            event for event in self._joins
            if event[1] == guild_id and now - event[0] <= self.join_window_seconds
        ]
        fresh_count = sum(1 for event in recent if event[2])
        if len(recent) >= self.join_threshold:
            await self._alert(
                member.guild, "join-burst", "Viele Beitritte in kurzer Zeit",
                f"{len(recent)} Beitritte innerhalb von {self.join_window_seconds} Sekunden.",
                "Mitgliederliste und Audit-Log prüfen; verdächtige Einladungen bei Bedarf manuell begrenzen.",
            )
        elif fresh_count >= self.new_account_threshold:
            await self._alert(
                member.guild, "new-account-burst", "Viele sehr junge Konten",
                f"{fresh_count} Konten, höchstens {self.NEW_ACCOUNT_DAYS} Tage alt, innerhalb von {self.join_window_seconds} Sekunden beigetreten.",
                "Kontoalter und Beitrittsmuster prüfen; keine Sanktion allein aufgrund dieser Meldung ausführen.",
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.enabled or getattr(message, "guild", None) is None:
            return
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False) or getattr(message, "webhook_id", None):
            return
        content = str(getattr(message, "content", "") or "")
        guild = message.guild
        mention_count = len(getattr(message, "mentions", []) or []) + len(getattr(message, "role_mentions", []) or [])
        if bool(getattr(message, "mention_everyone", False)) or mention_count >= self.mention_threshold:
            await self._alert(
                guild, "mass-mentions", "Massen-Erwähnung",
                f"Eine Nachricht enthält {'@everyone/@here' if getattr(message, 'mention_everyone', False) else str(mention_count) + ' Erwähnungen'}.",
                "Nachricht und Kanal-Kontext durch das Team prüfen; Pulse löscht oder bestraft nicht automatisch.",
            )

        normalized = " ".join(content.casefold().split())
        if len(normalized) < self.DUPLICATE_MIN_LENGTH:
            return
        channel_id = int(getattr(getattr(message, "channel", None), "id", 0) or 0)
        if not channel_id:
            return
        now = self._now()
        fingerprint = _fingerprint(normalized)
        self._messages.append((now, int(guild.id), channel_id, fingerprint))
        recent = [
            event for event in self._messages
            if event[1] == int(guild.id)
            and event[3] == fingerprint
            and now - event[0] <= self.DUPLICATE_WINDOW_SECONDS
        ]
        channel_ids = {event[2] for event in recent}
        if len(channel_ids) >= self.duplicate_channel_threshold:
            await self._alert(
                guild, "cross-channel-repeat", "Identischer Text in mehreren Kanälen",
                f"Dieselbe Nachricht wurde innerhalb von {self.DUPLICATE_WINDOW_SECONDS} Sekunden in {len(channel_ids)} verschiedenen Kanälen beobachtet; der Nachrichtentext wurde nicht gespeichert.",
                "Absender und betroffene Kanäle prüfen; gegebenenfalls die Nachrichten manuell moderieren.",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidWatch(bot))

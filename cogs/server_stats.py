"""Automatic, non-joinable voice channels with live server statistics."""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

STATS_CATEGORY_ID = 1536812484462256179
REFRESH_DEBOUNCE_SECONDS = 3

# Prefixes stay stable as the numbers change, so restarts never create duplicates.
STAT_DEFINITIONS = (
    ("members", "👤・Mitglieder: ", "member_count"),
    ("boosts", "💎・Boosts: ", "premium_subscription_count"),
    ("bots", "🤖・Bots: ", "bot_count"),
)


def format_count(value: int) -> str:
    """Format counts in the German thousands style used by the dashboard."""
    return f"{max(0, int(value)):,}".replace(",", ".")


def locked_overwrites(category: discord.CategoryChannel, guild: discord.Guild) -> dict:
    """Copy category visibility/access, but explicitly deny connecting to everyone."""
    overwrites = category.overwrites
    if guild.default_role not in overwrites:
        overwrites[guild.default_role] = discord.PermissionOverwrite()
    for overwrite in overwrites.values():
        # Explicitly deny connection on every category override as well; otherwise
        # a role-level allow could override the @everyone deny.
        overwrite.connect = False
    overwrites[guild.default_role].connect = False
    return overwrites


class ServerStatsVoiceChannels(commands.Cog):
    """Maintains three visible voice channels whose names show live server counts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._category: discord.CategoryChannel | None = None
        self._refresh_task: asyncio.Task | None = None
        self._refresh_requested = False

    async def _get_category(self) -> discord.CategoryChannel | None:
        category = self.bot.get_channel(STATS_CATEGORY_ID)
        if category is None:
            try:
                category = await self.bot.fetch_channel(STATS_CATEGORY_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(
                    f"📊 Server-Stats: Kategorie {STATS_CATEGORY_ID} nicht erreichbar: "
                    f"{type(exc).__name__}: {exc}"
                )
                return None
        if not isinstance(category, discord.CategoryChannel):
            print(
                f"❌ Server-Stats: ID {STATS_CATEGORY_ID} ist keine Kategorie. "
                "Bitte die ID der Discord-Kategorie verwenden."
            )
            return None
        self._category = category
        return category

    def schedule_refresh(self) -> None:
        """Coalesce bursts of join/boost events into one set of channel updates."""
        self._refresh_requested = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(), name="pulse-server-stats-refresh"
            )

    async def _refresh_loop(self) -> None:
        try:
            while self._refresh_requested:
                self._refresh_requested = False
                await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS)
                await self.refresh_stats()
        except asyncio.CancelledError:
            raise
        finally:
            self._refresh_task = None
            # An event might have arrived while the loop was being finalized.
            if self._refresh_requested:
                self.schedule_refresh()

    @staticmethod
    def _current_counts(guild: discord.Guild) -> dict[str, int]:
        # guild.member_count is Discord's full guild count, including bots.
        # discord.py's member cache is chunked at startup by the current bot setup.
        total_members = guild.member_count
        if total_members is None:
            total_members = len(guild.members)
        bots = sum(1 for member in guild.members if member.bot)
        return {
            "member_count": int(total_members),
            "premium_subscription_count": int(guild.premium_subscription_count or 0),
            "bot_count": bots,
        }

    @staticmethod
    def _target_name(prefix: str, value: int) -> str:
        return f"{prefix}{format_count(value)}"

    async def _ensure_locked(self, channel: discord.VoiceChannel) -> None:
        overwrites = channel.overwrites
        changed = False
        guild = channel.guild
        if guild.default_role not in overwrites:
            overwrites[guild.default_role] = discord.PermissionOverwrite(connect=False)
            changed = True
        for overwrite in overwrites.values():
            if overwrite.connect is not False:
                overwrite.connect = False
                changed = True
        if changed:
            await channel.edit(
                overwrites=overwrites,
                reason="Pulse Server-Statistik: Sprachkanal gegen Beitritt sperren",
            )

    async def refresh_stats(self) -> None:
        category = self._category
        if category is None:
            category = await self._get_category()
        if category is None:
            return

        guild = category.guild
        counts = self._current_counts(guild)
        overwrite_template = locked_overwrites(category, guild)

        for _key, prefix, count_key in STAT_DEFINITIONS:
            desired_name = self._target_name(prefix, counts[count_key])
            channel = next(
                (
                    voice
                    for voice in category.voice_channels
                    if voice.name.startswith(prefix)
                ),
                None,
            )
            try:
                if channel is None:
                    channel = await category.create_voice_channel(
                        name=desired_name,
                        overwrites={
                            target: discord.PermissionOverwrite.from_pair(*overwrite.pair())
                            for target, overwrite in overwrite_template.items()
                        },
                        reason="Pulse: automatische Server-Statistik",
                    )
                    print(f"📊 Server-Statistik-Sprachkanal erstellt: {desired_name}")
                else:
                    await self._ensure_locked(channel)
                    if channel.name != desired_name:
                        await channel.edit(
                            name=desired_name,
                            reason="Pulse: Server-Statistik aktualisiert",
                        )
            except discord.Forbidden:
                print(
                    f"❌ Server-Stats: Keine Rechte, um '{desired_name}' zu erstellen/"
                    "aktualisieren. Pulse benötigt 'Kanäle verwalten' in der Kategorie."
                )
            except discord.HTTPException as exc:
                print(f"⚠️ Server-Stats: '{desired_name}' konnte nicht aktualisiert werden: {exc}")

        # Channel create/rename updates the bot's cache, but refresh the category
        # reference so subsequent events see the newly created channels.
        cached_category = self.bot.get_channel(STATS_CATEGORY_ID)
        if isinstance(cached_category, discord.CategoryChannel):
            self._category = cached_category

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.schedule_refresh()
        print(f"📊 Server-Statistik-Sprachkanäle für Kategorie {STATS_CATEGORY_ID} aktiviert.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self._category is None or member.guild.id == self._category.guild.id:
            self.schedule_refresh()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if self._category is None or member.guild.id == self._category.guild.id:
            self.schedule_refresh()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Boost status is updated as part of the member update event.
        if before.premium_since != after.premium_since:
            self.schedule_refresh()

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if after.id == STATS_CATEGORY_ID or (
            self._category is not None and after.id == self._category.guild.id
        ):
            self.schedule_refresh()

    async def cog_unload(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerStatsVoiceChannels(bot))

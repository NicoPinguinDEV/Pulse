from __future__ import annotations

import asyncio

import discord
from discord.ext import commands, tasks

import pulse_next


class PulseNextEvents(commands.Cog):
    """Event bridge for Pulse TeamOS 2.0.

    Keeps activity telemetry and onboarding/offboarding in sync with Discord
    role membership without touching the legacy command handlers.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.housekeeping.start()

    def cog_unload(self):
        self.housekeeping.cancel()

    async def _ready(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def housekeeping(self):
        await pulse_next.run_workflows(self.bot)
        try:
            guild = self.bot.get_guild(int(__import__("os").getenv("DISCORD_GUILD_ID", "1474514929351524616")))
            if guild:
                for member in pulse_next.members(guild):
                    pulse_next.seed_profile_for_member(member)
                    pulse_next.record_score(member)
        except Exception:
            pass

    @housekeeping.before_loop
    async def before_housekeeping(self):
        await self._ready()

    def _is_team(self, member: discord.Member) -> bool:
        try:
            import webserver
            team_roles = {int(x) for x in webserver.load_config().get("team_role_ids", [])}
            return any(role.id in team_roles for role in member.roles)
        except Exception:
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not self._is_team(member):
            return
        pulse_next.seed_profile_for_member(member)
        pulse_next.event(
            str(member.id),
            "onboarding",
            "Teambeitritt erkannt",
            "Pulse hat das Teamprofil automatisch angelegt.",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        try:
            import pulse_ultimate as u
            with u.cx() as c:
                c.execute(
                    "UPDATE ultimate_profiles SET archived_at=? WHERE user_id=?",
                    (pulse_next.iso(), str(member.id)),
                )
            pulse_next.event(
                str(member.id),
                "offboarding",
                "Teammitglied ausgeschieden",
                "Discord-Mitglied wurde entfernt; Profil archiviert.",
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        was_team = self._is_team(before)
        is_team = self._is_team(after)
        if not was_team and is_team:
            pulse_next.seed_profile_for_member(after)
            pulse_next.event(
                str(after.id),
                "onboarding",
                "Teambeitritt erkannt",
                "Teamrolle wurde vergeben.",
            )
        elif was_team and not is_team:
            try:
                import pulse_ultimate as u
                with u.cx() as c:
                    c.execute(
                        "UPDATE ultimate_profiles SET archived_at=? WHERE user_id=?",
                        (pulse_next.iso(), str(after.id)),
                    )
                pulse_next.event(
                    str(after.id),
                    "offboarding",
                    "Teamrolle entfernt",
                    "Pulse hat das Profil archiviert.",
                )
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if not after.bot and self._is_team(after):
            # Telemetry is intentionally throttled in pulse_next.
            activity = pulse_next.get_activity(str(after.id))
            if not activity or activity.get("last_kind") != "discord_presence":
                pulse_next.touch_member(str(after.id), "discord_presence")


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseNextEvents(bot))

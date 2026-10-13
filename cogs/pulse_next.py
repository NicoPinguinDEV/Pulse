from __future__ import annotations

import asyncio
import time
from pathlib import Path

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
        # Cache role configuration by file mtime: presence updates can be frequent,
        # so never reopen/parse the JSON settings on every gateway event.
        self._team_role_ids_cache: set[int] = set()
        self._team_role_config_mtime_ns: int | None = None
        self._team_role_config_loaded = False
        # Presence may fire dozens of times per member. Keep the 5-minute persistence
        # throttle in memory instead of querying SQLite for every event.
        self._presence_last_write: dict[int, float] = {}
        self.housekeeping.start()

    def cog_unload(self):
        self.housekeeping.cancel()

    async def _ready(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def housekeeping(self):
        # Team score/profile maintenance performs synchronous SQLite work.
        # Never run it on Discord's event loop: setup()/member_stats() can
        # initialize tables and execute multiple queries, blocking heartbeats.
        guild = self.bot.get_guild(int(__import__("os").getenv("DISCORD_GUILD_ID", "1474514929351524616")))
        await asyncio.to_thread(self._housekeeping_sync, guild)

    def _housekeeping_sync(self, guild: discord.Guild | None) -> None:
        """Run synchronous workflow/profile/score database work off the event loop."""
        try:
            # run_workflows is declared async, but currently performs only
            # synchronous SQLite/cache work. Run and await it inside this
            # worker thread rather than blocking Discord's event loop.
            asyncio.run(pulse_next.run_workflows(self.bot))
        except Exception as exc:
            print(f"Pulse Workflow-Housekeeping fehlgeschlagen: {exc}")

        if not guild:
            return

        try:
            members = pulse_next.members(guild)
        except Exception as exc:
            print(f"Pulse Teammitglieder konnten nicht geladen werden: {exc}")
            return

        for member in members:
            try:
                pulse_next.seed_profile_for_member(member)
                pulse_next.record_score(member)
            except Exception as exc:
                print(f"Pulse Score-Update für {member.id} fehlgeschlagen: {exc}")

    @housekeeping.before_loop
    async def before_housekeeping(self):
        await self._ready()

    def _team_role_ids(self) -> set[int]:
        import webserver

        try:
            config_mtime_ns = Path(webserver.CONFIG_FILE).stat().st_mtime_ns
        except OSError:
            config_mtime_ns = None

        if (
            not self._team_role_config_loaded
            or config_mtime_ns != self._team_role_config_mtime_ns
        ):
            try:
                config = webserver.load_config()
                self._team_role_ids_cache = {
                    int(role_id) for role_id in config.get("team_role_ids", [])
                }
            except (OSError, ValueError, TypeError) as exc:
                # Cache the empty result until the file changes, rather than
                # retrying disk access on every subsequent presence event.
                self._team_role_ids_cache = set()
                print(f"Pulse Teamrollen-Konfiguration konnte nicht geladen werden ({type(exc).__name__}).")
            self._team_role_config_mtime_ns = config_mtime_ns
            self._team_role_config_loaded = True
        return self._team_role_ids_cache

    def _is_team(self, member: discord.Member) -> bool:
        try:
            team_roles = self._team_role_ids()
            return any(role.id in team_roles for role in member.roles)
        except Exception:
            return False

    def _record_team_since(self, member: discord.Member) -> None:
        """Speichert den Beginn der aktuellen Teamzugehörigkeit."""
        try:
            import webserver
            team_db = webserver.load_json(webserver.DATA_FILE, {})
            entry = webserver.user_entry(team_db, str(member.id))
            entry["team_since"] = webserver.now_de().isoformat(timespec="seconds")
            webserver.save_json(webserver.DATA_FILE, team_db)
        except Exception as exc:
            print(f"Team-Eintritt konnte nicht gespeichert werden: {exc}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not self._is_team(member):
            return
        self._record_team_since(member)
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
            self._record_team_since(after)
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
        if after.bot or not self._is_team(after):
            return

        # At most one write per member every five minutes. This avoids a SQLite
        # read on every presence event, which can otherwise delay the Discord
        # gateway when many users update status at once.
        now = time.monotonic()
        previous = self._presence_last_write.get(after.id)
        if previous is not None and now - previous < 300:
            return
        self._presence_last_write.pop(after.id, None)
        self._presence_last_write[after.id] = now
        while len(self._presence_last_write) > 2000:
            self._presence_last_write.pop(next(iter(self._presence_last_write)))

        try:
            await asyncio.to_thread(
                pulse_next.touch_member, str(after.id), "discord_presence"
            )
        except Exception as exc:
            # Permit a later event to retry if persistence failed.
            self._presence_last_write.pop(after.id, None)
            print(f"Pulse Presence-Aktivität für {after.id} konnte nicht gespeichert werden ({type(exc).__name__}).")


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseNextEvents(bot))

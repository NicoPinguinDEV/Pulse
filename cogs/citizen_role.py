"""Automatically assign the citizen role to new human members of the main server."""
from __future__ import annotations
import os
import time
import discord
from discord.ext import commands
import pulse_ids

CITIZEN_ROLE_ID = pulse_ids.CITIZEN_ROLE_ID
JOIN_DEDUPE_SECONDS = 60
WARNING_INTERVAL_SECONDS = 3600
MAX_TRACKED = 500

def _guild_id() -> int:
    raw = os.getenv("DISCORD_GUILD_ID", str(pulse_ids.GUILD_ID))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return pulse_ids.GUILD_ID

class CitizenRoleOnJoin(commands.Cog):
    """Grant the citizen role once on join; never gate access behind a verify action."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_id = _guild_id()
        self._recent_joins: dict[tuple[int, int], float] = {}
        self._warned: dict[str, float] = {}

    def _bounded(self, mapping: dict, key, value) -> None:
        mapping[key] = value
        while len(mapping) > MAX_TRACKED:
            mapping.pop(next(iter(mapping)))

    def _warn_once(self, key: str, message: str) -> None:
        now = time.monotonic()
        if now - self._warned.get(key, -WARNING_INTERVAL_SECONDS) < WARNING_INTERVAL_SECONDS:
            return
        self._bounded(self._warned, key, now)
        print(f"⚠️ Bürgerrolle: {message}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> str:
        guild = member.guild
        if member.bot:
            return "bot"
        if self.guild_id and guild.id != self.guild_id:
            return "wrong_guild"
        now = time.monotonic()
        join_key = (guild.id, member.id)
        previous = self._recent_joins.get(join_key)
        if previous is not None and now - previous < JOIN_DEDUPE_SECONDS:
            return "duplicate"
        self._bounded(self._recent_joins, join_key, now)
        if any(getattr(role, "id", None) == CITIZEN_ROLE_ID for role in member.roles):
            return "already_has_role"
        citizen_role = guild.get_role(CITIZEN_ROLE_ID)
        if citizen_role is None:
            self._warn_once("missing_role", f"Discord-Rolle {CITIZEN_ROLE_ID} existiert auf dem Server nicht.")
            return "missing_role"
        bot_member = getattr(guild, "me", None)
        permissions = getattr(bot_member, "guild_permissions", None)
        if not getattr(permissions, "manage_roles", False):
            self._warn_once("missing_permission", "Pulse fehlt die Berechtigung „Rollen verwalten“. Bürgerrolle wurde nicht vergeben.")
            return "missing_permission"
        top_role = getattr(bot_member, "top_role", None)
        top_position = getattr(top_role, "position", 0)
        role_position = getattr(citizen_role, "position", 0)
        if top_role is None or role_position >= top_position:
            self._warn_once("role_hierarchy", "Die Bürgerrolle liegt auf oder über der höchsten Pulse-Rolle. Bürgerrolle wurde nicht vergeben.")
            return "role_hierarchy"
        try:
            await member.add_roles(citizen_role, reason="Pulse: Bürgerrolle automatisch beim Serverbeitritt")
        except discord.Forbidden:
            self._warn_once("forbidden", "Discord hat die Vergabe der Bürgerrolle verweigert (Rechte/Rollen-Hierarchie prüfen).")
            return "forbidden"
        except discord.HTTPException as exc:
            self._warn_once("http_error", f"Discord-Fehler bei der Bürgerrollen-Vergabe (HTTP {exc.status}).")
            return "http_error"
        return "assigned"

async def setup(bot: commands.Bot):
    await bot.add_cog(CitizenRoleOnJoin(bot))

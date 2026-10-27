"""Auto-rejoin for members who verified via Discord OAuth (identify + guilds.join).

Listeners record why someone left (voluntary leave, kick, ban); the hourly job re-adds voluntary
leavers after the 24-hour grace period (pulse_rejoin.run_rejoin). /rejoin-abmelden deletes the
member's stored authorization. Off by default: nothing happens until an admin enables it in
Verwaltung → Datenschutz.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

import pulse_rejoin
from pulse_jobs import tracked

KICK_LOOKBACK_SECONDS = 30


def _guild_id() -> int:
    try:
        from webserver import GUILD_ID
        return int(GUILD_ID or 0)
    except Exception:
        return 0


def _audit(action: str, details: str) -> None:
    try:
        import webserver as ws
        ws.log_audit("Pulse", "system", action, details)
    except Exception as exc:
        print(f"⚠️ Rejoin-Audit nicht gespeichert ({type(exc).__name__}).")


async def leave_reason(guild: discord.Guild, user_id: int, *, now: datetime | None = None) -> str:
    """'kick' when the audit log shows a recent kick of this member, 'leave' otherwise, 'unknown' if unreadable.

    'unknown' never leads to a rejoin: without the audit log Pulse cannot rule out a kick.
    """
    now = now or datetime.now(timezone.utc)
    me = getattr(guild, "me", None)
    if not me or not getattr(me.guild_permissions, "view_audit_log", False):
        return "unknown"
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
            if getattr(entry.target, "id", None) == user_id and now - entry.created_at <= timedelta(seconds=KICK_LOOKBACK_SECONDS):
                return "kick"
    except (discord.Forbidden, discord.HTTPException):
        return "unknown"
    return "leave"


class Rejoin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_id = _guild_id()
        if hasattr(bot, "wait_until_ready"):
            self.rejoin_loop.start()

    def cog_unload(self):
        self.rejoin_loop.cancel()

    # ------------------------------------------------------------------ membership tracking
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if (self.guild_id and member.guild.id != self.guild_id) or member.bot:
            return
        if not await asyncio.to_thread(pulse_rejoin.has_authorization, member.id):
            return
        await asyncio.sleep(2)  # the audit log entry of a kick appears shortly after the event
        reason = await leave_reason(member.guild, member.id)
        await asyncio.to_thread(pulse_rejoin.mark_left, member.id, reason)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user):
        if self.guild_id and guild.id != self.guild_id:
            return
        await asyncio.to_thread(pulse_rejoin.mark_left, user.id, "ban")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if self.guild_id and member.guild.id != self.guild_id:
            return
        await asyncio.to_thread(pulse_rejoin.mark_present, member.id)

    # ------------------------------------------------------------------ hourly job
    @tasks.loop(hours=1)
    @tracked("Auto-Rejoin (stündlich)", 3600)
    async def rejoin_loop(self):
        await self.bot.wait_until_ready()
        await self.run_once()

    async def run_once(self, client: httpx.AsyncClient | None = None) -> dict:
        if not await asyncio.to_thread(pulse_rejoin.is_enabled):
            return {"ran": False, "stopped": "deaktiviert"}
        if client is None:
            async with httpx.AsyncClient(timeout=pulse_rejoin.TOKEN_TIMEOUT) as own:
                report = await pulse_rejoin.run_rejoin(self.bot, own)
        else:
            report = await pulse_rejoin.run_rejoin(self.bot, client)
        for uid in report.get("revoked", []):
            await asyncio.to_thread(_audit, "Rejoin-Autorisierung entfernt", f"Discord-ID {uid}: Autorisierung widerrufen/ungültig")
        if report.get("added") or report.get("banned") or report.get("failed") or report.get("stopped"):
            await asyncio.to_thread(
                _audit, "Auto-Rejoin-Lauf",
                f"zurückgeholt {report.get('added', 0)} · gebannt übersprungen {report.get('banned', 0)} · "
                f"Fehler {report.get('failed', 0)}" + (f" · abgebrochen: {report['stopped']}" if report.get("stopped") else ""),
            )
        return report

    # ------------------------------------------------------------------ opt-out
    @app_commands.command(
        name="rejoin-abmelden",
        description="Widerruft deine Verifizierungs-Autorisierung (Auto-Rejoin) und löscht die gespeicherten Daten",
    )
    async def rejoin_optout(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        removed = await pulse_rejoin.forget(interaction.user.id)
        if removed:
            await asyncio.to_thread(_audit, "Rejoin-Autorisierung gelöscht", f"Discord-ID {interaction.user.id} (selbst widerrufen)")
            text = ("✅ Deine Autorisierung wurde widerrufen und alle dafür gespeicherten Daten gelöscht. "
                    "Pulse fügt dich nicht mehr automatisch wieder zum Server hinzu. Deine Rollen bleiben unverändert.")
        else:
            text = "ℹ️ Für dein Konto ist keine Autorisierung gespeichert."
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rejoin(bot))

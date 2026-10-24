"""BOT-08: raid and spam early warning – sends an alert to the team, changes nothing by itself."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

import pulse_db
from pulse_raid import RaidWatch, alert_text


def enabled() -> bool:
    return os.getenv("PULSE_RAID_WATCH", "1").strip() != "0"


class RaidWatchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.watch = RaidWatch()

    def alert_channel(self, guild):
        import webserver
        raw = os.getenv("PULSE_SYSTEM_ALERT_CHANNEL_ID", "").strip()
        channel = guild.get_channel(int(raw)) if raw.isdigit() else None
        return channel or guild.get_channel(int(getattr(webserver, "TEAM_UPDATE_CHANNEL_ID", 0) or 0))

    async def send_alert(self, guild, alert: dict) -> None:
        import webserver
        text = alert_text(alert)
        channel = self.alert_channel(guild)
        if channel is not None:
            try:
                # people are named, nobody is pinged (allowed_mentions none)
                await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                print(f"⚠️ Raid-Frühwarnung konnte nicht gesendet werden: {type(exc).__name__}")
        config = webserver.load_config()
        for member in guild.members:
            if member.bot:
                continue
            perms, _ = webserver.compute_perms(guild, member.id, config)
            if perms.get("can_promote") or perms.get("is_admin"):
                pulse_db.notify(member.id, "🚨 Raid-/Spam-Frühwarnung", alert["text"], "warning", "/system",
                                f"raid:{alert['kind']}", 600)
        webserver.log_audit("Pulse – Frühwarnung", "system", "Raid-/Spam-Frühwarnung", alert["text"])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not enabled() or member.bot:
            return
        age_days = (datetime.now(timezone.utc) - member.created_at).total_seconds() / 86400
        alert = self.watch.member_joined(time.monotonic(), age_days)
        if alert:
            await self.send_alert(member.guild, alert)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not enabled() or message.guild is None or message.author.bot:
            return
        content = message.content or ""
        tried_everyone = ("@everyone" in content or "@here" in content) and not message.channel.permissions_for(message.author).mention_everyone
        mentions = len(message.raw_mentions) + len(message.raw_role_mentions)
        alert = self.watch.message(time.monotonic(), message.author.id, message.channel.id, content, mentions, tried_everyone)
        if alert:
            await self.send_alert(message.guild, alert)


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidWatchCog(bot))

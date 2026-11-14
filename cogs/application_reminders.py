"""Recurring reminders for applicants who still need to arrange their interview."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands, tasks

import pulse_ids as _pulse_ids
from pulse_jobs import tracked


GUILD_ID = _pulse_ids.GUILD_ID
APPLICATION_ROLE_ID = _pulse_ids.APPLICATION_ROLE_ID
APPLICATION_REMINDER_CHANNEL_ID = _pulse_ids.APPLICATION_REMINDER_CHANNEL_ID
REMINDER_INTERVAL_MINUTES = 30
REMINDER_INTERVAL_SECONDS = REMINDER_INTERVAL_MINUTES * 60
REMINDER_TEXT = (
    "⏰ **Erinnerung an alle Bewerber:** Bitte vereinbart zeitnah euren Termin "
    "für das verpflichtende Bewerbungsgespräch mit der BOCHUM-RP-Leitung. "
    "Die Erinnerung wird alle 30 Minuten gesendet, solange eure Bewerberrolle "
    "noch gesetzt ist."
)


async def send_applicant_reminder(bot: commands.Bot) -> None:
    """Ping only the applicant role in its reminder channel.

    The role is the source of truth: once it is removed after the interview
    workflow, the member is no longer included in subsequent role mentions.
    """
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError(f"BOCHUM-RP-Server {GUILD_ID} ist nicht im Cache.")

    role = guild.get_role(APPLICATION_ROLE_ID)
    if role is None:
        raise RuntimeError(f"Bewerberrolle {APPLICATION_ROLE_ID} wurde nicht gefunden.")

    channel = guild.get_channel(APPLICATION_REMINDER_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(APPLICATION_REMINDER_CHANNEL_ID)

    channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
    if channel_guild_id != guild.id:
        raise RuntimeError(
            f"Erinnerungskanal {APPLICATION_REMINDER_CHANNEL_ID} gehört nicht zum BOCHUM-RP-Server."
        )

    send = getattr(channel, "send", None)
    if not callable(send):
        raise RuntimeError(
            f"Erinnerungskanal {APPLICATION_REMINDER_CHANNEL_ID} kann keine Nachrichten empfangen."
        )

    await send(
        content=f"{role.mention}\n{REMINDER_TEXT}",
        allowed_mentions=discord.AllowedMentions(
            roles=[role],
            users=False,
            everyone=False,
        ),
    )


class ApplicationReminderCog(commands.Cog):
    """Posts one role reminder every 30 minutes while the applicant role exists."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.applicant_reminder.start()

    @tasks.loop(minutes=REMINDER_INTERVAL_MINUTES)
    @tracked("Bewerbertermin-Erinnerung (30 min)", REMINDER_INTERVAL_SECONDS)
    async def applicant_reminder(self) -> None:
        await send_applicant_reminder(self.bot)

    @applicant_reminder.before_loop
    async def before_applicant_reminder(self) -> None:
        await self.bot.wait_until_ready()
        # Avoid an immediate role ping on every restart/deployment.
        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)

    def cog_unload(self) -> None:
        self.applicant_reminder.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationReminderCog(bot))

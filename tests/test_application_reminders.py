"""Tests for the recurring applicant interview reminder."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.application_reminders import (
    ApplicationReminderCog,
    send_applicant_reminder,
)
import pulse_ids


class ApplicantReminderTests(unittest.TestCase):
    def setUp(self):
        self.role = SimpleNamespace(
            id=pulse_ids.APPLICATION_ROLE_ID,
            mention=f"<@&{pulse_ids.APPLICATION_ROLE_ID}>",
        )
        self.send = AsyncMock()
        self.channel = SimpleNamespace(send=self.send)
        self.guild = SimpleNamespace(
            id=pulse_ids.GUILD_ID,
            get_role=lambda role_id: self.role if role_id == pulse_ids.APPLICATION_ROLE_ID else None,
            get_channel=lambda channel_id: (
                self.channel if channel_id == pulse_ids.APPLICATION_REMINDER_CHANNEL_ID else None
            ),
        )
        self.channel.guild = self.guild
        self.bot = SimpleNamespace(
            get_guild=lambda guild_id: self.guild if guild_id == pulse_ids.GUILD_ID else None,
            fetch_channel=AsyncMock(return_value=self.channel),
        )

    def test_mentions_only_the_applicant_role_in_the_configured_channel(self):
        asyncio.run(send_applicant_reminder(self.bot))

        self.send.assert_awaited_once()
        kwargs = self.send.await_args.kwargs
        self.assertIn(self.role.mention, kwargs["content"])
        self.assertIn("Bewerbungsgespräch", kwargs["content"])
        self.assertEqual(kwargs["allowed_mentions"].roles, [self.role])
        self.assertIs(kwargs["allowed_mentions"].users, False)
        self.assertIs(kwargs["allowed_mentions"].everyone, False)
        self.bot.fetch_channel.assert_not_awaited()

    def test_fetches_the_reminder_channel_when_not_cached(self):
        self.guild.get_channel = lambda _channel_id: None

        asyncio.run(send_applicant_reminder(self.bot))

        self.bot.fetch_channel.assert_awaited_once_with(pulse_ids.APPLICATION_REMINDER_CHANNEL_ID)
        self.send.assert_awaited_once()

    def test_refuses_a_channel_from_another_server(self):
        self.channel.guild = SimpleNamespace(id=pulse_ids.GUILD_ID + 1)

        with self.assertRaisesRegex(RuntimeError, "gehört nicht zum BOCHUM-RP-Server"):
            asyncio.run(send_applicant_reminder(self.bot))

        self.send.assert_not_awaited()

    def test_missing_applicant_role_does_not_send_a_ping(self):
        self.guild.get_role = lambda _role_id: None

        with self.assertRaisesRegex(RuntimeError, "Bewerberrolle"):
            asyncio.run(send_applicant_reminder(self.bot))

        self.send.assert_not_awaited()

    def test_reminder_loop_uses_a_30_minute_interval(self):
        self.assertEqual(ApplicationReminderCog.applicant_reminder.minutes, 30)


if __name__ == "__main__":
    unittest.main()

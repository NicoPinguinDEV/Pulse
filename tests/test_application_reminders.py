"""Tests for the recurring applicant interview reminder."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_ids
from cogs.application_reminders import (
    ApplicationReminderCog,
    REMINDER_MESSAGE_SETTING_KEY,
    REMINDER_TEXT,
    send_applicant_reminder,
)


class ApplicantReminderTests(unittest.TestCase):
    def setUp(self):
        self.role = SimpleNamespace(
            id=pulse_ids.APPLICATION_ROLE_ID,
            mention=f"<@&{pulse_ids.APPLICATION_ROLE_ID}>",
        )
        self.sent_message = SimpleNamespace(id=987654321)
        self.send = AsyncMock(return_value=self.sent_message)
        self.channel = SimpleNamespace(send=self.send, guild=None)
        self.channel.fetch_message = AsyncMock()
        self.history_messages = []
        self.history_calls = 0

        async def history(limit=100):
            self.history_calls += 1
            self.assertEqual(limit, 100)
            for message in self.history_messages:
                yield message

        self.channel.history = history
        self.guild = SimpleNamespace(
            id=pulse_ids.GUILD_ID,
            get_role=lambda role_id: self.role if role_id == pulse_ids.APPLICATION_ROLE_ID else None,
            get_channel=lambda channel_id: (
                self.channel if channel_id == pulse_ids.APPLICATION_REMINDER_CHANNEL_ID else None
            ),
        )
        self.channel.guild = self.guild
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=123456789),
            get_guild=lambda guild_id: self.guild if guild_id == pulse_ids.GUILD_ID else None,
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        self.get_setting_patch = patch.object(pulse_db, "get_setting", return_value=None)
        self.set_setting_patch = patch.object(pulse_db, "set_setting")
        self.get_setting_mock = self.get_setting_patch.start()
        self.set_setting_mock = self.set_setting_patch.start()
        self.addCleanup(self.get_setting_patch.stop)
        self.addCleanup(self.set_setting_patch.stop)

    def _message(self, *, author_id, content, message_id):
        return SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=author_id),
            content=content,
            delete=AsyncMock(),
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
        self.set_setting_mock.assert_called_once_with(
            REMINDER_MESSAGE_SETTING_KEY,
            self.sent_message.id,
        )

    def test_deletes_only_previous_bot_reminders_before_sending(self):
        reminder_content = f"{self.role.mention}\n{REMINDER_TEXT}"
        previous = self._message(
            author_id=self.bot.user.id,
            content=reminder_content,
            message_id=111,
        )
        unrelated_bot_message = self._message(
            author_id=self.bot.user.id,
            content=f"{reminder_content} (andere Nachricht)",
            message_id=222,
        )
        another_authors_message = self._message(
            author_id=999,
            content=reminder_content,
            message_id=333,
        )
        self.history_messages = [unrelated_bot_message, another_authors_message, previous]

        async def send_after_cleanup(**_kwargs):
            previous.delete.assert_awaited_once()
            unrelated_bot_message.delete.assert_not_awaited()
            another_authors_message.delete.assert_not_awaited()
            return self.sent_message

        self.send.side_effect = send_after_cleanup
        asyncio.run(send_applicant_reminder(self.bot))

        previous.delete.assert_awaited_once()
        unrelated_bot_message.delete.assert_not_awaited()
        another_authors_message.delete.assert_not_awaited()
        self.send.assert_awaited_once()
        self.assertEqual(self.history_calls, 1)

    def test_uses_stored_message_id_without_scanning_history(self):
        previous = self._message(
            author_id=self.bot.user.id,
            content=f"{self.role.mention}\n{REMINDER_TEXT}",
            message_id=444,
        )
        self.get_setting_mock.return_value = previous.id
        self.channel.fetch_message = AsyncMock(return_value=previous)

        asyncio.run(send_applicant_reminder(self.bot))

        self.channel.fetch_message.assert_awaited_once_with(previous.id)
        previous.delete.assert_awaited_once()
        self.assertEqual(self.history_calls, 0)
        self.set_setting_mock.assert_called_once_with(
            REMINDER_MESSAGE_SETTING_KEY,
            self.sent_message.id,
        )

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

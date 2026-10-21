"""BOT-12: citizen ticket reminder DMs respect quiet hours and are bundled persistently."""
import asyncio
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from cogs import tickets


class FakeMember:
    def __init__(self, user_id=42):
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class FakeChannel:
    def __init__(self, channel_id=44, guild_id=2, guild_name="BOCHUM RP"):
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id, name=guild_name)
        self.sent = []

    async def send(self, message, **kwargs):
        self.sent.append((message, kwargs))


class FakeBot:
    def __init__(self, users):
        self.users = users
        self.fetch_calls = []

    def get_user(self, user_id):
        return self.users.get(user_id)

    async def fetch_user(self, user_id):
        self.fetch_calls.append(user_id)
        return self.users[user_id]


class TicketReminderQuietHoursTests(unittest.TestCase):
    def setUp(self):
        self.storage = {tickets.TICKET_REMINDER_QUEUE_KEY: []}

        def get_setting(key, default=None):
            return self.storage.get(key, default)

        def set_setting(key, value):
            self.storage[key] = value

        self.patches = [
            patch.object(tickets, "get_setting", side_effect=get_setting),
            patch.object(tickets, "set_setting", side_effect=set_setting),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_quiet_window_wraps_midnight_and_is_configurable(self):
        with patch.dict(os.environ, {
            "PULSE_TICKET_REMINDER_QUIET_FROM": "23",
            "PULSE_TICKET_REMINDER_QUIET_TO": "8",
        }):
            self.assertTrue(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 21, 23, 15)))
            self.assertTrue(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 22, 7, 59)))
            self.assertFalse(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 22, 8, 0)))
            self.assertFalse(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 22, 12, 0)))

        with patch.dict(os.environ, {
            "PULSE_TICKET_REMINDER_QUIET_FROM": "1",
            "PULSE_TICKET_REMINDER_QUIET_TO": "6",
        }):
            self.assertTrue(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 22, 3, 0)))
            self.assertFalse(tickets.ticket_reminder_quiet_hours(datetime(2026, 10, 22, 7, 0)))

    def test_quiet_hour_reminder_keeps_in_channel_notice_and_queues_dm(self):
        member = FakeMember()
        channel = FakeChannel()
        with patch.object(tickets, "ticket_reminder_quiet_hours", return_value=True):
            queued = asyncio.run(tickets.send_user_reminder(
                channel, {"id": "T-101"}, member, by="Support", auto_close_hours=24
            ))

        self.assertTrue(queued)
        self.assertEqual(len(channel.sent), 1)
        self.assertIn("<@42>", channel.sent[0][0])
        self.assertEqual(member.sent, [])
        queue = self.storage[tickets.TICKET_REMINDER_QUEUE_KEY]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["ticket_id"], "T-101")
        self.assertEqual(queue[0]["user_id"], "42")

    def test_duplicate_pending_ticket_reminder_is_deduplicated(self):
        member = FakeMember()
        channel = FakeChannel()
        with patch.object(tickets, "ticket_reminder_quiet_hours", return_value=True):
            asyncio.run(tickets.send_user_reminder(channel, {"id": "T-102"}, member))
            asyncio.run(tickets.send_user_reminder(channel, {"id": "T-102"}, member))

        self.assertEqual(len(self.storage[tickets.TICKET_REMINDER_QUEUE_KEY]), 1)
        self.assertEqual(len(channel.sent), 2)
        self.assertEqual(member.sent, [])

    def test_pending_reminders_for_same_user_are_bundled_after_quiet_hours(self):
        member = FakeMember()
        self.storage[tickets.TICKET_REMINDER_QUEUE_KEY] = [
            {"user_id": "42", "guild_id": "2", "guild_name": "BOCHUM RP",
             "channel_id": "44", "ticket_id": "T-201", "auto_close_hours": 0},
            {"user_id": "42", "guild_id": "2", "guild_name": "BOCHUM RP",
             "channel_id": "45", "ticket_id": "T-202", "auto_close_hours": 24},
        ]
        bot = FakeBot({42: member})

        with patch.object(tickets, "ticket_reminder_quiet_hours", return_value=False):
            result = asyncio.run(tickets.drain_ticket_reminder_dms(bot))

        self.assertEqual(result, {"sent": 2, "skipped": 0, "pending": 0})
        self.assertEqual(len(member.sent), 1)
        self.assertIn("T-201", member.sent[0])
        self.assertIn("T-202", member.sent[0])
        self.assertIn("<#44>", member.sent[0])
        self.assertIn("<#45>", member.sent[0])
        self.assertEqual(self.storage[tickets.TICKET_REMINDER_QUEUE_KEY], [])

    def test_quiet_hours_keep_persistent_queue_pending(self):
        member = FakeMember()
        self.storage[tickets.TICKET_REMINDER_QUEUE_KEY] = [
            {"user_id": "42", "guild_id": "2", "guild_name": "BOCHUM RP",
             "channel_id": "44", "ticket_id": "T-203", "auto_close_hours": 0},
        ]
        bot = FakeBot({42: member})

        with patch.object(tickets, "ticket_reminder_quiet_hours", return_value=True):
            result = asyncio.run(tickets.drain_ticket_reminder_dms(bot))

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(member.sent, [])
        self.assertEqual(len(self.storage[tickets.TICKET_REMINDER_QUEUE_KEY]), 1)

    def test_direct_reminder_is_unchanged_outside_quiet_hours(self):
        member = FakeMember()
        channel = FakeChannel()
        with patch.object(tickets, "ticket_reminder_quiet_hours", return_value=False):
            queued = asyncio.run(tickets.send_user_reminder(
                channel, {"id": "T-103"}, member, auto_close_hours=12
            ))

        self.assertFalse(queued)
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(len(member.sent), 1)
        self.assertIn("T-103", member.sent[0])
        self.assertIn("12 h", member.sent[0])
        self.assertEqual(self.storage[tickets.TICKET_REMINDER_QUEUE_KEY], [])


if __name__ == "__main__":
    unittest.main()

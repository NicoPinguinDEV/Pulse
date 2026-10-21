"""BOT-12: long-shift reminders must use opt-in notifications, never bypass quiet hours."""
import asyncio
import unittest
from unittest.mock import patch

import webserver as ws
from cogs import pulse_suite


class FakeMember:
    id = 1
    display_name = "Testmitglied"

    def __init__(self):
        self.direct_dm_attempts = 0

    async def send(self, *_args, **_kwargs):
        self.direct_dm_attempts += 1
        raise AssertionError("Long-shift reminder must not bypass the notification queue")


class FakeGuild:
    def __init__(self, member):
        self.member = member

    def get_member(self, member_id):
        return self.member if member_id == self.member.id else None


class LongShiftReminderTests(unittest.TestCase):
    def test_reminder_uses_opt_in_notification_queue(self):
        member = FakeMember()
        guild = FakeGuild(member)
        shifts = {"active_shifts": {"1": {"date": "2026-10-21", "status": "online"}}}
        cog = object.__new__(pulse_suite.PulseSuite)

        with (
            patch.object(ws, "load_shifts", return_value=shifts),
            patch.object(ws, "shift_elapsed", return_value=8 * 3600),
            patch.object(pulse_suite, "managers", return_value=[]),
            patch.object(pulse_suite, "setting", return_value=False),
            patch.object(pulse_suite, "set_setting"),
            patch.object(pulse_suite.db, "notify") as notify,
        ):
            asyncio.run(cog.long_shift_check(guild))

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], member.id)
        self.assertEqual(notify.call_args.args[1], "⏱ Noch im Dienst?")
        self.assertEqual(member.direct_dm_attempts, 0)


if __name__ == "__main__":
    unittest.main()

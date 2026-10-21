"""Behavior tests for the single Discord presence updater (BOT-13)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pulse_presence


class FakeBot:
    def __init__(self):
        self.activities = []

    async def change_presence(self, *, activity):
        self.activities.append(activity.name)


class PresenceTests(unittest.TestCase):
    def test_presence_uses_active_and_break_shifts_and_skips_unchanged_updates(self):
        shifts = {
            "active_shifts": {
                "1": {"status": "online"},
                "2": {"status": "break"},
                "3": {"status": "offline"},
                "4": {},
            }
        }
        fake_webserver = SimpleNamespace(load_shifts=lambda: shifts)
        bot = FakeBot()

        with patch.dict(sys.modules, {"webserver": fake_webserver}), patch(
            "pulse_presence.time.monotonic", side_effect=[1000.0, 1001.0]
        ):
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot)))
            self.assertFalse(asyncio.run(pulse_presence.update_presence(bot)))

        self.assertEqual(bot.activities, ["Pulse · 2 im Dienst"])

    def test_changed_count_waits_until_cooldown_has_elapsed(self):
        shifts = {"active_shifts": {"1": {"status": "online"}}}
        fake_webserver = SimpleNamespace(load_shifts=lambda: shifts)
        bot = FakeBot()

        with patch.dict(sys.modules, {"webserver": fake_webserver}), patch(
            "pulse_presence.time.monotonic", side_effect=[2000.0, 2001.0, 2181.0]
        ):
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot)))
            shifts["active_shifts"]["2"] = {"status": "break"}
            self.assertFalse(asyncio.run(pulse_presence.update_presence(bot)))
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot)))

        self.assertEqual(bot.activities, ["Pulse · 1 im Dienst", "Pulse · 2 im Dienst"])

    def test_invalid_bot_is_ignored(self):
        self.assertFalse(asyncio.run(pulse_presence.update_presence(None)))
        self.assertFalse(asyncio.run(pulse_presence.update_presence(SimpleNamespace())))


if __name__ == "__main__":
    unittest.main()

"""BOT-13: single, throttled Discord presence writer."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_presence


class PresenceTests(unittest.TestCase):
    def test_same_status_is_not_sent_twice(self):
        bot = SimpleNamespace(change_presence=AsyncMock())
        with patch("pulse_presence.time.monotonic", side_effect=[100.0]):
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot, active_count=2, force=True)))
        self.assertFalse(asyncio.run(pulse_presence.update_presence(bot, active_count=2)))
        self.assertEqual(bot.change_presence.await_count, 1)

    def test_changed_status_waits_for_throttle_then_updates(self):
        bot = SimpleNamespace(change_presence=AsyncMock())
        with patch("pulse_presence.time.monotonic", side_effect=[100.0, 120.0, 281.0]):
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot, active_count=1, force=True)))
            self.assertFalse(asyncio.run(pulse_presence.update_presence(bot, active_count=3)))
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot, active_count=3)))
        self.assertEqual(bot.change_presence.await_count, 2)
        sent = bot.change_presence.await_args_list[-1].kwargs["activity"]
        self.assertEqual(sent.name, "Pulse · 3 im Dienst")

    def test_failed_discord_write_does_not_mark_status_as_applied(self):
        bot = SimpleNamespace(change_presence=AsyncMock(side_effect=[RuntimeError("offline"), None]))
        with patch("pulse_presence.time.monotonic", side_effect=[100.0, 101.0]):
            self.assertFalse(asyncio.run(pulse_presence.update_presence(bot, active_count=4, force=True)))
            self.assertTrue(asyncio.run(pulse_presence.update_presence(bot, active_count=4)))
        self.assertEqual(bot.change_presence.await_count, 2)

    def test_presence_api_call_is_centralized(self):
        from pathlib import Path
        import main
        import cogs.pulse_pro as pulse_pro_cog
        import cogs.pulse_suite as pulse_suite_cog

        for module in (main, pulse_pro_cog, pulse_suite_cog):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn(".change_presence(", source, module.__name__)


if __name__ == "__main__":
    unittest.main()

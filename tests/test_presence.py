"""BOT-13: one place sets the bot status – same text, throttled, only on change."""
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_presence as pp
import webserver as ws


class PresenceTests(unittest.TestCase):
    def setUp(self):
        pp._state.update(text=None, at=0.0)
        self.bot = SimpleNamespace(change_presence=AsyncMock())
        self.shifts = {"active_shifts": {"1": {"status": "online"}, "2": {"status": "break"}, "3": {"status": "offline"}}}

    def run_update(self, **kw):
        with patch.object(ws, "load_shifts", lambda: self.shifts), patch.object(pp, "open_tickets", lambda: 4):
            return asyncio.run(pp.update(self.bot, **kw))

    def test_text_counts_like_the_dashboard(self):
        self.assertEqual(pp.on_duty(self.shifts), 2)
        self.assertEqual(pp.presence_text(2, 1), "2 im Dienst · 1 Ticket offen")
        self.assertEqual(pp.presence_text(0, 4), "0 im Dienst · 4 Tickets offen")

    def test_throttled_and_only_on_change(self):
        self.assertTrue(self.run_update(force=True, now=1000))
        self.assertEqual(self.bot.change_presence.await_args.kwargs["activity"].name, "2 im Dienst · 4 Tickets offen")
        self.assertFalse(self.run_update(now=1100))            # within 5 minutes
        self.assertFalse(self.run_update(now=1400))            # old enough, but nothing changed
        self.shifts["active_shifts"]["4"] = {"status": "online"}
        self.assertTrue(self.run_update(now=1800))
        self.assertEqual(self.bot.change_presence.await_count, 2)

    def test_discord_error_does_not_raise(self):
        self.bot.change_presence.side_effect = RuntimeError("boom")
        self.assertFalse(self.run_update(force=True, now=1))

    def test_no_other_place_sets_the_status(self):
        root = Path(pp.__file__).parent
        hits = [str(p.relative_to(root)) for p in [root / "main.py", *root.glob("cogs/*.py"), *root.glob("*.py")]
                if re.search(r"\.change_presence\(", p.read_text(encoding="utf-8"))]
        self.assertEqual(sorted(set(hits)), ["pulse_presence.py"])


if __name__ == "__main__":
    unittest.main()

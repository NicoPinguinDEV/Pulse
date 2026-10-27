"""Regressions from the VPS log review (2026-10-27): repeated 429 on the absence list, loop stalls."""
import asyncio
import threading
import time
import unittest
from datetime import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import pulse_interactions as pi
import pulse_jobs
from cogs import abmeldung


def _frozen_datetime(moment):
    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)
    return Frozen


class AbsenceListPatchTests(unittest.TestCase):
    ROWS = [(1, "Alex", "Urlaub", "01.10.2026", "31.12.2026")]

    def _build(self, moment):
        cog = object.__new__(abmeldung.AbmeldungCog)
        with patch.object(abmeldung, "datetime", _frozen_datetime(moment)):
            return cog._build_embeds(self.ROWS)

    def test_unchanged_list_is_recognised_after_discord_round_trip(self):
        # The 15-minute sync rebuilt the embed with a fresh timestamp, so the "unchanged" check never
        # matched and the same message was PATCHed every run (VPS log: repeated 429 on one message).
        posted = [discord.Embed.from_dict(e.to_dict()) for e in self._build(real_datetime(2026, 10, 26, 11, 43, 39))]
        rebuilt = self._build(real_datetime(2026, 10, 26, 11, 58, 39))
        self.assertEqual(len(posted), len(rebuilt))
        for old, new in zip(posted, rebuilt):
            self.assertEqual(abmeldung._embed_signature(old), abmeldung._embed_signature(new))

    def test_real_content_change_still_patches(self):
        cog = object.__new__(abmeldung.AbmeldungCog)
        before = cog._build_embeds(self.ROWS)[0]
        after = cog._build_embeds(self.ROWS + [(2, "Sam", "Krank", "01.10.2026", "31.12.2026")])[0]
        self.assertNotEqual(abmeldung._embed_signature(before), abmeldung._embed_signature(after))


class UnknownInteractionLogTests(unittest.TestCase):
    def test_expired_interaction_is_logged_briefly_without_reply(self):
        view = pi.PulseView(timeout=None)
        response = SimpleNamespace(status=404, reason="Not Found")
        error = discord.NotFound(response, {"code": 10062, "message": "Unknown interaction"})
        inter = SimpleNamespace(response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
                                followup=SimpleNamespace(send=AsyncMock()))
        with patch("traceback.print_exception") as tb, patch("builtins.print") as out:
            asyncio.run(view.on_error(inter, error, SimpleNamespace(custom_id="x")))
        tb.assert_not_called()
        inter.response.send_message.assert_not_called()
        inter.followup.send.assert_not_called()
        self.assertIn("abgelaufen", out.call_args[0][0])


class LoopStallWatchdogTests(unittest.TestCase):
    def test_blocking_call_is_reported_with_its_stack(self):
        reports = []

        def blocking_work():
            time.sleep(0.6)

        async def main():
            stop = pulse_jobs.start_stall_watchdog(asyncio.get_running_loop(), threshold=0.2, interval=0.05,
                                                   report=reports.append, cooldown=0)
            await asyncio.sleep(0.1)
            blocking_work()
            await asyncio.sleep(0.2)
            stop.set()

        asyncio.run(main())
        self.assertTrue(reports, "stall was not reported")
        self.assertIn("blocking_work", reports[0])
        self.assertEqual(pulse_jobs.snapshot()["lag"]["stalls"] >= 1, True)

    def test_watchdog_is_started_once(self):
        async def main():
            loop = asyncio.get_running_loop()
            first = pulse_jobs.start_stall_watchdog(loop, report=lambda _t: None)
            second = pulse_jobs.start_stall_watchdog(loop, report=lambda _t: None)
            self.assertIs(first, second)
            first.set()
            await asyncio.sleep(0)
        asyncio.run(main())
        self.assertEqual(sum(t.name == "pulse-loop-watchdog" and t.is_alive() for t in threading.enumerate()) <= 1, True)


if __name__ == "__main__":
    unittest.main()

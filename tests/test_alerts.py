"""O-07: throttled system alerts and the job summary in /healthz."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_alerts
import pulse_jobs


class FakeChannel:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    async def send(self, content, allowed_mentions=None):
        if self.fail:
            raise self.fail
        self.sent.append((content, allowed_mentions))


def bot_with(channel):
    return SimpleNamespace(get_channel=lambda _id: channel)


class AlertTests(unittest.TestCase):
    def setUp(self):
        pulse_alerts.reset()
        self.env = patch.dict(os.environ, {"PULSE_SYSTEM_ALERT_CHANNEL_ID": "123"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        pulse_alerts.reset()

    def test_same_key_is_throttled_until_cooldown_passed(self):
        self.assertTrue(pulse_alerts.raise_alert("job:x", "a", now=0))
        self.assertFalse(pulse_alerts.raise_alert("job:x", "b", now=100))
        self.assertTrue(pulse_alerts.raise_alert("job:y", "c", now=100))
        self.assertTrue(pulse_alerts.raise_alert("job:x", "d", now=pulse_alerts.DEFAULT_COOLDOWN_SECONDS + 1))
        self.assertEqual(pulse_alerts.pending(), ["a", "c", "d"])

    def test_queue_and_keys_are_bounded(self):
        for i in range(500):
            pulse_alerts.raise_alert(f"k{i}", "m", now=0)
        self.assertEqual(len(pulse_alerts.pending()), pulse_alerts.MAX_PENDING)
        self.assertLessEqual(len(pulse_alerts._LAST), pulse_alerts.MAX_KEYS)

    def test_flush_sends_without_mentions_and_at_most_five_per_run(self):
        for i in range(7):
            pulse_alerts.raise_alert(f"k{i}", f"m{i}", now=0)
        channel = FakeChannel()
        self.assertEqual(asyncio.run(pulse_alerts.flush(bot_with(channel))), 5)
        self.assertEqual([c for c, _ in channel.sent], ["m0", "m1", "m2", "m3", "m4"])
        self.assertFalse(channel.sent[0][1].everyone)
        self.assertEqual(len(pulse_alerts.pending()), 2)

    def test_http_error_keeps_queue_for_next_minute(self):
        pulse_alerts.raise_alert("k", "m", now=0)
        response = SimpleNamespace(status=429, reason="Too Many Requests")
        channel = FakeChannel(fail=discord.HTTPException(response, "rate limited"))
        self.assertEqual(asyncio.run(pulse_alerts.flush(bot_with(channel))), 0)
        self.assertEqual(pulse_alerts.pending(), ["m"])

    def test_without_channel_queue_is_discarded(self):
        pulse_alerts.raise_alert("k", "m", now=0)
        with patch.dict(os.environ, {"PULSE_SYSTEM_ALERT_CHANNEL_ID": ""}):
            self.assertEqual(asyncio.run(pulse_alerts.flush(bot_with(FakeChannel()))), 0)
        self.assertEqual(pulse_alerts.pending(), [])

    def test_failing_job_raises_one_alert(self):
        async def run():
            for _ in range(3):
                try:
                    async with pulse_jobs.job("O07-Test", 60):
                        raise RuntimeError("kaputt")
                except RuntimeError:
                    pass
        with patch.dict(pulse_jobs._JOBS, {}, clear=True):
            asyncio.run(run())
            summary = pulse_jobs.health_summary()
        self.assertEqual(len(pulse_alerts.pending()), 1)
        self.assertIn("O07-Test", pulse_alerts.pending()[0])
        self.assertEqual(summary["jobs_failing"], ["O07-Test"])

    def test_health_summary_reports_newest_success(self):
        async def ok(name):
            async with pulse_jobs.job(name, 60):
                pass
        with patch.dict(pulse_jobs._JOBS, {}, clear=True):
            asyncio.run(ok("A"))
            summary = pulse_jobs.health_summary()
        self.assertTrue(summary["last_job_ok"])
        self.assertEqual(summary["jobs_failing"], [])


if __name__ == "__main__":
    unittest.main()

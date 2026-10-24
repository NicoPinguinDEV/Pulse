"""O-07: /healthz keeps its status contract (200 only when ready) and adds latency/memory diagnostics."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import webserver as ws


def call(bot):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)))
    response = asyncio.run(ws.healthz(request))
    return response.status_code, json.loads(response.body)


class HealthzTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_ready_bot_reports_latency_and_memory(self):
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _: object(), latency=0.0423)
        with patch("pulse_ultimate.container_memory_mb", return_value=187.5):
            status, body = call(bot)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["gateway_latency_ms"], 42)
        self.assertEqual(body["memory_mb"], 187.5)

    def test_not_connected_stays_503_without_latency(self):
        bot = SimpleNamespace(is_ready=lambda: False, get_guild=lambda _: None, latency=float("nan"))
        status, body = call(bot)
        self.assertEqual(status, 503)
        self.assertIsNone(body["gateway_latency_ms"])

    def test_memory_probe_failure_does_not_break_the_health_check(self):
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _: object(), latency=0.05)
        with patch("pulse_ultimate.container_memory_mb", side_effect=RuntimeError("cgroup")):
            status, body = call(bot)
        self.assertEqual(status, 200)
        self.assertIsNone(body["memory_mb"])

    def test_reports_job_summary_and_alerts_on_database_failure(self):
        import pulse_alerts
        pulse_alerts.reset()
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _: object(), latency=0.05)
        with patch("pulse_jobs.health_summary", return_value={"last_job_ok": "2026-10-23T10:00:00+00:00", "jobs_failing": ["X"]}),              patch.object(pulse_db, "connect", side_effect=RuntimeError("locked")):
            status, body = call(bot)
        self.assertEqual(status, 503)
        self.assertEqual(body["last_job_ok"], "2026-10-23T10:00:00+00:00")
        self.assertEqual(body["jobs_failing"], ["X"])
        self.assertEqual(len(pulse_alerts.pending()), 1)
        pulse_alerts.reset()

    def test_no_bot_yet(self):
        status, body = call(None)
        self.assertEqual(status, 503)
        self.assertFalse(body["bot_ready"])


if __name__ == "__main__":
    unittest.main()

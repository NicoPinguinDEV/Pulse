"""Audit #154/#169/#172: Down-Detector (Zustandslogik, Meldungen), Release-Verlauf, erweiterte Aufbewahrungsmatrix."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_ops as ops

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pulse_watchdog", ROOT / "tools" / "pulse_watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

WEBHOOK = "https://discord.com/api/webhooks/1/abc"


class Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DecideTests(unittest.TestCase):
    def test_reports_the_outage_once_and_the_recovery_once(self):
        state, msgs = {}, []
        for healthy in (False, False, False, False, False, True, True):
            state, msg = wd.decide(state, healthy, "HTTP 503", 3, 1000.0 if not healthy else 1300.0)
            msgs.append(msg)
        self.assertEqual([m is not None for m in msgs], [False, False, True, False, False, True, False])
        self.assertIn("nicht erreichbar", msgs[2])
        self.assertIn("wieder erreichbar", msgs[5])
        self.assertIn("5 Min", msgs[5])

    def test_a_short_blip_stays_silent(self):
        state, msg = wd.decide({}, False, "x", 3, 1.0)
        state, msg2 = wd.decide(state, True, "", 3, 2.0)
        self.assertEqual((msg, msg2), (None, None))
        self.assertEqual(state["failures"], 0)


class CheckAndNotifyTests(unittest.TestCase):
    def test_check_classifies_status_and_errors(self):
        self.assertEqual(wd.check("http://x", lambda r, timeout: Resp(200)), (True, "HTTP 200"))
        self.assertEqual(wd.check("http://x", lambda r, timeout: Resp(204)), (False, "HTTP 204"))

        def boom(r, timeout):
            raise urllib.error.URLError("refused")
        self.assertEqual(wd.check("http://x", boom), (False, "URLError"))

        def http503(r, timeout):
            raise urllib.error.HTTPError("http://x", 503, "x", {}, None)
        self.assertEqual(wd.check("http://x", http503), (False, "HTTP 503"))

    def test_only_discord_webhooks_are_used_and_mentions_are_disabled(self):
        sent = []
        self.assertFalse(wd.notify("http://evil.example/hook", "x", lambda r, timeout: sent.append(r) or Resp()))
        self.assertEqual(sent, [])
        ok = wd.notify(WEBHOOK, "@everyone Test", lambda r, timeout: sent.append(r) or Resp(204))
        self.assertTrue(ok)
        self.assertEqual(json.loads(sent[0].data)["allowed_mentions"], {"parse": []})

    def test_run_end_to_end_with_failed_webhook_retries_next_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PULSE_WATCHDOG_STATE": f"{tmp}/s.json", "PULSE_WATCHDOG_FAILURES": "1", "PULSE_WATCHDOG_WEBHOOK": WEBHOOK}
            calls = []

            def opener(request, timeout):
                url = request.full_url
                calls.append(url)
                if "webhooks" in url:
                    raise urllib.error.URLError("down")
                return Resp(503)
            self.assertEqual(wd.run(env, opener, lambda: 5.0), 1)
            self.assertFalse(json.loads(Path(env["PULSE_WATCHDOG_STATE"]).read_text())["reported_down"])  # noch nicht als gemeldet gespeichert
            self.assertEqual(wd.run(env, opener, lambda: 65.0), 1)
            self.assertEqual(sum("webhooks" in c for c in calls), 2)

    def test_invalid_config_returns_2(self):
        self.assertEqual(wd.run({"PULSE_WATCHDOG_URL": "ftp://x"}), 2)
        self.assertEqual(wd.run({"PULSE_WATCHDOG_FAILURES": "abc"}), 2)


class DbBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patcher = patch.object(pulse_db, "DB_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()


class ReleaseHistoryTests(DbBase):
    def test_records_each_release_once_and_keeps_the_previous_as_rollback_reference(self):
        self.assertTrue(ops.record_release("7.17.0", "a" * 40))
        self.assertFalse(ops.record_release("7.17.0", "a" * 40))
        self.assertTrue(ops.record_release("7.17.1", "b" * 40))
        history = ops.release_history()
        self.assertEqual([h["version"] for h in history], ["7.17.1", "7.17.0"])

    def test_history_is_bounded_and_ignores_empty_input(self):
        self.assertFalse(ops.record_release("", ""))
        for i in range(ops.RELEASE_KEEP + 5):
            ops.record_release(f"1.0.{i}", "c" * 40)
        self.assertEqual(len(ops.release_history()), ops.RELEASE_KEEP)

    def test_broken_stored_value_is_tolerated(self):
        pulse_db.set_setting(ops.RELEASE_KEY, "kaputt")
        self.assertEqual(ops.release_history(), [])
        self.assertTrue(ops.record_release("1", "d"))


class RetentionMatrixTests(DbBase):
    def test_new_data_kinds_are_listed_and_unreadable_ones_do_not_fail(self):
        class WS:
            SHIFTS_FILE, DATA_FILE, APPS_FILE, AUDIT_FILE = "s", "d", "a", "u"
            __file__ = str(Path(self.tmp.name) / "webserver.py")

            @staticmethod
            def load_json(path, default):
                return {"history": [{"started_at_iso": "2026-01-02T10:00:00+00:00"}]} if path == "s" else default
        rows = {r["key"]: r for r in ops.retention_matrix(WS)}
        for key in ("events", "shifts", "transcripts", "team_data"):
            self.assertIn(key, rows)
        self.assertEqual((rows["shifts"]["count"], rows["shifts"]["oldest"]), (1, "2026-01-02"))
        self.assertEqual(rows["transcripts"]["count"], 0)
        self.assertFalse(any(rows[k]["automatic"] for k in ("events", "shifts", "transcripts", "team_data")))


class CockpitExtrasTests(DbBase):
    def test_extras_have_the_documented_shape_and_survive_missing_sources(self):
        extra = ops.cockpit_extras(self.tmp.name, f"{self.tmp.name}/pulse.db")
        self.assertEqual(set(extra), {"jobs", "job_counts", "problem_jobs", "databases", "database_total_mb"})
        self.assertEqual(set(extra["job_counts"]), {"ok", "failing", "overdue", "paused", "waiting"})
        self.assertTrue(any(d["name"] == "pulse.db" for d in extra["databases"]))
        with patch.dict("sys.modules", {"pulse_health": None}):
            broken = ops.cockpit_extras()
        self.assertEqual((broken["jobs"], broken["databases"], broken["database_total_mb"]), ([], [], 0))

    def test_problem_jobs_are_listed_first_and_bounded(self):
        jobs = [{"name": f"j{i}", "runs": 1, "last_start": "2020-01-01T00:00:00+00:00", "last_ok": "2020-01-01T00:00:00+00:00",
                 "last_error_at": "2020-01-02T00:00:00+00:00", "last_error": "boom"} for i in range(15)]
        with patch("pulse_jobs.snapshot", return_value={"jobs": jobs}):
            extra = ops.cockpit_extras(self.tmp.name, f"{self.tmp.name}/pulse.db")
        self.assertEqual(extra["job_counts"]["failing"], 15)
        self.assertEqual(len(extra["problem_jobs"]), 10)


if __name__ == "__main__":
    unittest.main()

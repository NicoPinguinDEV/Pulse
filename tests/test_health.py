import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pulse_health as h  # noqa: E402
import pulse_sqlite  # noqa: E402


def _record(msg, *args):
    return logging.LogRecord("discord.http", logging.WARNING, __file__, 1, msg, args, None)


class RedactTests(unittest.TestCase):
    def test_webhook_and_interaction_tokens_are_removed(self):
        url = "https://discord.com/api/v10/webhooks/123456789012345678/SECRET_TOKEN_abc/messages/@original"
        self.assertEqual(h.redact_route(url), "/webhooks/{id}/***/messages/@original")
        self.assertNotIn("SECRET", h.redact_route("https://discord.com/api/v10/interactions/123456789012345678/SECRET/callback"))

    def test_snowflakes_collapse_and_query_is_dropped(self):
        url = "https://discord.com/api/v10/channels/123456789012345678/messages/223456789012345678?limit=5"
        self.assertEqual(h.redact_route(url), "/channels/{id}/messages/{id}")


class RateLimitRecorderTests(unittest.TestCase):
    def test_counts_bucket_and_global_limits_bounded(self):
        rec = h.RateLimitRecorder(keep=3)
        fmt = "We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds."
        for _ in range(5):
            rec.emit(_record(fmt, "PATCH", "https://discord.com/api/v10/guilds/123456789012345678/members/223456789012345678", 1.5))
        rec.emit(_record("Global rate limit has been hit. Retrying in %.2f seconds.", 3.0))
        rec.emit(_record("unrelated warning %s", "x"))
        snap = rec.snapshot()
        self.assertEqual(snap["total"], 6)
        self.assertEqual(snap["global_total"], 1)
        self.assertEqual(snap["recent"], 3)  # ring buffer keeps the last 3
        self.assertEqual(snap["recent_global"], 1)
        self.assertEqual(snap["top_routes"][0]["route"], "PATCH /guilds/{id}/members/{id}")

    def test_old_events_leave_the_window(self):
        rec = h.RateLimitRecorder()
        r = _record("We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.", "GET", "/x", 1.0)
        r.created = time.time() - h.RATE_WINDOW_SECONDS - 5
        rec.emit(r)
        snap = rec.snapshot()
        self.assertEqual((snap["total"], snap["recent"]), (1, 0))

    def test_malformed_record_never_raises(self):
        rec = h.RateLimitRecorder()
        rec.emit(_record("We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.", "GET"))
        rec.emit(_record("Global rate limit has been hit. Retrying in %.2f seconds.", "nan-ish"))
        self.assertEqual(rec.snapshot()["total"], 0)

    def test_install_is_idempotent(self):
        logger = logging.getLogger("discord.http")
        before = list(logger.handlers)
        try:
            h.install_rate_limit_recorder()
            h.install_rate_limit_recorder()
            self.assertEqual(sum(1 for x in logger.handlers if x is h._RECORDER), 1)
        finally:
            logger.handlers = before


class JobStateTests(unittest.TestCase):
    def iso(self, ago):
        return h._iso(time.time() - ago)

    def test_states(self):
        jobs = [
            {"name": "ok", "interval": 60, "runs": 3, "last_start": self.iso(30), "last_ok": self.iso(29)},
            {"name": "fail", "interval": 60, "runs": 2, "last_start": self.iso(30), "last_ok": self.iso(200), "last_error_at": self.iso(29)},
            {"name": "late", "interval": 60, "runs": 2, "last_start": self.iso(3600), "last_ok": self.iso(3599)},
            {"name": "pause", "interval": 60, "runs": 1, "last_start": self.iso(500), "paused_at": self.iso(10)},
            {"name": "new", "interval": None, "runs": 0, "last_start": ""},
        ]
        states = {j["name"]: j["state"] for j in h.job_states(jobs)}
        self.assertEqual(states, {"ok": "ok", "fail": "failing", "late": "overdue", "pause": "paused", "new": "waiting"})
        self.assertEqual(h.job_states(jobs)[0]["name"], "fail")


class EvaluateTests(unittest.TestCase):
    MEM = {"process_mb": 100.0, "warn_mb": 350.0, "critical_mb": 420.0}

    def test_healthy_has_no_warnings(self):
        self.assertEqual(h.evaluate(self.MEM, [], {"last": 0.0}, {"recent": 0}, {"open": 1}, [], 0.25), [])

    def test_problems_are_reported_worst_first(self):
        checks = h.evaluate({**self.MEM, "process_mb": 430.0}, [{"name": "j", "state": "failing"}], {"last": 0.5},
                            {"recent": 25, "recent_global": 0, "window_minutes": 15}, {"open": 25, "closed_by_gc": 2},
                            [{"name": "pulse.db", "wal_mb": 80.0}], 0.25)
        self.assertEqual(checks[0]["severity"], "bad")
        comps = {c["component"] for c in checks}
        self.assertTrue({"RAM", "Scheduler", "Event-Loop", "Discord", "Datenbank"} <= comps)

    def test_missing_memory_is_info_not_error(self):
        checks = h.evaluate({}, [], {}, {}, {}, [], 0.25)
        self.assertEqual([c["severity"] for c in checks], ["info"])


class DatabaseFilesAndCollectTests(unittest.TestCase):
    def test_sizes_include_wal_and_dedupe_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.db").write_bytes(b"x" * 2048)
            Path(d, "a.db-wal").write_bytes(b"x" * 1024 * 1024)
            Path(d, "notes.txt").write_text("no db")
            files = h.database_files([d, d])
            self.assertEqual([f["name"] for f in files], ["a.db"])
            self.assertEqual(files[0]["wal_mb"], 1.0)

    def test_collect_is_read_only_and_complete(self):
        class Bot:
            def is_ws_ratelimited(self):
                return False

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "pulse.db")
            with pulse_sqlite.connect(db) as c:
                c.execute("CREATE TABLE t(v)")
            before = sorted(os.listdir(d))
            data = h.collect({"process_mb": 90.0, "warn_mb": 350.0, "critical_mb": 420.0}, d, db, Bot())
            self.assertEqual(sorted(os.listdir(d)), before)
        for key in ("status", "checks", "memory", "scheduler", "discord", "connections", "databases", "at"):
            self.assertIn(key, data)
        self.assertIs(data["discord"]["ws_ratelimited"], False)
        self.assertIn("pulse.db", [f["name"] for f in data["databases"]["files"]])


class ConnectionStatsTests(unittest.TestCase):
    def test_open_close_and_gc_leak_are_counted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.db")
            start = pulse_sqlite.connection_stats()
            c = pulse_sqlite.connect(path)
            self.assertEqual(pulse_sqlite.connection_stats()["open"], start["open"] + 1)
            c.close()
            c.close()  # double close counts once
            self.assertEqual(pulse_sqlite.connection_stats()["open"], start["open"])
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                leaked = pulse_sqlite.connect(path)
                del leaked
                import gc
                gc.collect()  # sqlite keeps internal reference cycles; real leaks also end in the GC
            after = pulse_sqlite.connection_stats()
            self.assertEqual(after["open"], start["open"])
            self.assertEqual(after["closed_by_gc"], start["closed_by_gc"] + 1)
            self.assertEqual(after["opened_total"], start["opened_total"] + 2)


if __name__ == "__main__":
    unittest.main()

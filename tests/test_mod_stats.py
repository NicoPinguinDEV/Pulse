"""Audit #24: moderation statistics – guild isolation, period windows, test hits, false positives, rights, empty/broken DB."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_admin
import pulse_db
import pulse_mod_stats as ms
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
GUILD = 111


def iso(dt):
    return dt.isoformat(timespec="seconds")


class StatsDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.n = 0

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def case(self, *, guild=GUILD, target="5", action="warn", status="active", age=timedelta(days=1), closed_after=None,
             dm=1, proof="", template=None, actor=("2", "Mod A")):
        self.n += 1
        created = NOW - age
        with pulse_db.connect() as db:
            db.execute("INSERT INTO mod_cases(id,guild_id,target_id,target_name,action,reason,proof,actor_id,actor_name,created_at,"
                       "status,dm_sent,closed_at,reason_template_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"MOD-{self.n:06d}", str(guild), target, f"P{target}", action, "Grund für Test", proof, actor[0], actor[1],
                        iso(created), status, dm, iso(created + closed_after) if closed_after else None, template))

    def automod(self, *, guild=GUILD, rule="spam", test=False, age=timedelta(days=1), event_type=None):
        self.n += 1
        eid = f"event_{self.n:08d}"
        payload = {"guild_id": guild, "rule": rule, "action": "delete", "simulation": test}
        with pulse_db.connect() as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                       (eid, event_type or ("AutoMod-Testtreffer" if test else "AutoMod-Maßnahme"), "user", "9", "0", "Pulse",
                        json.dumps(payload), iso(NOW - age)))
        return eid

    def false_positive(self, event_id, *, guild=GUILD, rule="spam", after=timedelta(hours=3), age=timedelta(days=1)):
        with pulse_db.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS automod_false_positive_feedback (event_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL,"
                       " target_id TEXT NOT NULL, channel_id TEXT NOT NULL, rule TEXT NOT NULL, action TEXT NOT NULL, mode TEXT NOT NULL,"
                       " reviewer_id TEXT NOT NULL, reviewer_name TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("INSERT INTO automod_false_positive_feedback VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (event_id, str(guild), "9", "1", rule, "delete", "enforce", "2", "Mod A", "kein Verstoß", iso(NOW - age + after)))


class CollectTests(StatsDb):
    def test_period_type_result_and_duration(self):
        self.case(action="warn")
        self.case(action="ban", status="expired", closed_after=timedelta(hours=4), proof="https://beweis.example", template="T1")
        self.case(action="timeout", status="expired", closed_after=timedelta(hours=2), dm=0)
        self.case(action="kick", status="failed")
        self.case(action="warn", age=timedelta(days=40))   # previous period of a 30-day window
        self.case(action="warn", age=timedelta(days=80))   # outside both windows
        stats = ms.collect(GUILD, 30, now=NOW)
        c = stats["cases"]
        self.assertTrue(stats["available"])
        self.assertEqual(c["total"], 4)
        self.assertEqual(dict((k, n) for k, _l, n in c["by_action"]), {"warn": 1, "timeout": 1, "kick": 1, "ban": 1})
        self.assertEqual(dict((k, n) for k, _l, n in c["by_status"]), {"active": 1, "expired": 2, "failed": 1})
        self.assertEqual(c["median_hours_to_close"], 3.0)
        self.assertEqual(c["closed_count"], 2)
        self.assertEqual(c["dm_rate"], 75.0)
        self.assertEqual(c["proof_rate"], 25.0)
        self.assertEqual(c["template_rate"], 25.0)
        self.assertEqual(c["failure_rate"], 25.0)
        self.assertEqual(stats["cases_delta"], 3)  # 4 now vs. 1 in the previous 30 days
        self.assertEqual(len(stats["daily"]), 30)
        self.assertEqual(sum(n for _d, n, _m in stats["daily"]), 4)

    def test_guild_isolation(self):
        self.case(guild=999)
        self.automod(guild=999)
        stats = ms.collect(GUILD, 30, now=NOW)
        self.assertEqual(stats["cases"]["total"], 0)
        self.assertEqual(stats["automod"]["total"], 0)
        self.assertFalse(stats["has_data"])

    def test_repeat_people_and_moderators(self):
        self.case(target="5")
        self.case(target="5", actor=("3", "Mod B"))
        self.case(target="6")
        c = ms.collect(GUILD, 7, now=NOW)["cases"]
        self.assertEqual(c["affected_people"], 2)
        self.assertEqual(c["repeat_people"], 1)
        self.assertEqual(c["moderators"], [("Mod A", 2), ("Mod B", 1)])

    def test_automod_tests_and_false_positives(self):
        real = self.automod(rule="spam")
        self.automod(rule="spam")
        self.automod(rule="invite")
        self.automod(test=True)
        self.automod(event_type="AutoMod-Maßnahme", test=True)   # simulation flag on a real-typed event still counts as a test
        self.false_positive(real, after=timedelta(hours=3))
        self.false_positive("event_unknown")                       # feedback without a matching event is ignored
        a = ms.collect(GUILD, 30, now=NOW)["automod"]
        self.assertEqual(a["total"], 3)
        self.assertEqual(a["tests"], 2)
        self.assertEqual(a["false_positives"], 1)
        self.assertEqual(a["false_positive_rate"], 33.3)
        self.assertEqual(a["median_hours_to_review"], 3.0)
        self.assertEqual(a["by_rule"][0], ("spam", 2, 1))

    def test_no_data_and_rates_are_not_invented(self):
        stats = ms.collect(GUILD, 30, now=NOW)
        self.assertFalse(stats["has_data"])
        self.assertIsNone(stats["cases"]["dm_rate"])
        self.assertIsNone(stats["cases"]["median_hours_to_close"])
        self.assertIsNone(stats["automod"]["false_positive_rate"])

    def test_invalid_period_and_missing_guild(self):
        self.assertEqual(ms.normalize_period("abc"), 30)
        self.assertEqual(ms.normalize_period("365"), 30)
        self.assertEqual(ms.normalize_period("7"), 7)
        self.assertFalse(ms.collect(None)["available"])

    def test_unreadable_database_is_reported(self):
        with patch.object(ms, "_load", side_effect=sqlite3.DatabaseError("broken")):
            stats = ms.collect(GUILD, 30, now=NOW)
        self.assertFalse(stats["available"])
        self.assertIn("nicht lesbar", stats["reason"])
        self.assertEqual(ms.csv_rows(stats)[0][0], "Hinweis")

    def test_corrupt_rows_are_skipped(self):
        with pulse_db.connect() as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                       ("event_bad", "AutoMod-Maßnahme", "user", "9", "0", "Pulse", "{kaputt", iso(NOW - timedelta(days=1))))
        self.case(closed_after=timedelta(hours=-5), status="expired")   # closed before created: no duration
        stats = ms.collect(GUILD, 30, now=NOW)
        self.assertEqual(stats["automod"]["total"], 0)
        self.assertIsNone(stats["cases"]["median_hours_to_close"])

    def test_read_only(self):
        self.case()
        before = pulse_db.connect
        with pulse_db.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM mod_cases").fetchone()[0]
        ms.collect(GUILD, 90, now=NOW)
        with before() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mod_cases").fetchone()[0], count)


class PageTests(StatsDb):
    def test_rights_page_and_csv(self):
        self.case(action="ban", status="expired", closed_after=timedelta(hours=1))
        self.automod(rule="spam")
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "GUILD_ID", GUILD), patch.object(ms, "datetime") as fake_dt:
            fake_dt.now.return_value = NOW
            fake_dt.fromisoformat = datetime.fromisoformat
            with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
                self.assertEqual(client.get("/mod-cases/stats").status_code, 403)
                self.assertEqual(client.get("/mod-cases/stats.csv").status_code, 403)
            with patch.object(ws, "auth", fake_auth_for(ADMIN)):
                page = client.get("/mod-cases/stats", params={"days": "7"})
                self.assertEqual(page.status_code, 200)
                self.assertIn("Moderationsstatistik", page.text)
                self.assertIn("AutoMod nach Regel", page.text)
                self.assertIn('aria-current="page">7 Tage', page.text)
                csv = client.get("/mod-cases/stats.csv", params={"days": "7"}).content.decode("utf-8")
                self.assertIn("Fälle: Ban", csv)
                self.assertIn("AutoMod-Regel: spam", csv)

    def test_empty_state(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "GUILD_ID", GUILD), patch.object(ws, "auth", fake_auth_for(ADMIN)):
            page = client.get("/mod-cases/stats", params={"days": "bogus"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("keine Moderationsfälle", page.text)

    def test_admin_navigation_respects_rights(self):
        keys = lambda perms: [s[0] for s in pulse_admin.admin_sections(perms)]  # noqa: E731
        self.assertIn("mod-stats", keys({"can_moderate_kicks": True}))
        self.assertIn("mod-stats", keys({"is_admin": True}))
        self.assertNotIn("mod-stats", keys({}))


if __name__ == "__main__":
    unittest.main()

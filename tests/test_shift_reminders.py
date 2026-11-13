"""Audit #86: reminders before personal shifts."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import pulse_db
import pulse_shift_reminders as sr
import webserver as ws
from cogs import pulse_suite as suite

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 13, 19, 40, tzinfo=BERLIN)
GUILD = "155555555555555555"


def shift(sid="s1", day="2026-11-13", start="20:00", end="23:00", who="100000000000000001"):
    return {"id": sid, "date": day, "start_time": start, "end_time": end, "assignee_id": who, "shift_name": "Spätdienst"}


class LogicTests(unittest.TestCase):
    def test_only_shifts_starting_inside_the_lead_window_are_due(self):
        rows = [shift("a", start="20:00"), shift("b", start="20:30"), shift("c", start="19:00"), shift("d", start="19:40")]
        due = sr.due_reminders(rows, NOW, 30)
        self.assertEqual([item["shift_id"] for item in due], ["d", "a"])  # b is 50 min away, c already started
        self.assertEqual(due[1]["minutes"], 20)
        self.assertIn("20:00", due[1]["text"])

    def test_shift_after_midnight_and_next_day_are_found(self):
        late = datetime(2026, 11, 13, 23, 45, tzinfo=BERLIN)
        self.assertEqual([i["shift_id"] for i in sr.due_reminders([shift("n", day="2026-11-14", start="00:10")], late, 30)], ["n"])
        self.assertEqual(sr.candidate_dates(late, 30), ["2026-11-13", "2026-11-14"])
        self.assertEqual(sr.candidate_dates(NOW, 30), ["2026-11-13"])

    def test_invalid_rows_and_leads_are_harmless(self):
        rows = [shift("x", start="25:99"), shift("y", who=""), shift("z", who="abc"), {"id": "q"}, None, shift("ok")]
        self.assertEqual([i["shift_id"] for i in sr.due_reminders(rows, NOW)], ["ok"])
        for bad in (None, "x", 0, 4, 99999):
            self.assertEqual(sr.normalize_lead(bad), sr.DEFAULT_LEAD_MINUTES)
        self.assertEqual(sr.normalize_lead("60"), 60)

    def test_dedupe_key_changes_when_the_shift_is_moved_or_lead_changes(self):
        base = sr.due_reminders([shift()], NOW, 30)[0]["dedupe_key"]
        moved = sr.due_reminders([shift(start="20:10")], NOW, 30)[0]["dedupe_key"]
        self.assertNotEqual(base, moved)
        self.assertNotEqual(base, sr.due_reminders([shift()], NOW, 60)[0]["dedupe_key"])
        self.assertEqual(base, sr.due_reminders([shift()], NOW, 30)[0]["dedupe_key"])


class LoopAndRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "suite.db"))]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        suite.register_routes()
        suite.init_db()
        self.today = datetime.now(ws.TZ).date()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def add(self, sid, start, who="100000000000000001", guild=GUILD, day=None):
        with suite.cx() as c:
            c.execute("INSERT INTO suite_schedule VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, guild, (day or self.today).isoformat(), "Früh", start, "23:59", who, "A", "1", "L", suite.iso()))

    def run_step(self, guild_id=GUILD):
        cog = suite.PulseSuite.__new__(suite.PulseSuite)
        asyncio.run(cog.shift_reminders(SimpleNamespace(id=int(guild_id))))

    def inbox(self, user):
        return [n for n in pulse_db.notifications(user, limit=20) if "Schicht" in n["title"]]

    def soon(self, minutes):
        return (datetime.now(ws.TZ) + timedelta(minutes=minutes)).strftime("%H:%M")

    def test_step_notifies_only_the_assignee_once_and_only_for_this_guild(self):
        if (datetime.now(ws.TZ) + timedelta(minutes=20)).date() != self.today:
            self.skipTest("too close to midnight for a same-day fixture")
        self.add("near", self.soon(15))
        self.add("far", self.soon(180)) if (datetime.now(ws.TZ) + timedelta(minutes=180)).date() == self.today else None
        self.add("other", self.soon(10), who="100000000000000002", guild="999")
        self.run_step()
        self.run_step()  # second run must not notify again
        self.assertEqual(len(self.inbox("100000000000000001")), 1)
        self.assertEqual(self.inbox("100000000000000002"), [])

    def test_lead_setting_is_used_and_validated_by_the_route(self):
        ctx = SimpleNamespace(user={"id": "9", "global_name": "L"}, guild=SimpleNamespace(id=int(GUILD), members=[], roles=[]),
                              guild_name="G", member=None, perms={"is_admin": True, "can_view_dashboard": True}, config={}, msg="", msg_ok=True)
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", lambda *a, **k: ctx):
            for bad in ("2", "99999", "abc"):
                self.assertEqual(client.post("/suite/schedule/reminder-lead", data={"minutes": bad}, follow_redirects=False).status_code, 400)
            self.assertEqual(suite.setting("shift_reminder_minutes"), 30)
            self.assertEqual(client.post("/suite/schedule/reminder-lead", data={"minutes": "90"}, follow_redirects=False).status_code, 303)
        self.assertEqual(suite.setting("shift_reminder_minutes"), 90)
        if (datetime.now(ws.TZ) + timedelta(minutes=80)).date() == self.today:
            self.add("wide", self.soon(80))
            self.run_step()
            self.assertEqual(len(self.inbox("100000000000000001")), 1)


if __name__ == "__main__":
    unittest.main()

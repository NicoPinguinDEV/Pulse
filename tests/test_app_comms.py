"""BOT-10: application DMs from templates (unchanged by default), interview date with calendar entry and reminder."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_app_comms as ac
import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class TemplateTests(TempDb):
    def test_defaults_are_the_former_texts(self):
        self.assertEqual(ac.render("rejected", server="Bochum RP"),
                         "Deine Bewerbung bei **Bochum RP** wurde leider abgelehnt. Danke für dein Interesse.")
        self.assertIn("Bewerberolle **Bewerber**", ac.render("interview", server="X", rolle="Bewerber"))
        source = Path(ws.__file__).read_text(encoding="utf-8")
        self.assertNotIn("wurde leider abgelehnt. Danke für dein Interesse.\"", source)  # one place for the text now

    def test_custom_template_and_fallback(self):
        ac.save({"received": "Danke {server}! Nr. {id} {unbekannt}", "hired": "  "})
        self.assertEqual(ac.render("received", server="S", id="app_1"), "Danke S! Nr. app_1 {unbekannt}")
        self.assertEqual(ac.load()["hired"], ac.DEFAULTS["hired"])   # emptied → built-in text
        ac.save({})
        self.assertEqual(ac.load()["received"], ac.DEFAULTS["received"])

    def test_slot_and_reminders(self):
        tz = timezone(timedelta(hours=2))
        with self.assertRaises(ValueError):
            ac.parse_slot("2000-01-01T10:00", tz)
        with self.assertRaises(ValueError):
            ac.parse_slot("kaputt", tz)
        now = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        apps = {"a": {"status": "interview", "interview_at": (now + timedelta(minutes=30)).isoformat()},
                "b": {"status": "interview", "interview_at": (now + timedelta(hours=3)).isoformat()},
                "c": {"status": "interview", "interview_at": (now + timedelta(minutes=10)).isoformat(), "interview_reminded": True},
                "d": {"status": "rejected", "interview_at": (now + timedelta(minutes=10)).isoformat()}}
        self.assertEqual(ac.due_reminders(apps, now), ["a"])


class RouteTests(TempDb):
    def test_schedule_interview_rights_calendar_and_dm(self):
        ws.save_json(ws.APPS_FILE, {"app_1": {"status": "interview", "user_id": "55", "name": "Max"},
                                    "app_2": {"status": "pending", "user_id": "56"}})
        client = TestClient(ws.app, raise_server_exceptions=False)
        future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        teamler = dict(TEAMLER, can_manage_applications=False)
        with patch.object(ws, "auth", fake_auth_for(teamler)):
            self.assertEqual(client.post("/applications/interview-slot", data={"app_id": "app_1", "interview_at": future}).status_code, 403)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(ws, "dm_applicant", AsyncMock(return_value=True)) as dm:
            ok = client.post("/applications/interview-slot", data={"app_id": "app_1", "interview_at": future}, follow_redirects=False)
            self.assertIn("t=ok", ok.headers["location"])
            self.assertEqual(dm.await_args.args[2], "scheduled")
            wrong = client.post("/applications/interview-slot", data={"app_id": "app_2", "interview_at": future}, follow_redirects=False)
            self.assertIn("t=err", wrong.headers["location"])
        item = ws.load_json(ws.APPS_FILE, {})["app_1"]
        self.assertTrue(item["interview_at"].startswith(future))
        self.assertFalse(item["interview_reminded"])
        self.assertEqual(len(pulse_db.list_calendar_events()), 1)

    def test_reminder_is_sent_once(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        ws.save_json(ws.APPS_FILE, {"app_1": {"status": "interview", "user_id": "55", "interview_at": soon, "interview_scheduled_by_id": "3"}})
        user = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(get_guild=lambda gid: None, fetch_user=AsyncMock(return_value=user))
        with patch.object(ws, "send_dm_notification", AsyncMock(return_value=True)) as dm:
            self.assertEqual(asyncio.run(ws.send_interview_reminders(bot)), 1)
            self.assertEqual(asyncio.run(ws.send_interview_reminders(bot)), 0)
        self.assertIn("Erinnerung", dm.await_args.args[1])
        self.assertTrue(any("Bewerbungsgespräch" in n["title"] for n in pulse_db.notifications("3")))

    def test_templates_editor_admin_only(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.post("/applications/templates", data={"tpl_received": "x"}).status_code, 403)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            client.post("/applications/templates", data={"tpl_received": "Hallo {server}"}, follow_redirects=False)
        self.assertEqual(ac.load()["received"], "Hallo {server}")


if __name__ == "__main__":
    unittest.main()

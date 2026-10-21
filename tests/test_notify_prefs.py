"""U-11: notification preferences – muting, quiet hours, opt-in DMs (fake bot), inbox grouping and settings."""
import asyncio
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_notify_prefs as np
import webserver as ws
from tests.test_pulse_admin_area import TEAMLER, fake_auth_for


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        np._dm_queue.clear()

    def tearDown(self):
        np._dm_queue.clear()
        self.db.stop()
        self.tmp.cleanup()


class PreferenceTests(TempDb):
    def test_categories(self):
        self.assertEqual(np.category_for("/tickets"), "tickets")
        self.assertEqual(np.category_for("/suite/applications"), "applications")
        self.assertEqual(np.category_for("/loa"), "absence")
        self.assertEqual(np.category_for("/suite/audit"), np.SYSTEM)

    def test_quiet_hours_wrap_midnight(self):
        prefs = np.normalize({"quiet_from": 22, "quiet_to": 7})
        self.assertTrue(np.in_quiet_hours(prefs, datetime(2026, 1, 1, 23)))
        self.assertTrue(np.in_quiet_hours(prefs, datetime(2026, 1, 1, 3)))
        self.assertFalse(np.in_quiet_hours(prefs, datetime(2026, 1, 1, 12)))
        self.assertFalse(np.in_quiet_hours(np.normalize({"quiet_from": 5, "quiet_to": 5})))

    def test_normalize_rejects_unknown_values(self):
        prefs = np.normalize({"muted": ["tickets", "system", "evil"], "quiet_from": "99", "quiet_to": 3})
        self.assertEqual(prefs["muted"], ["tickets"])
        self.assertIsNone(prefs["quiet_from"])

    def test_default_is_unchanged_behaviour(self):
        pulse_db.notify("1", "Neues Ticket", "x", url="/tickets")
        self.assertIsNone(pulse_db.notifications("1")[0]["read_at"])
        self.assertEqual(np.pending_dms(), 0)

    def test_muted_category_is_kept_but_read_system_never_muted(self):
        np.save("1", {"muted": ["tickets"]})
        pulse_db.notify("1", "Neues Ticket", "x", url="/tickets")
        pulse_db.notify("1", "Sicherheit", "y", url="/suite/audit")
        rows = {r["title"]: r for r in pulse_db.notifications("1")}
        self.assertIsNotNone(rows["Neues Ticket"]["read_at"])  # still there, just not nagging
        self.assertIsNone(rows["Sicherheit"]["read_at"])

    def test_dm_is_opt_in_and_respects_quiet_hours(self):
        np.save("1", {"dm": True})
        pulse_db.notify("1", "Aufgabe", "z", url="/tasks")
        self.assertEqual(np.pending_dms(), 1)
        always_quiet = np.normalize({"dm": True, "quiet_from": 0, "quiet_to": 23})
        self.assertFalse(np.decide("1", "/tasks", prefs=always_quiet, now=datetime(2026, 1, 1, 10))["dm"])


class FakeUser:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send(self, text):
        if self.fail:
            raise self.fail
        self.sent.append(text)


class DrainTests(TempDb):
    def test_sends_batch_skips_closed_dms_and_stops_on_rate_limit(self):
        ok, closed = FakeUser(), FakeUser(discord.Forbidden(SimpleNamespace(status=403, reason="x"), "closed"))
        users = {1: ok, 2: closed}
        bot = SimpleNamespace(get_user=lambda uid: users.get(uid), fetch_user=None)
        np.queue_dm("1", "Titel", "Text", "/tasks")
        np.queue_dm("2", "Titel", "Text", "/tasks")
        result = asyncio.run(np.drain_dms(bot, "https://pulse.example"))
        self.assertEqual((result["sent"], result["skipped"], result["pending"]), (1, 1, 0))
        self.assertIn("https://pulse.example/tasks", ok.sent[0])

        limited = FakeUser(discord.HTTPException(SimpleNamespace(status=429, reason="x"), "slow down"))
        bot = SimpleNamespace(get_user=lambda uid: limited, fetch_user=None)
        np.queue_dm("3", "T", "B", "/x")
        self.assertEqual(asyncio.run(np.drain_dms(bot))["pending"], 1)  # waits for the next run

    def test_queue_is_bounded(self):
        for i in range(np.DM_QUEUE_MAX + 50):
            np.queue_dm(str(i), "t", "b", "/")
        self.assertEqual(np.pending_dms(), np.DM_QUEUE_MAX)


class InboxPageTests(TempDb):
    def test_settings_form_saves_and_inbox_groups(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.cookies.set("user_session", ws.sign_payload({"id": "123", "exp": int(time.time() + 60)}, typ=ws.TOKEN_DASHBOARD))
        pulse_db.notify("123", "Neues Ticket", "a", url="/tickets")
        pulse_db.notify("123", "Abmeldung", "b", url="/loa")
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            page = client.get("/pulse-inbox")
            self.assertEqual(page.status_code, 200)
            self.assertIn('action="/pulse-inbox/settings"', page.text)
            self.assertIn("?cat=tickets", page.text)
            filtered = client.get("/pulse-inbox?cat=absence").text
            self.assertIn("Abmeldung", filtered)
            self.assertNotIn("Neues Ticket", filtered)
            saved = client.post("/pulse-inbox/settings", data={"show": ["absence"], "dm": "1", "quiet_from": "22", "quiet_to": "7"},
                                follow_redirects=False)
            self.assertIn("t=ok", saved.headers["location"])
        prefs = np.load("123")
        self.assertIn("tickets", prefs["muted"])
        self.assertNotIn("absence", prefs["muted"])
        self.assertTrue(prefs["dm"])
        self.assertEqual((prefs["quiet_from"], prefs["quiet_to"]), (22, 7))


if __name__ == "__main__":
    unittest.main()

"""U-11: notification preferences – muting, quiet hours, opt-in DMs (fake bot), inbox grouping and settings."""
import asyncio
import os
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

    def test_global_quiet_window_wraps_midnight_and_is_configurable(self):
        with patch.dict(os.environ, {
            "PULSE_DM_QUIET_FROM": "23",
            "PULSE_DM_QUIET_TO": "8",
        }):
            self.assertTrue(np.global_quiet_hours(datetime(2026, 1, 1, 23, 30)))
            self.assertTrue(np.global_quiet_hours(datetime(2026, 1, 2, 7, 59)))
            self.assertFalse(np.global_quiet_hours(datetime(2026, 1, 2, 8, 0)))
        with patch.dict(os.environ, {
            "PULSE_DM_QUIET_FROM": "1",
            "PULSE_DM_QUIET_TO": "6",
        }):
            self.assertTrue(np.global_quiet_hours(datetime(2026, 1, 2, 3, 0)))
            self.assertFalse(np.global_quiet_hours(datetime(2026, 1, 2, 12, 0)))
        with patch.dict(os.environ, {
            "PULSE_DM_QUIET_FROM": "6",
            "PULSE_DM_QUIET_TO": "6",
        }):
            self.assertFalse(np.global_quiet_hours(datetime(2026, 1, 2, 6, 0)))

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
        with patch.object(np, "global_quiet_hours", return_value=False):
            self.assertEqual(np._delivery_status(
                "1", ("Aufgabe", "z", "/tasks"), prefs=always_quiet
            ), "quiet")


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
        np.save("1", {"dm": True})
        np.save("2", {"dm": True})
        np.queue_dm("1", "Titel", "Text", "/tasks")
        np.queue_dm("2", "Titel", "Text", "/tasks")
        result = asyncio.run(np.drain_dms(bot, "https://pulse.example"))
        self.assertEqual((result["sent"], result["skipped"], result["pending"]), (1, 1, 0))
        self.assertIn("https://pulse.example/tasks", ok.sent[0])

        limited = FakeUser(discord.HTTPException(SimpleNamespace(status=429, reason="x"), "slow down"))
        bot = SimpleNamespace(get_user=lambda uid: limited, fetch_user=None)
        np.save("3", {"dm": True})
        np.queue_dm("3", "T", "B", "/x")
        self.assertEqual(asyncio.run(np.drain_dms(bot))["pending"], 1)  # waits for the next run)

    def test_same_user_notifications_are_bundled(self):
        user = FakeUser()
        bot = SimpleNamespace(get_user=lambda uid: user, fetch_user=None)
        np.save("1", {"dm": True})
        pulse_db.notify("1", "Neues Ticket", "Ein Ticket wartet.", url="/tickets")
        pulse_db.notify("1", "Aufgabe überfällig", "Eine Aufgabe ist fällig.", url="/tasks")

        result = asyncio.run(np.drain_dms(bot, "https://pulse.example"))

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(user.sent), 1)
        self.assertIn("Neues Ticket", user.sent[0])
        self.assertIn("Aufgabe überfällig", user.sent[0])
        self.assertIn("https://pulse.example/tickets", user.sent[0])
        self.assertIn("https://pulse.example/tasks", user.sent[0])
        self.assertEqual(result["pending"], 0)

    def test_global_quiet_hours_defer_opt_in_dms_until_daytime(self):
        user = FakeUser()
        bot = SimpleNamespace(get_user=lambda uid: user, fetch_user=None)
        np.save("1", {"dm": True})
        pulse_db.notify("1", "Noch im Dienst?", "Bitte ausstempeln.", url="/me")
        self.assertEqual(np.pending_dms(), 1)

        with patch.object(np, "global_quiet_hours", return_value=True):
            night_result = asyncio.run(np.drain_dms(bot))
        self.assertEqual(night_result["sent"], 0)
        self.assertEqual(night_result["deferred"], 1)
        self.assertEqual(night_result["pending"], 1)
        self.assertEqual(user.sent, [])

        with patch.object(np, "global_quiet_hours", return_value=False):
            day_result = asyncio.run(np.drain_dms(bot))
        self.assertEqual(day_result["sent"], 1)
        self.assertEqual(day_result["pending"], 0)
        self.assertEqual(len(user.sent), 1)
        self.assertIn("Noch im Dienst?", user.sent[0])

    def test_large_notifications_are_split_without_losing_later_items(self):
        user = FakeUser()
        bot = SimpleNamespace(get_user=lambda uid: user, fetch_user=None)
        np.save("1", {"dm": True})
        first = "A" * 1300
        second = "B" * 1300
        np.queue_dm("1", "Erste Meldung", first, "/tasks")
        np.queue_dm("1", "Zweite Meldung", second, "/tickets")

        result = asyncio.run(np.drain_dms(bot))

        self.assertEqual(result["sent"], 2)
        self.assertEqual(len(user.sent), 2)
        self.assertTrue(any("Erste Meldung" in text and "A" * 100 in text for text in user.sent))
        self.assertTrue(any("Zweite Meldung" in text and "B" * 100 in text for text in user.sent))
        self.assertTrue(all(len(text) <= np.MAX_DM_MESSAGE_CHARS for text in user.sent))
        self.assertEqual(result["pending"], 0)

    def test_queued_dm_is_deferred_if_quiet_hours_start_before_delivery(self):
        user = FakeUser()
        bot = SimpleNamespace(get_user=lambda uid: user, fetch_user=None)
        np.save("1", {"dm": True, "quiet_from": 22, "quiet_to": 7})
        with patch.object(ws, "now_de", return_value=datetime(2026, 1, 1, 12)):
            pulse_db.notify("1", "Aufgabe", "Bitte prüfen.", url="/tasks")
        self.assertEqual(np.pending_dms(), 1)

        with patch.object(ws, "now_de", return_value=datetime(2026, 1, 1, 23)):
            night_result = asyncio.run(np.drain_dms(bot))
        self.assertEqual(night_result["sent"], 0)
        self.assertEqual(night_result["deferred"], 1)
        self.assertEqual(night_result["pending"], 1)
        self.assertEqual(user.sent, [])

        with patch.object(ws, "now_de", return_value=datetime(2026, 1, 2, 12)):
            day_result = asyncio.run(np.drain_dms(bot))
        self.assertEqual(day_result["sent"], 1)
        self.assertEqual(day_result["pending"], 0)
        self.assertEqual(len(user.sent), 1)

    def test_dm_opt_out_after_queue_prevents_delivery(self):
        user = FakeUser()
        bot = SimpleNamespace(get_user=lambda uid: user, fetch_user=None)
        np.save("1", {"dm": True})
        pulse_db.notify("1", "Aufgabe", "Bitte prüfen.", url="/tasks")
        np.save("1", {"dm": False})

        result = asyncio.run(np.drain_dms(bot))

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(user.sent, [])
        self.assertEqual(result["pending"], 0)

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

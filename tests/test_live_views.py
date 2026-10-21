"""U-08: live ticket queue, voice waiting room and team status – visible refresh time, no polling in background tabs."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_pro
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, GUILD


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        waiting = SimpleNamespace(id=55, members=[SimpleNamespace(bot=False), SimpleNamespace(bot=False), SimpleNamespace(bot=True)])
        self.guild = SimpleNamespace(**{**vars(GUILD), "get_channel": lambda cid: waiting if cid == 55 else None})
        self.ctx = SimpleNamespace(user={"id": "123", "global_name": "Tester"}, guild=self.guild, guild_name="G", perms=ADMIN,
                                   config={"team_role_ids": []}, member=None, msg="", msg_ok=True)

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_voice_waiting_counts_people_not_bots(self):
        self.assertEqual(pulse_pro.voice_waiting(self.guild), 0)  # not configured
        pulse_db.set_setting("voice_support_config", {"waiting_room_channel_id": "55"})
        self.assertEqual(pulse_pro.voice_waiting(self.guild), 2)
        self.assertEqual(pulse_pro.voice_waiting(None), 0)        # never raises

    def test_live_endpoint_has_the_new_numbers(self):
        with patch.object(ws, "auth", lambda *a, **k: self.ctx):
            data = TestClient(ws.app, raise_server_exceptions=False).get("/api/pulse/live").json()
        for key in ("open_tickets", "unclaimed_tickets", "voice_waiting", "now"):
            self.assertIn(key, data)

    def test_shared_script_pauses_and_refreshes_live_views_faster(self):
        js = pulse_pro.PRO_JS
        self.assertIn("visibilityState==='hidden'", js)
        self.assertIn("[data-live-view]", js)
        self.assertIn("pulse-live-stale", js)
        # the script sits in <head>: the live-view check must wait for the page (found in the browser check)
        self.assertIn("document.addEventListener('DOMContentLoaded',liveStart)", js)
        self.assertIn(".pulse-live-stale .pulse-live-error{display:inline}", pulse_pro.PRO_CSS)

    def test_team_status_has_no_fixed_interval(self):
        source = Path(pulse_pro.__file__).read_text(encoding="utf-8")
        self.assertNotIn("setInterval(refreshDiscordStatuses", source)  # before: every 10 s, also in background tabs
        self.assertIn('id="statusUpdated"', source)
        self.assertIn('data-live="voice_waiting"', source)

    def test_ticket_center_shows_live_queue(self):
        with patch.object(ws, "auth", lambda *a, **k: self.ctx):
            page = TestClient(ws.app, raise_server_exceptions=False).get("/tickets")
        self.assertEqual(page.status_code, 200)
        self.assertIn("data-live-view", page.text)
        self.assertIn('data-live="unclaimed_tickets"', page.text)
        self.assertIn("ohne Bearbeiter", page.text)


if __name__ == "__main__":
    unittest.main()

"""U-02: a personal start page (default "Mein Pulse") after login and on "/"."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import TEAMLER, fake_auth_for


class StartPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def session(self, uid="123"):
        return ws.sign_payload({"id": uid, "exp": int(time.time() + 60)}, typ=ws.TOKEN_DASHBOARD)

    def test_default_is_mein_pulse_and_choice_is_used(self):
        self.client.cookies.set("user_session", self.session())
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/me")
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            saved = self.client.post("/me/start-page", data={"start_page": "/tickets"}, follow_redirects=False)
            self.assertIn("t=ok", saved.headers["location"])
            bad = self.client.post("/me/start-page", data={"start_page": "https://evil.example"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/tickets")

    def test_unknown_stored_value_falls_back(self):
        pulse_db.set_setting("start_page:9", "//evil.example")
        self.assertEqual(ws.start_page_for("9"), "/me")


if __name__ == "__main__":
    unittest.main()

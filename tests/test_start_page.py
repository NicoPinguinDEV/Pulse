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

# Browsers always send Origin on POST; cookie POSTs without it are refused (pulse_security, audit H1).
BROWSER = {"Origin": "http://testserver"}
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


class StartPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def session(self, uid="123"):
        return ws.sign_payload({"id": uid, "exp": int(time.time() + 60)}, typ=ws.TOKEN_DASHBOARD)

    def test_default_is_mein_pulse_and_choice_is_used_only_after_team_login(self):
        self.client.cookies.set("user_session", self.session())
        self.assertEqual(ws.start_page_for("123"), "/me")
        public_home = self.client.get("/", follow_redirects=False)
        self.assertEqual(public_home.status_code, 303)
        self.assertEqual(public_home.headers["location"], "/me")
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            saved = self.client.post("/me/start-page", data={"start_page": "/tickets"}, follow_redirects=False)
            self.assertIn("t=ok", saved.headers["location"])
            bad = self.client.post("/me/start-page", data={"start_page": "https://evil.example"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
        self.assertEqual(ws.start_page_for("123"), "/tickets")
        # The configured personal start page is entered deliberately through Team-Login,
        # never by opening the public root URL.
        team_login = self.client.get("/team-login", follow_redirects=False)
        self.assertEqual(team_login.status_code, 303)
        self.assertEqual(team_login.headers["location"], "/tickets")
        public_home_after_login = self.client.get("/", follow_redirects=False)
        self.assertEqual(public_home_after_login.status_code, 303)
        self.assertEqual(public_home_after_login.headers["location"], "/tickets")

    def test_unknown_stored_value_falls_back(self):
        pulse_db.set_setting("start_page:9", "//evil.example")
        self.assertEqual(ws.start_page_for("9"), "/me")


    def test_dashboard_favorites_are_private_allowlisted_and_removable(self):
        from types import SimpleNamespace

        user = SimpleNamespace(user={"id": "123", "global_name": "Test"}, perms={}, member=None, config={})
        with patch.object(ws, "auth", return_value=user):
            added = self.client.post(
                "/me/favorites/add", data={"path": "/tickets"}, follow_redirects=False
            )
            self.assertIn("t=ok", added.headers["location"])
            self.assertEqual(pulse_db.get_setting("dashboard_favorites:123"), ["/tickets"])

            duplicate = self.client.post(
                "/me/favorites/add", data={"path": "/tickets"}, follow_redirects=False
            )
            self.assertIn("t=ok", duplicate.headers["location"])
            self.assertEqual(pulse_db.get_setting("dashboard_favorites:123"), ["/tickets"])

            rejected = self.client.post(
                "/me/favorites/add", data={"path": "https://evil.example"}, follow_redirects=False
            )
            self.assertIn("t=err", rejected.headers["location"])
            self.assertEqual(pulse_db.get_setting("dashboard_favorites:123"), ["/tickets"])

            removed = self.client.post(
                "/me/favorites/remove", data={"path": "/tickets"}, follow_redirects=False
            )
            self.assertIn("t=ok", removed.headers["location"])
            self.assertEqual(pulse_db.get_setting("dashboard_favorites:123"), [])

    def test_dashboard_favorites_are_scoped_to_the_logged_in_user(self):
        from types import SimpleNamespace

        first = SimpleNamespace(user={"id": "123", "global_name": "First"}, perms={}, member=None, config={})
        second = SimpleNamespace(user={"id": "456", "global_name": "Second"}, perms={}, member=None, config={})
        with patch.object(ws, "auth", return_value=first):
            self.client.post("/me/favorites/add", data={"path": "/tickets"}, follow_redirects=False)
        with patch.object(ws, "auth", return_value=second):
            self.client.post("/me/favorites/add", data={"path": "/tickets"}, follow_redirects=False)
        self.assertEqual(pulse_db.get_setting("dashboard_favorites:123"), ["/tickets"])
        self.assertEqual(pulse_db.get_setting("dashboard_favorites:456"), ["/tickets"])

    def test_dashboard_favorites_reject_unknown_internal_paths(self):
        from types import SimpleNamespace

        user = SimpleNamespace(user={"id": "123", "global_name": "Test"}, perms={}, member=None, config={})
        with patch.object(ws, "auth", return_value=user):
            response = self.client.post(
                "/me/favorites/add", data={"path": "/admin/console?cmd=wipe"}, follow_redirects=False
            )
        self.assertIn("t=err", response.headers["location"])
        self.assertEqual(pulse_db.get_setting("dashboard_favorites:123", []), [])


if __name__ == "__main__":
    unittest.main()

"""Audit #168: Testserver-Modus (Umgebung, Guild-Schutz, Banner, Testdaten nur im Testmodus und nur einmal)."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_testmode as tm
import webserver as ws

ON = {"PULSE_TEST_MODE": "1", "PULSE_TEST_GUILD_ID": "555"}


class EnvTests(unittest.TestCase):
    def test_inactive_changes_nothing(self):
        self.assertFalse(tm.active({}))
        self.assertTrue(tm.guild_allowed(123, {}))
        self.assertEqual(tm.banner_html(str, {}), "")

    def test_active_allows_only_the_test_guild_and_fails_closed_without_id(self):
        self.assertTrue(tm.guild_allowed(555, ON))
        self.assertTrue(tm.guild_allowed("555", ON))
        self.assertFalse(tm.guild_allowed(1, ON))
        self.assertFalse(tm.guild_allowed(555, {"PULSE_TEST_MODE": "true"}))
        self.assertFalse(tm.guild_allowed(555, {"PULSE_TEST_MODE": "on", "PULSE_TEST_GUILD_ID": "abc"}))
        self.assertIn("Testmodus", tm.banner_html(str, ON))
        self.assertTrue(tm.status({"PULSE_TEST_MODE": "1"})["problems"])
        self.assertEqual(tm.status(ON)["problems"], [])

    def test_off_values(self):
        for value in ("", "0", "false", "no", "nein"):
            self.assertFalse(tm.active({"PULSE_TEST_MODE": value}))


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


class SeedTests(DbBase):
    def test_seed_is_refused_outside_test_mode_and_writes_nothing(self):
        with self.assertRaises(PermissionError):
            tm.seed(pulse_db, {})
        self.assertEqual(pulse_db.list_tasks(limit=50), [])

    def test_seed_creates_marked_data_once(self):
        self.assertEqual(tm.seed(pulse_db, ON), {"created": 4, "already": False})
        self.assertEqual(tm.seed(pulse_db, ON), {"created": 0, "already": True})
        tasks = pulse_db.list_tasks(limit=50)
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(t["title"].startswith(tm.MARKER) for t in tasks))
        self.assertTrue(all(p["title"].startswith(tm.MARKER) for p in pulse_db.wiki_pages()))


class RouteTests(DbBase):
    def _auth(self, admin=True):
        def auth(request, session, perm="can_view_dashboard", admin_only=False, admin=False):
            if admin and not self_admin:
                raise HTTPException(status_code=403, detail="nur Admin")
            return SimpleNamespace(user={"id": "7", "global_name": "Ann"}, guild=SimpleNamespace(id=555), guild_name="G",
                                   perms={"is_admin": self_admin, "can_view_dashboard": True}, config={}, member=None, msg="", msg_ok=True)
        self_admin = admin
        return patch.object(ws, "auth", auth)

    def test_page_for_admins_only_and_seed_requires_test_mode(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self._auth(False):
            self.assertEqual(client.get("/admin/testmodus").status_code, 403)
            self.assertEqual(client.post("/admin/testmodus/daten", follow_redirects=False).status_code, 403)
        with self._auth(True), patch.object(ws, "log_audit"):
            self.assertIn("aus", client.get("/admin/testmodus").text)
            resp = client.post("/admin/testmodus/daten", follow_redirects=False)
            self.assertEqual(resp.status_code, 303)
            self.assertEqual(pulse_db.list_tasks(limit=50), [])           # kein Testmodus -> nichts angelegt
            with patch.dict("os.environ", ON):
                self.assertIn("aktiv", client.get("/admin/testmodus").text)
                client.post("/admin/testmodus/daten", follow_redirects=False)
                self.assertEqual(len(pulse_db.list_tasks(limit=50)), 3)

    def test_dashboard_auth_refuses_a_foreign_guild_in_test_mode(self):
        bot = SimpleNamespace(get_guild=lambda gid: SimpleNamespace(id=999, name="Produktiv"))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)), method="GET", url=SimpleNamespace(path="/x"), query_params={})
        with patch.object(ws, "get_current_user", return_value={"id": "7"}), patch.dict("os.environ", ON):
            with self.assertRaises(HTTPException) as caught:
                ws.auth(request, "cookie")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("Test-Guild", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()

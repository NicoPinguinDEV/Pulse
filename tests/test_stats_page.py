"""/stats must render when nobody has duty time this week yet (e.g. Monday morning)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, GUILD


class EmptyWeekStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_team_without_hours_does_not_crash(self):
        member = SimpleNamespace(id=42, bot=False, display_name="Anna", roles=[SimpleNamespace(id=5)])
        guild = SimpleNamespace(**{**vars(GUILD), "members": [member]})

        def fake_auth(request, session, perm=None, admin=False):
            return SimpleNamespace(user={"id": "123", "global_name": "Tester"}, guild=guild, guild_name=guild.name,
                                   perms=ADMIN, config={"team_role_ids": [5]}, member=None, msg="", msg_ok=True)

        with patch.object(ws, "auth", fake_auth), \
                patch.object(ws, "load_shifts", lambda: {"history": [], "active_shifts": {}}):
            response = TestClient(ws.app, raise_server_exceptions=False).get("/stats")
        self.assertEqual(response.status_code, 200)  # before: ZeroDivisionError → error page
        self.assertIn("Anna", response.text)


if __name__ == "__main__":
    unittest.main()

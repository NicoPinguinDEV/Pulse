"""D-08: visibility of the team file (E-3), privacy notice, retention and data export."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

TARGET = SimpleNamespace(id=555, bot=False, display_name="Ziel", roles=[], avatar=None, nick=None,
                         joined_at=None, status="offline", top_role=None, guild_permissions=SimpleNamespace(administrator=False))


def guild_with_target():
    return SimpleNamespace(id=1, name="G", members=[TARGET], roles=[], channels=[], text_channels=[], member_count=1,
                           owner_id=0, icon=None, get_member=lambda uid: TARGET if uid == TARGET.id else None,
                           get_role=lambda _: None, get_channel=lambda _: None)


class TeamFileVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "log_audit", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws.save_json(ws.DATA_FILE, {"555": {"warns_list": [{"id": "w1", "reason": "Geheimer Warngrund", "active": True,
                                                              "date": "20.10.2026 10:00", "expires_at": "2099-01-01T00:00:00+00:00"}],
                                            "notes": ["Interne Notiz XYZ"]}})
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def page(self, perms, user_id="123"):
        base = fake_auth_for(perms)

        def auth(*a, **k):
            ctx = base(*a, **k)
            ctx.guild = guild_with_target()
            ctx.user = {"id": user_id, "global_name": "Tester"}
            return ctx
        with patch.object(ws, "auth", auth):
            response = self.client.get(f"/member/{TARGET.id}")
        self.assertEqual(response.status_code, 200, response.text[-500:])
        return response.text

    def test_plain_teamler_sees_neither_warnings_nor_notes(self):
        html = self.page(TEAMLER)
        self.assertNotIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)
        self.assertIn("Nur für die Person selbst", html)

    def test_person_sees_own_warnings_but_not_notes(self):
        html = self.page(TEAMLER, user_id=str(TARGET.id))
        self.assertIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)

    def test_warn_and_note_permissions(self):
        html = self.page(dict(TEAMLER, can_warn=True))
        self.assertIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)
        html = self.page(dict(TEAMLER, can_add_notes=True))
        self.assertNotIn("Geheimer Warngrund", html)
        self.assertIn("Interne Notiz XYZ", html)
        html = self.page(ADMIN)
        self.assertIn("Geheimer Warngrund", html)
        self.assertIn("Interne Notiz XYZ", html)


if __name__ == "__main__":
    unittest.main()

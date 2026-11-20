"""Regression tests for server-side validation of team-note targets."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import webserver as ws


class TeamNoteTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.data_path = root / "team_data.json"
        role = SimpleNamespace(id=77, position=1)
        self.members = {}
        self.guild = SimpleNamespace(
            id=1,
            owner_id=0,
            roles=[role],
            get_role=lambda role_id: role if role_id == role.id else None,
            get_member=lambda user_id: self.members.get(int(user_id)),
        )
        self.perms = {"can_add_notes": True, "can_promote": False, "is_admin": False}

        def fake_auth(request, session, perm=None, admin=False):
            if admin or (perm and not self.perms.get(perm)):
                raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
            return SimpleNamespace(
                user={"id": "123", "global_name": "Tester"},
                guild=self.guild,
                config={"team_role_ids": [77]},
                perms=self.perms,
                member=None,
            )

        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
            patch.object(ws, "DATA_FILE", str(self.data_path)),
            patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
            patch.object(ws, "auth", fake_auth),
            patch.object(ws, "log_audit", lambda *args, **kwargs: "audit-test"),
        ]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def post_note(self, target_id):
        return self.client.post(
            "/action",
            data={
                "action": "add_note",
                "user_id": str(target_id),
                "note_text": "Interne Testnotiz",
                "redirect_to_member": "1",
            },
            follow_redirects=False,
        )

    def test_missing_discord_member_cannot_receive_a_team_note(self):
        """A forged user_id must not create an arbitrary team_data.json entry."""
        response = self.post_note(999)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=err", response.headers.get("location", ""))
        self.assertFalse(self.data_path.exists())

    def test_non_team_member_cannot_receive_a_team_note(self):
        """When team roles are configured, an unrelated guild member is not a note target."""
        self.members[555] = SimpleNamespace(id=555, bot=False, roles=[], display_name="Citizen")
        response = self.post_note(555)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=err", response.headers.get("location", ""))
        self.assertFalse(self.data_path.exists())

    def test_configured_team_member_can_receive_a_team_note(self):
        self.members[555] = SimpleNamespace(
            id=555, bot=False, roles=[SimpleNamespace(id=77)], display_name="Teammitglied"
        )
        ws.save_json(self.data_path, {"555": {"notes": ["Alte Notiz"], "custom": "bleibt erhalten"}})
        response = self.post_note(555)
        self.assertEqual(response.status_code, 303)
        saved = ws.load_json(self.data_path, {})
        self.assertEqual(saved["555"]["notes"][0], "Alte Notiz")
        self.assertTrue(any("Interne Testnotiz" in item for item in saved["555"]["notes"]))
        self.assertEqual(saved["555"]["custom"], "bleibt erhalten")


if __name__ == "__main__":
    unittest.main()

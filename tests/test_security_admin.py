"""Admin-panel access, validation, and persistence for AutoMod and anti-nuke settings."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


class SecurityAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.auth_patch = patch.object(ws, "auth", fake_auth_for(ADMIN))
        self.file_patch = patch.object(ws, "CONFIG_FILE", str(self.config_path))
        self.log_patch = patch.object(ws, "log_audit", lambda *args, **kwargs: None)
        self.auth_patch.start()
        self.file_patch.start()
        self.log_patch.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False)
        self._bot = getattr(ws.app.state, "bot", None)

    def tearDown(self):
        ws.app.state.bot = self._bot  # the setup wizard test replaces it – do not leak into other test files
        self.auth_patch.stop()
        self.file_patch.stop()
        self.log_patch.stop()
        self.tmp.cleanup()

    def test_security_page_exposes_mod_controls_and_safety_details(self):
        response = self.client.get("/security")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Spam und Nachrichtenflut", response.text)
        self.assertIn("Massen-Erwähnungen", response.text)
        self.assertIn("Anti-Nuke", response.text)
        self.assertIn("Serverinhaber", response.text)
        self.assertIn("nur kurzzeitig im begrenzten Arbeitsspeicher", response.text)
        self.assertNotIn("Es werden keine Nachrichteninhalte gespeichert", response.text)

    def test_explicitly_empty_rule_lists_stay_empty_in_admin_ui(self):
        ctx = fake_auth_for(ADMIN)(None, None)
        ctx.config = {
            "pulse_automod": {"rules": []},
            "pulse_antinuke": {"events": []},
        }
        with patch.object(ws, "auth", return_value=ctx):
            response = self.client.get("/security")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Regeln: keine", response.text)
        self.assertIn("Überwacht: keine", response.text)
        self.assertIn('name="automod_rules" value="spam">', response.text)  # unchecked
        self.assertIn('name="antinuke_events" value="ban">', response.text)

    def test_team_member_cannot_view_or_change_mod_settings(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(self.client.get("/security").status_code, 403)
            response = self.client.post("/security", data={})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.config_path.exists())

    def test_valid_configuration_is_saved_with_explicit_thresholds(self):
        form = {
            "automod_enabled": "on",
            "automod_rules": ["spam", "mentions", "invites", "keywords", "domains"],
            "mention_limit": "7",
            "blocked_terms": "blocked phrase\nanother phrase",
            "blocked_domains": "bad.example\nsub.bad.example",
            "automod_exempt_role_ids": "123, 456",
            "automod_exempt_channel_ids": "",
            "antinuke_enabled": "on",
            "antinuke_events": ["ban", "role_delete", "channel_update"],
            "action_limit": "4",
            "window_seconds": "15",
            "trusted_role_ids": "789",
            "trusted_user_ids": "111",
            "alert_channel_id": "",
        }
        response = self.client.post("/security", data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["pulse_automod"]["mention_limit"], 7)
        self.assertEqual(saved["pulse_automod"]["blocked_terms"], ["blocked phrase", "another phrase"])
        self.assertEqual(saved["pulse_automod"]["exempt_role_ids"], [123, 456])
        self.assertEqual(saved["pulse_antinuke"]["action_limit"], 4)
        self.assertEqual(saved["pulse_antinuke"]["window_seconds"], 15)
        self.assertEqual(saved["pulse_antinuke"]["trusted_user_ids"], [111])

    def test_invalid_domain_does_not_change_configuration(self):
        response = self.client.post("/security", data={
            "automod_rules": ["domains"],
            "mention_limit": "5",
            "blocked_domains": "https://example.com",
            "action_limit": "3",
            "window_seconds": "10",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=err", response.headers["location"])
        self.assertFalse(self.config_path.exists())

    def test_enabled_protection_requires_at_least_one_rule(self):
        response = self.client.post("/security", data={
            "automod_enabled": "on",
            "mention_limit": "5",
            "action_limit": "3",
            "window_seconds": "10",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=err", response.headers["location"])
        self.assertFalse(self.config_path.exists())

    def test_setup_wizard_is_guided_and_does_not_require_real_bot(self):
        self.client.app.state.bot = SimpleNamespace(is_ready=lambda: True)
        checks = [
            (".env-Pflichtwerte", True, "Werte gesetzt"),
            ("Gateway-Latenz", True, "80 ms"),
            ("Rollenhierarchie", True, "Rollen passen"),
        ]
        with patch("pulse_system_checks.run", return_value=checks), \
                patch.object(ws, "full_backup_status", return_value={
                    "latest": "backup.zip", "count": 1, "total_ok": True,
                    "restore_test": {"ok": True},
                }):
            response = self.client.get("/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Einrichtungsassistent", response.text)
        self.assertIn("AutoMod", response.text)
        self.assertIn("Backup", response.text)
        self.assertIn('role="progressbar"', response.text)
        self.assertIn("ohne Einstellungen automatisch zu verändern", response.text)


if __name__ == "__main__":
    unittest.main()

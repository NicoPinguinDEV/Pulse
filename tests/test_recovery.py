"""Opt-in recovery token storage and role safety tests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_recovery
import webserver as ws


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(ws, "SESSION_KEY", b"p" * 32),
        ]
        for patcher in self.patches:
            patcher.start()
        pulse_db.init_db()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_refresh_tokens_are_encrypted_at_rest_and_can_be_unlinked(self):
        token = "very-secret-refresh-token-for-tests"
        uid = "123456789012345678"
        pulse_recovery.store_refresh_token(uid, token, now=100)
        self.assertEqual(pulse_recovery.get_refresh_token(uid), token)
        with pulse_db.connect() as db:
            raw = db.execute("SELECT refresh_token_enc FROM recovery_oauth_tokens WHERE user_id=?", (uid,)).fetchone()[0]
        self.assertNotIn(token, raw)
        self.assertEqual(pulse_recovery.count_linked_users(), 1)
        self.assertTrue(pulse_recovery.unlink(uid))
        self.assertIsNone(pulse_recovery.get_refresh_token(uid))
        self.assertEqual(pulse_recovery.count_linked_users(), 0)

    def test_invalid_ids_are_not_stored(self):
        with self.assertRaises(ValueError):
            pulse_recovery.store_refresh_token("short", "token")
        settings = pulse_recovery.normalize_settings({"enabled": True, "backup_guild_id": "bad", "citizen_role_id": "1"})
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["backup_guild_id"], "")
        self.assertEqual(settings["citizen_role_id"], "")

    def test_backup_role_with_admin_permission_is_rejected(self):
        role = SimpleNamespace(
            id=123456789012345679, position=5,
            permissions=SimpleNamespace(administrator=True),
        )
        me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True, create_instant_invite=True),
            top_role=SimpleNamespace(position=10),
        )
        guild = SimpleNamespace(
            id=123456789012345678, me=me,
            get_role=lambda _role_id: role,
        )
        bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
        settings = {
            "enabled": True,
            "backup_guild_id": str(guild.id),
            "citizen_role_id": str(role.id),
        }
        _, _, problems = pulse_recovery.target_status(bot, settings)
        self.assertTrue(any("Administrator" in problem or "administrator" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()

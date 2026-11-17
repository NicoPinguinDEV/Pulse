"""AUTH-2: the Recovery OAuth refresh tokens use a dedicated, versioned key (independent of the session secret),
fail closed without it, stay readable while legacy rows exist, and migrate only through an explicit step."""
import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_recovery as pr
import webserver as ws
from nacl.secret import SecretBox

KEY_A = "recovery-key-A-0123456789-abcdefghijklmnop"
KEY_B = "recovery-key-B-9876543210-zyxwvutsrqponmlk"
UID = "123456789012345678"
TOKEN = "refresh-token-value-for-tests"


def legacy_ciphertext(token: str, session_secret: bytes) -> str:
    """What the old implementation stored: key derived from the session secret, no version prefix."""
    box = SecretBox(hashlib.sha256(b"Pulse recovery OAuth token encryption v1\x00" + session_secret).digest())
    return base64.urlsafe_b64encode(bytes(box.encrypt(token.encode()))).decode("ascii")


class RecoveryKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                  patch.object(ws, "SESSION_KEY", b"s" * 32)):
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_A})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PULSE_RECOVERY_KEY_PREVIOUS", None)
        pulse_db.init_db()

    def stored(self):
        with pulse_db.connect() as db:
            return db.execute("SELECT refresh_token_enc FROM recovery_oauth_tokens WHERE user_id=?", (UID,)).fetchone()[0]

    def put_raw(self, value):
        with pulse_db.connect() as db:
            pr._init(db)
            db.execute("INSERT OR REPLACE INTO recovery_oauth_tokens(user_id, refresh_token_enc, consented_at) VALUES (?,?,1)",
                       (UID, value))

    # ---- independence from the session secret ----
    def test_new_tokens_use_the_dedicated_versioned_format(self):
        pr.store_refresh_token(UID, TOKEN, now=1)
        self.assertTrue(self.stored().startswith("v2:"))
        self.assertNotIn(TOKEN, self.stored())
        self.assertEqual(pr.get_refresh_token(UID), TOKEN)

    def test_changing_the_session_secret_does_not_make_new_tokens_unreadable(self):
        pr.store_refresh_token(UID, TOKEN, now=1)
        with patch.object(ws, "SESSION_KEY", b"x" * 32):
            self.assertEqual(pr.get_refresh_token(UID), TOKEN)

    # ---- fail closed ----
    def test_missing_key_refuses_to_store_anything(self):
        with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": ""}):
            with self.assertRaises(pr.RecoveryKeyError):
                pr.store_refresh_token(UID, TOKEN, now=1)
            self.assertFalse(pr.key_configured())
        with pulse_db.connect() as db:
            pr._init(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recovery_oauth_tokens").fetchone()[0], 0)

    def test_weak_or_placeholder_keys_are_not_accepted(self):
        for weak in ("short", "a" * 40, "1234567890" * 3):
            with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": weak}):
                self.assertFalse(pr.key_configured(), weak)
                with self.assertRaises(pr.RecoveryKeyError):
                    pr.store_refresh_token(UID, TOKEN, now=1)

    def test_wrong_key_does_not_decrypt_and_is_reported_not_silent(self):
        pr.store_refresh_token(UID, TOKEN, now=1)
        with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_B}):
            self.assertIsNone(pr.get_refresh_token(UID))
            status = pr.encryption_status()
        self.assertEqual((status["total"], status["unreadable"]), (1, 1))

    # ---- legacy rows stay readable, migration is explicit ----
    def test_legacy_rows_remain_readable_and_are_counted(self):
        self.put_raw(legacy_ciphertext(TOKEN, b"s" * 32))
        self.assertEqual(pr.get_refresh_token(UID), TOKEN)
        self.assertEqual(pr.encryption_status()["legacy"], 1)

    def test_migration_is_dry_run_by_default_and_never_automatic(self):
        legacy = legacy_ciphertext(TOKEN, b"s" * 32)
        self.put_raw(legacy)
        report = pr.migrate_to_current_key()
        self.assertEqual((report["dry_run"], report["would_migrate"], report["migrated"]), (True, 1, 0))
        self.assertEqual(self.stored(), legacy)
        self.assertEqual(pr.get_refresh_token(UID), TOKEN)  # reading does not migrate either

    def test_explicit_migration_reencrypts_verified_and_keeps_the_token(self):
        self.put_raw(legacy_ciphertext(TOKEN, b"s" * 32))
        report = pr.migrate_to_current_key(dry_run=False)
        self.assertEqual(report["migrated"], 1)
        self.assertTrue(self.stored().startswith("v2:"))
        self.assertEqual(pr.get_refresh_token(UID), TOKEN)
        self.assertEqual(pr.migrate_to_current_key(dry_run=False)["migrated"], 0)  # idempotent

    def test_unreadable_rows_are_left_untouched_by_the_migration(self):
        self.put_raw("v2:deadbeef:Zm9v")
        before = self.stored()
        report = pr.migrate_to_current_key(dry_run=False)
        self.assertEqual((report["unreadable"], report["migrated"]), (1, 0))
        self.assertEqual(self.stored(), before)

    def test_migration_needs_the_new_key(self):
        self.put_raw(legacy_ciphertext(TOKEN, b"s" * 32))
        with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": ""}):
            with self.assertRaises(pr.RecoveryKeyError):
                pr.migrate_to_current_key(dry_run=False)

    # ---- rotation ----
    def test_rotation_with_previous_key(self):
        pr.store_refresh_token(UID, TOKEN, now=1)
        with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_B, "PULSE_RECOVERY_KEY_PREVIOUS": KEY_A}):
            self.assertEqual(pr.get_refresh_token(UID), TOKEN)
            self.assertEqual(pr.encryption_status()["old-key"], 1)
            self.assertEqual(pr.migrate_to_current_key(dry_run=False)["migrated"], 1)
            self.assertEqual(pr.encryption_status()["current"], 1)
        with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_B}):  # previous key can be dropped afterwards
            self.assertEqual(pr.get_refresh_token(UID), TOKEN)


class UnlinkUnreadableConsentTests(unittest.IsolatedAsyncioTestCase):
    """A consent that cannot be decrypted (key rotated/missing) can still be withdrawn, with an honest message."""

    async def test_unreadable_consent_is_deleted_and_the_person_is_told(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from cogs.recovery import RecoveryCog
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        with patch.object(pulse_db, "DB_PATH", str(Path(tmp.name) / "pulse.db")), \
                patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_A}):
            pulse_db.init_db()
            pr.store_refresh_token(UID, TOKEN, now=1)
            with patch.dict(os.environ, {"PULSE_RECOVERY_KEY": KEY_B}):
                interaction = SimpleNamespace(user=SimpleNamespace(id=int(UID), display_name="U"),
                                              response=SimpleNamespace(send_message=AsyncMock()))
                cog = RecoveryCog.__new__(RecoveryCog)
                await RecoveryCog.recovery_unlink.callback(cog, interaction)
            self.assertFalse(pr.has_stored_consent(UID))
        self.assertIn("nicht mehr lesbar", interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()

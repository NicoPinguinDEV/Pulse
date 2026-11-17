"""Opt-in recovery token storage and role safety tests."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_recovery
import webserver as ws


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(ws, "SESSION_KEY", b"p" * 32),
            patch.dict(os.environ, {"PULSE_RECOVERY_KEY": "unit-test-recovery-key-0123456789-ABCDEFGH"}),  # AUTH-2
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


    def test_primary_ban_tombstone_deletes_token_and_blocks_stale_refresh_writes(self):
        uid = "123456789012345678"
        pulse_recovery.store_refresh_token(uid, "refresh-before-ban", now=100)
        token, had_consent = pulse_recovery.block_primary_ban(uid, now=200)
        self.assertEqual(token, "refresh-before-ban")
        self.assertTrue(had_consent)
        self.assertFalse(pulse_recovery.has_stored_consent(uid))
        self.assertTrue(pulse_recovery.is_primary_ban_blocked(uid))
        with self.assertRaises(pulse_recovery.RecoveryBlockedError):
            pulse_recovery.store_refresh_token(uid, "stale-in-flight-refresh", now=201)
        self.assertFalse(pulse_recovery.has_stored_consent(uid))

    def test_only_a_new_consent_started_after_the_ban_can_clear_the_tombstone(self):
        uid = "123456789012345678"
        pulse_recovery.block_primary_ban(uid, now=200)
        self.assertFalse(pulse_recovery.clear_primary_ban_block_for_new_consent(uid, consent_started_at=199))
        self.assertTrue(pulse_recovery.is_primary_ban_blocked(uid))
        self.assertTrue(pulse_recovery.clear_primary_ban_block_for_new_consent(uid, consent_started_at=201))
        self.assertFalse(pulse_recovery.is_primary_ban_blocked(uid))

    def test_rejoin_user_rejects_stale_sync_snapshot_for_banned_member(self):
        uid = "123456789012345678"
        pulse_recovery.block_primary_ban(uid, now=200)

        async def run():
            return await pulse_recovery.rejoin_user(
                uid, "stale-refresh-token", bot=object(), client=object(),
            )

        ok, message = asyncio.run(run())
        self.assertFalse(ok)
        self.assertIn("Bann im Hauptserver", message)


    def test_user_unlink_tombstone_prevents_inflight_sync_from_restoring_consent(self):
        uid = "123456789012345678"
        pulse_recovery.store_refresh_token(uid, "refresh-before-unlink", now=100)
        self.assertTrue(pulse_recovery.unlink(uid, now=200))
        self.assertFalse(pulse_recovery.has_stored_consent(uid))
        self.assertEqual(pulse_recovery.recovery_consent_block_reason(uid), "user_revoked")
        with self.assertRaises(pulse_recovery.RecoveryBlockedError):
            pulse_recovery.store_refresh_token(uid, "stale-in-flight-refresh", now=201)
        self.assertFalse(pulse_recovery.has_stored_consent(uid))
        self.assertTrue(pulse_recovery.clear_primary_ban_block_for_new_consent(uid, consent_started_at=202))
        self.assertFalse(pulse_recovery.is_recovery_consent_blocked(uid))

    def test_unlink_tombstone_wins_against_oauth_flow_started_before_withdrawal(self):
        uid = "123456789012345678"
        pulse_recovery.store_refresh_token(uid, "refresh-before-unlink", now=100)
        self.assertTrue(pulse_recovery.unlink(uid, now=200))
        self.assertFalse(pulse_recovery.clear_primary_ban_block_for_new_consent(uid, consent_started_at=199))
        self.assertTrue(pulse_recovery.is_recovery_consent_blocked(uid))

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

    def test_full_recovery_paging_processes_more_than_one_hundred_consents(self):
        for index in range(125):
            pulse_recovery.store_refresh_token(str(100000000000000000 + index), f"refresh-{index}")

        total, cutoff = pulse_recovery.recovery_snapshot()
        self.assertEqual(total, 125)

        cursor = None
        seen = []
        scanned = 0
        while True:
            page, next_cursor, page_scanned, has_more = pulse_recovery.list_linked_users_page(
                after_user_id=cursor, limit=37, consented_before=cutoff
            )
            if page_scanned == 0:
                break
            seen.extend(page)
            scanned += page_scanned
            cursor = next_cursor
            if not has_more:
                break

        self.assertEqual(scanned, 125)
        self.assertEqual(len(seen), 125)
        self.assertEqual(len({person["user_id"] for person in seen}), 125)

    def test_recovery_snapshot_excludes_consents_added_after_start(self):
        for index in range(3):
            pulse_recovery.store_refresh_token(str(100000000000000100 + index), f"initial-{index}")

        total, cutoff = pulse_recovery.recovery_snapshot()
        pulse_recovery.store_refresh_token(
            "100000000000000199", "added-after-start", now=cutoff + 1
        )
        page, _cursor, scanned, _has_more = pulse_recovery.list_linked_users_page(
            after_user_id=None, limit=100, consented_before=cutoff
        )

        self.assertEqual(total, 3)
        self.assertEqual(scanned, 3)
        self.assertEqual(len(page), 3)
        self.assertNotIn("100000000000000199", {person["user_id"] for person in page})


class RecoveryRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_longer_than_five_minutes_is_fully_honored(self):
        class FakeResponse:
            def __init__(self, status_code, retry_after=None):
                self.status_code = status_code
                self.headers = {"Retry-After": str(retry_after or 1)}

            def json(self):
                return {"retry_after": 301} if self.status_code == 429 else {}

        responses = [FakeResponse(429, 301), FakeResponse(200)]

        async def request_factory():
            return responses.pop(0)

        with patch("pulse_recovery.asyncio.sleep", new_callable=AsyncMock) as sleep:
            response = await pulse_recovery._request_with_429_retry(request_factory)

        self.assertEqual(response.status_code, 200)
        sleep.assert_awaited_once_with(301.0)


if __name__ == "__main__":
    unittest.main()

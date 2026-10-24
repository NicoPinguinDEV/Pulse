"""Opt-in recovery token storage and role safety tests."""
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

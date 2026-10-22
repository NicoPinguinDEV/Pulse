"""Regression tests for retryable warning-role synchronization."""
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import webserver
from pulse_time import BERLIN


class FakeMember:
    id = 123456789012345678
    roles = []


class FakeGuild:
    name = "Pulse-Test"

    def __init__(self, member):
        self.member = member
        self.fetch_error = None
        self.fetch_calls = 0

    def get_member(self, user_id):
        return None

    async def fetch_member(self, user_id):
        self.fetch_calls += 1
        if self.fetch_error:
            raise self.fetch_error
        return self.member


def make_database():
    return {
        "123456789012345678": {
            "warns_list": [
                {
                    "id": "warn_already_expired",
                    "reason": "Regressionstest",
                    "date": "16.10.2026 17:30",
                    "duration_days": 7,
                    "expires_at": "2026-10-22T17:30:00+02:00",
                    "active": False,
                    "expired_at": "2026-10-22T18:00:00+02:00",
                    "expired_reason": "Gültigkeitsdauer abgelaufen",
                }
            ]
        }
    }


class WarningExpiryCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 22, 18, 5, tzinfo=BERLIN)
        self.member = FakeMember()
        self.guild = FakeGuild(self.member)
        self.database = make_database()

    def cleanup_patches(self):
        return (
            patch("webserver.load_json", return_value=self.database),
            patch("webserver.save_json") as save_json,
            patch("webserver.now_de", return_value=self.current),
            patch("webserver.load_config", return_value={}),
            patch(
                "webserver.sync_warn_roles",
                new_callable=AsyncMock,
                return_value={"ok": True, "message": "Warnrollen synchronisiert."},
            ) as sync_roles,
            patch(
                "webserver.restore_expired_warn_sanctions",
                new_callable=AsyncMock,
                return_value={"changed": False, "restored": [], "failed": []},
            ),
            patch("webserver.log_audit"),
            patch("webserver.send_dm_notification", new_callable=AsyncMock),
        )

    async def test_cache_miss_fetches_member_and_retries_old_expiry_sync(self):
        patches = self.cleanup_patches()
        with patches[0], patches[1] as save_json, patches[2], patches[3], patches[4] as sync_roles, patches[5], patches[6], patches[7]:
            result = await webserver.cleanup_expired_team_warnings(self.guild)

        warn = self.database["123456789012345678"]["warns_list"][0]
        self.assertEqual(self.guild.fetch_calls, 1)
        self.assertEqual(result["expired"], 0)
        self.assertEqual(result["roles_synced"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertIn("expiry_roles_synced_at", warn)
        sync_roles.assert_awaited_once_with(self.guild, self.member, 0, {})
        save_json.assert_called()

    async def test_temporary_member_fetch_error_is_retried_on_next_cycle(self):
        self.guild.fetch_error = RuntimeError("temporary Discord API error")
        patches = self.cleanup_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4] as sync_roles, patches[5], patches[6], patches[7]:
            first = await webserver.cleanup_expired_team_warnings(self.guild)
            warn = self.database["123456789012345678"]["warns_list"][0]
            self.assertEqual(first["failed"], 1)
            self.assertNotIn("expiry_roles_synced_at", warn)
            sync_roles.assert_not_awaited()

            self.guild.fetch_error = None
            second = await webserver.cleanup_expired_team_warnings(self.guild)

        self.assertEqual(second["roles_synced"], 1)
        self.assertEqual(second["failed"], 0)
        self.assertIn("expiry_roles_synced_at", warn)
        self.assertEqual(self.guild.fetch_calls, 2)

    async def test_confirmed_departed_member_is_skipped_without_retrying_forever(self):
        class MemberNotFound(Exception):
            pass

        self.guild.fetch_error = MemberNotFound("user is not in the guild")
        patches = self.cleanup_patches()
        with patch("webserver.discord.NotFound", MemberNotFound), patches[0], patches[1], patches[2], patches[3], patches[4] as sync_roles, patches[5], patches[6], patches[7]:
            result = await webserver.cleanup_expired_team_warnings(self.guild)

        warn = self.database["123456789012345678"]["warns_list"][0]
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["roles_synced"], 0)
        self.assertEqual(warn["expiry_roles_sync_skip_reason"], "Mitglied nicht mehr auf dem Server")
        sync_roles.assert_not_awaited()

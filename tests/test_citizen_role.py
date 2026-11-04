"""Automatic citizen role assignment on join; all Discord objects are local fakes."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import discord
from cogs.citizen_role import CITIZEN_ROLE_ID, CitizenRoleOnJoin

GUILD_ID = 1474514929351524616

class FakeRole:
    def __init__(self, role_id=CITIZEN_ROLE_ID, position=5):
        self.id = role_id
        self.position = position

class FakeGuild:
    def __init__(self, *, guild_id=GUILD_ID, role=True, manage_roles=True, top=100):
        self.id = guild_id
        self.roles = {CITIZEN_ROLE_ID: FakeRole()} if role else {}
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=manage_roles),
                                  top_role=SimpleNamespace(position=top))
    def get_role(self, role_id):
        return self.roles.get(role_id)

class FakeMember:
    def __init__(self, guild, *, roles=(), bot=False, error=None):
        self.guild = guild
        self.id = 4242
        self.roles = list(roles)
        self.bot = bot
        self.error = error
        self.calls = []
    async def add_roles(self, *roles, reason=None):
        self.calls.append((tuple(role.id for role in roles), reason))
        if self.error:
            raise self.error
        self.roles.extend(roles)

class CitizenRoleJoinTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DISCORD_GUILD_ID": str(GUILD_ID)})
        self.env.start()
        self.cog = CitizenRoleOnJoin(SimpleNamespace())
        self.guild = FakeGuild()
    def tearDown(self):
        self.env.stop()
    def join(self, member):
        return asyncio.run(self.cog.on_member_join(member))
    def test_new_human_gets_citizen_role(self):
        member = FakeMember(self.guild)
        self.assertEqual(self.join(member), "assigned")
        self.assertEqual([role.id for role in member.roles], [CITIZEN_ROLE_ID])
        self.assertEqual(len(member.calls), 1)
        self.assertIn("automatisch beim Serverbeitritt", member.calls[0][1])
    def test_join_assignment_is_not_blocked_by_maintenance_mode(self):
        member = FakeMember(self.guild)
        with patch("pulse_flags.automations_paused", return_value=True):
            self.assertEqual(self.join(member), "assigned")
        self.assertEqual([role.id for role in member.roles], [CITIZEN_ROLE_ID])

    def test_duplicate_join_event_does_not_assign_twice(self):
        member = FakeMember(self.guild)
        self.assertEqual(self.join(member), "assigned")
        self.assertEqual(self.join(member), "duplicate")
        self.assertEqual(len(member.calls), 1)
    def test_member_with_citizen_role_is_left_unchanged(self):
        member = FakeMember(self.guild, roles=[FakeRole()])
        self.assertEqual(self.join(member), "already_has_role")
        self.assertEqual(member.calls, [])
    def test_bot_accounts_are_skipped(self):
        member = FakeMember(self.guild, bot=True)
        self.assertEqual(self.join(member), "bot")
        self.assertEqual(member.calls, [])
    def test_other_guild_is_ignored(self):
        member = FakeMember(FakeGuild(guild_id=GUILD_ID + 1))
        self.assertEqual(self.join(member), "wrong_guild")
        self.assertEqual(member.calls, [])
    def test_missing_role_is_reported_without_crashing(self):
        member = FakeMember(FakeGuild(role=False))
        self.assertEqual(self.join(member), "missing_role")
        self.assertEqual(member.calls, [])
    def test_missing_manage_roles_permission_is_reported(self):
        member = FakeMember(FakeGuild(manage_roles=False))
        self.assertEqual(self.join(member), "missing_permission")
        self.assertEqual(member.calls, [])
    def test_role_must_be_below_the_bot_role(self):
        member = FakeMember(FakeGuild(top=5))
        self.assertEqual(self.join(member), "role_hierarchy")
        self.assertEqual(member.calls, [])
    def test_discord_forbidden_is_handled(self):
        error = discord.Forbidden(SimpleNamespace(status=403, reason="forbidden"), "no")
        member = FakeMember(self.guild, error=error)
        self.assertEqual(self.join(member), "forbidden")
    def test_discord_http_error_is_handled(self):
        error = discord.HTTPException(SimpleNamespace(status=500, reason="error"), "no")
        member = FakeMember(self.guild, error=error)
        self.assertEqual(self.join(member), "http_error")

if __name__ == "__main__":
    unittest.main()

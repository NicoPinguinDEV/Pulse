"""Regression tests for the post-team-kick Discord role reset."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import webserver


class FakeRole:
    def __init__(self, role_id, name, position, *, managed=False, default=False):
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = managed
        self._default = default

    def is_default(self):
        return self._default


class FakeMember:
    def __init__(self, roles, default_role):
        self.roles = list(roles)
        self.default_role = default_role
        self.edit_calls = []
        self.id = 3001
        self.kick = AsyncMock()

    async def edit(self, *, roles, reason):
        self.edit_calls.append({"roles": list(roles), "reason": reason})
        self.roles = [self.default_role, *roles]


class FakeGuild:
    def __init__(self, roles, default_role, *, manage_roles=True, bot_top_position=100):
        self._roles = {role.id: role for role in roles}
        self._roles[default_role.id] = default_role
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=manage_roles),
            top_role=SimpleNamespace(position=bot_top_position),
        )
        self.members = []

    def get_member(self, member_id):
        return next((item for item in self.members if item.id == member_id), None)

    def get_role(self, role_id):
        return self._roles.get(role_id)


class TeamKickRoleResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_team_kick_keeps_exactly_citizen_and_ex_team_roles(self):
        everyone = FakeRole(1, "@everyone", 0, default=True)
        citizen = FakeRole(1531303733311307997, "Bürger", 10)
        ex_team = FakeRole(1489912887949394082, "Ex-Teamler", 11)
        team = FakeRole(2001, "Supporter", 20)
        warn = FakeRole(2002, "Team-Warn 5", 21)
        ping = FakeRole(2003, "Ankündigungen", 5)
        roles = [everyone, citizen, ex_team, team, warn, ping]
        guild = FakeGuild(roles, everyone)
        member = FakeMember([everyone, citizen, ex_team, team, warn, ping], everyone)
        guild.members.append(member)

        result = await webserver.apply_team_kick_role_reset(
            guild, member, {}, "Test Team-Kick"
        )

        self.assertTrue(result["ok"], result["message"])
        self.assertTrue(result["changed"])
        self.assertEqual(
            {role.id for role in member.roles if not role.is_default()},
            {1531303733311307997, 1489912887949394082},
        )
        self.assertEqual(len(member.edit_calls), 1)
        self.assertIs(guild.get_member(member.id), member)  # still a guild member
        member.kick.assert_not_awaited()
        self.assertIn(2001, result["removed_role_ids"])
        self.assertIn(2002, result["removed_role_ids"])
        self.assertIn(2003, result["removed_role_ids"])

    async def test_unmanageable_role_blocks_the_reset_without_partial_changes(self):
        everyone = FakeRole(1, "@everyone", 0, default=True)
        citizen = FakeRole(1531303733311307997, "Bürger", 10)
        ex_team = FakeRole(1489912887949394082, "Ex-Teamler", 11)
        team = FakeRole(2001, "Supporter", 20)
        managed = FakeRole(2002, "Integration", 30, managed=True)
        guild = FakeGuild([citizen, ex_team, team, managed], everyone)
        member = FakeMember([everyone, citizen, ex_team, team, managed], everyone)
        before = list(member.roles)

        result = await webserver.apply_team_kick_role_reset(
            guild, member, {}, "Test Team-Kick"
        )

        self.assertFalse(result["ok"])
        self.assertIn("Integration", result["message"])
        self.assertEqual(member.roles, before)
        self.assertEqual(member.edit_calls, [])


    async def test_missing_required_role_aborts_without_any_role_change(self):
        everyone = FakeRole(1, "@everyone", 0, default=True)
        citizen = FakeRole(1531303733311307997, "Bürger", 10)
        team = FakeRole(2001, "Supporter", 20)
        guild = FakeGuild([citizen, team], everyone)
        member = FakeMember([everyone, citizen, team], everyone)
        before = list(member.roles)

        result = await webserver.apply_team_kick_role_reset(guild, member, {}, "Test Team-Kick")

        self.assertFalse(result["ok"])
        self.assertIn("nicht gefunden", result["message"])
        self.assertEqual(member.roles, before)
        self.assertEqual(member.edit_calls, [])
        member.kick.assert_not_awaited()

    async def test_missing_manage_roles_aborts_without_any_role_change(self):
        everyone = FakeRole(1, "@everyone", 0, default=True)
        citizen = FakeRole(1531303733311307997, "Bürger", 10)
        ex_team = FakeRole(1489912887949394082, "Ex-Teamler", 11)
        team = FakeRole(2001, "Supporter", 20)
        guild = FakeGuild([citizen, ex_team, team], everyone, manage_roles=False)
        member = FakeMember([everyone, citizen, ex_team, team], everyone)
        before = list(member.roles)

        result = await webserver.apply_team_kick_role_reset(guild, member, {}, "Test Team-Kick")

        self.assertFalse(result["ok"])
        self.assertIn("Rollen verwalten", result["message"])
        self.assertEqual(member.roles, before)
        self.assertEqual(member.edit_calls, [])
        member.kick.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()

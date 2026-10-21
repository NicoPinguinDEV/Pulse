"""BOT-05: both warning-removal flows share temporary-role rollback rules."""
import asyncio
import unittest
from types import SimpleNamespace

from pulse_warning_consequences import revoke_temporary_warning_roles


class FakeMember:
    def __init__(self, roles, *, fail=False):
        self.roles = list(roles)
        self.fail = fail
        self.removed = []

    async def remove_roles(self, role, *, reason):
        if self.fail:
            raise RuntimeError("Discord role removal failed")
        self.removed.append((role, reason))
        self.roles.remove(role)


class WarningConsequenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.role = SimpleNamespace(id=10, position=2)
        self.guild = SimpleNamespace(
            get_role=lambda role_id: self.role if role_id == self.role.id else None,
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
                top_role=SimpleNamespace(position=10),
            ),
        )

    @staticmethod
    def is_expired(warning):
        return bool(warning.get("expired"))

    async def test_last_active_warning_removes_its_temporary_role(self):
        warning = {"id": "w1", "active": False, "revoked_at": "now",
                   "consequences": {"temporary_roles": [{"role_id": 10}]}}
        entry = {"warns_list": [warning]}
        member = FakeMember([self.role])
        ok = await revoke_temporary_warning_roles(
            self.guild, member, warning, entry, "Rücknahme", self.is_expired,
        )
        self.assertTrue(ok)
        self.assertEqual(member.roles, [])
        self.assertEqual(warning["consequences"]["temporary_roles"], [])

    async def test_other_active_warning_keeps_restriction_metadata_and_role(self):
        warning = {"id": "w1", "active": False, "revoked_at": "now",
                   "consequences": {"temporary_roles": [{"role_id": 10}]}}
        other = {"id": "w2", "active": True, "revoked_at": None}
        entry = {"warns_list": [warning, other]}
        member = FakeMember([self.role])
        ok = await revoke_temporary_warning_roles(
            self.guild, member, warning, entry, "Rücknahme", self.is_expired,
        )
        self.assertTrue(ok)
        self.assertEqual(member.roles, [self.role])
        self.assertEqual(warning["consequences"]["temporary_roles"], [{"role_id": 10}])

    async def test_failed_role_removal_keeps_all_metadata_for_retry(self):
        restrictions = [{"role_id": 10}, {"role_id": 999}]
        warning = {"id": "w1", "active": False, "revoked_at": "now",
                   "consequences": {"temporary_roles": restrictions[:]}}
        entry = {"warns_list": [warning]}
        member = FakeMember([self.role], fail=True)
        ok = await revoke_temporary_warning_roles(
            self.guild, member, warning, entry, "Rücknahme", self.is_expired,
        )
        self.assertFalse(ok)
        self.assertEqual(warning["consequences"]["temporary_roles"], restrictions)
        self.assertEqual(member.roles, [self.role])


if __name__ == "__main__":
    unittest.main()

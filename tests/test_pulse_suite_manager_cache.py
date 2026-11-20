"""Regression tests for reusing one permission/config snapshot during manager lookup."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cogs.pulse_suite import managers, members


class SuiteManagerCacheTests(unittest.TestCase):
    def setUp(self):
        self.team_role = SimpleNamespace(id=10)
        self.other_role = SimpleNamespace(id=20)
        self.promoted = SimpleNamespace(id=1, bot=False, roles=[self.team_role])
        self.member = SimpleNamespace(id=2, bot=False, roles=[self.team_role])
        self.outsider = SimpleNamespace(id=3, bot=False, roles=[self.other_role])
        self.bot = SimpleNamespace(id=4, bot=True, roles=[self.team_role])
        self.guild = SimpleNamespace(members=[self.promoted, self.member, self.outsider, self.bot])
        self.config = {"team_role_ids": ["10"]}
        self.web = SimpleNamespace(
            load_config=Mock(return_value=self.config),
            compute_perms=Mock(
                side_effect=lambda _guild, user_id, _config: (
                    {"can_promote": user_id == self.promoted.id, "is_admin": False},
                    {},
                )
            ),
        )

    def test_manager_lookup_loads_config_once_for_all_team_members(self):
        self.assertEqual(managers(self.guild, self.web), [self.promoted])
        self.web.load_config.assert_called_once_with()
        self.assertEqual(self.web.compute_perms.call_count, 2)
        for call in self.web.compute_perms.call_args_list:
            self.assertIs(call.args[2], self.config)

    def test_members_can_reuse_a_caller_supplied_config_snapshot(self):
        self.assertEqual(members(self.guild, self.web, config=self.config), [self.promoted, self.member])
        self.web.load_config.assert_not_called()

    def test_members_keep_the_existing_two_argument_api(self):
        self.assertEqual(members(self.guild, self.web), [self.promoted, self.member])
        self.web.load_config.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

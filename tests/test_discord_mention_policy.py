"""BOT-01: the default mention policy denies broad role/everyone pings."""
import unittest
from types import SimpleNamespace

import pulse_discord_policy


class MentionPolicyTests(unittest.TestCase):
    def test_bot_default_blocks_everyone_and_role_mentions(self):
        policy = pulse_discord_policy.default_allowed_mentions()
        self.assertIs(policy.everyone, False)
        self.assertIs(policy.roles, False)
        self.assertIs(policy.replied_user, False)
        # Do not silently break existing direct member mentions; individual messages
        # that handle user-generated text can still opt out explicitly.
        self.assertIs(policy.users, True)

    def test_configured_role_ping_is_limited_to_that_role(self):
        role = SimpleNamespace(id=12345)
        policy = pulse_discord_policy.role_ping_allowed(role)
        self.assertIs(policy.everyone, False)
        self.assertIs(policy.users, False)
        self.assertEqual(policy.roles, [role])

    def test_missing_role_means_no_role_ping(self):
        policy = pulse_discord_policy.role_ping_allowed(None)
        self.assertIs(policy.everyone, False)
        self.assertIs(policy.users, False)
        self.assertIs(policy.roles, False)


if __name__ == "__main__":
    unittest.main()

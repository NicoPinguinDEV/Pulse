"""Regression tests for the /team-ping authorization and anti-spam controls."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cogs.pulse_suite as suite
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class FakeMember:
    def __init__(self, roles=()):
        self.id = 123
        self.roles = list(roles)


class TeamPingAuthorizationTests(unittest.TestCase):
    def _allowed(self, permissions, *, role_ids=()):
        member = FakeMember([SimpleNamespace(id=value) for value in role_ids])
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=member)
        web = SimpleNamespace(
            compute_perms=lambda guild, user_id, config: (permissions, {}),
            load_config=lambda: {},
        )
        with patch.object(suite.discord, "Member", FakeMember), patch.object(
            suite, "setting", side_effect=lambda _key, default=None: default
        ):
            return suite.allowed(interaction, "team-ping", web)

    def test_dashboard_access_alone_cannot_send_team_wide_pings(self):
        self.assertFalse(self._allowed({"can_view_dashboard": True}))

    def test_leadership_can_send_team_wide_pings(self):
        self.assertTrue(self._allowed({"can_view_dashboard": True, "can_promote": True}))

    def test_discord_administrator_can_send_team_wide_pings(self):
        self.assertTrue(self._allowed({"is_admin": True}))

    def test_explicit_command_role_override_remains_supported(self):
        member = FakeMember([SimpleNamespace(id=987)])
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=member)
        web = SimpleNamespace(
            compute_perms=lambda guild, user_id, config: ({"can_view_dashboard": True}, {}),
            load_config=lambda: {},
        )

        def setting(key, default=None):
            return {"team-ping": ["987"]} if key == "command_roles" else default

        with patch.object(suite.discord, "Member", FakeMember), patch.object(suite, "setting", side_effect=setting):
            self.assertTrue(suite.allowed(interaction, "team-ping", web))


class TeamPingCooldownTests(unittest.TestCase):
    def test_team_ping_has_a_per_user_and_per_guild_cooldown(self):
        source = Path(suite.__file__).read_text(encoding="utf-8")
        module = ast.parse(source)
        function = next(
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "team_ping"
        )
        cooldown = next(
            decorator
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
            and ast.unparse(decorator.func) == "app_commands.checks.cooldown"
        )
        self.assertEqual([ast.literal_eval(value) for value in cooldown.args], [1, 60.0])
        key = next(keyword.value for keyword in cooldown.keywords if keyword.arg == "key")
        self.assertIsInstance(key, ast.Lambda)
        key_expression = ast.unparse(key.body)
        self.assertIn("interaction.guild_id", key_expression)
        self.assertIn("interaction.user.id", key_expression)


if __name__ == "__main__":
    unittest.main()

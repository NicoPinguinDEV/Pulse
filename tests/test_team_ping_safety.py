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
    def test_team_ping_uses_only_the_shared_rate_limiter(self):
        source = Path(suite.__file__).read_text(encoding="utf-8")
        module = ast.parse(source)
        function = next(
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "team_ping"
        )
        decorators = [
            ast.unparse(decorator)
            for decorator in function.decorator_list
        ]
        self.assertFalse(any("app_commands.checks.cooldown" in item for item in decorators))

        rate_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "web.rate_limited"
        ]
        self.assertEqual(len(rate_calls), 1)
        call = rate_calls[0]
        self.assertEqual(
            {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords},
            {"limit": 1, "window": 60},
        )
        key = call.args[0]
        self.assertIn("i.guild.id", ast.unparse(key))
        self.assertIn("i.user.id", ast.unparse(key))


if __name__ == "__main__":
    unittest.main()

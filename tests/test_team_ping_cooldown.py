"""Regression tests for the /team-ping per-user cooldown."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.pulse_suite import PulseSuite


class TeamPingCooldownTests(unittest.TestCase):
    def interaction(self):
        return SimpleNamespace(
            guild=SimpleNamespace(id=42),
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def invoke(self, interaction):
        return asyncio.run(PulseSuite.team_ping.callback(None, interaction, "all", None))

    def test_rate_limited_team_member_is_stopped_before_member_lookup(self):
        interaction = self.interaction()
        with (
            patch("cogs.pulse_suite.allowed", return_value=True),
            patch("webserver.rate_limited", return_value=True) as rate_limited,
            patch("cogs.pulse_suite.members") as members,
        ):
            self.invoke(interaction)

        rate_limited.assert_called_once_with("team-ping:42:7", limit=1, window=60)
        members.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        _args, kwargs = interaction.response.send_message.await_args
        self.assertTrue(kwargs.get("ephemeral"))

    def test_first_team_ping_still_sends_the_normal_announcement(self):
        interaction = self.interaction()
        target = SimpleNamespace(mention="<@9>")
        with (
            patch("cogs.pulse_suite.allowed", return_value=True),
            patch("webserver.rate_limited", return_value=False) as rate_limited,
            patch("cogs.pulse_suite.members", return_value=[target]) as members,
        ):
            self.invoke(interaction)

        rate_limited.assert_called_once_with("team-ping:42:7", limit=1, window=60)
        members.assert_called_once()
        interaction.response.send_message.assert_awaited_once_with("📣 <@9>")


if __name__ == "__main__":
    unittest.main()
